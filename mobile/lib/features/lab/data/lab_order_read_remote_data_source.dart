import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/lab_order_read.dart';

class LabReadSessionUnavailable implements Exception {
  const LabReadSessionUnavailable();
}

// P23H.S04A: read-only, online-only, no PHI cache, no automatic retries.
// Emergency access requires its own separate authorization contract.
class LabOrderReadRemoteDataSource {
  const LabOrderReadRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _readerRoles = <String>{
    'admin',
    'doctor',
    'nurse',
    'receptionist',
    'lab_technician',
  };

  bool _validRevision(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _requireSession(int revision) async {
    if (!_validRevision(revision)) {
      throw const LabReadSessionUnavailable();
    }
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.trim().isEmpty ||
          session.accessToken != session.accessToken.trim() ||
          !_readerRoles.contains(session.role.trim().toLowerCase()) ||
          !_validRevision(revision)) {
        throw const LabReadSessionUnavailable();
      }
      return session;
    } on LabReadSessionUnavailable {
      rethrow;
    } on Object {
      throw const LabReadSessionUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<Map<String, dynamic>> _get(
    String path,
    int revision,
    AuthTokenResponse expected,
  ) async {
    final before = await _requireSession(revision);
    if (!_same(before, expected)) {
      throw const LabReadSessionUnavailable();
    }
    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    final after = await _requireSession(revision);
    if (!_same(before, after) || !_same(expected, after)) {
      throw const LabReadSessionUnavailable();
    }
    return response;
  }

  static void _id(int value, String label) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, label, 'Invalid positive identifier.');
    }
  }

  static void _pagination(int page, int perPage) {
    if (page <= 0 || perPage <= 0 || perPage > 100) {
      throw ArgumentError('Invalid laboratory pagination.');
    }
  }

  Future<LabReadPage<LabCatalogTest>> fetchCatalog({
    int page = 1,
    int perPage = 20,
  }) async {
    _pagination(page, perPage);
    final revision = sessionNavigationGuard.revision;
    final session = await _requireSession(revision);
    final raw = await _get(
      Uri(
        path: 'api/v1/lab/tests',
        queryParameters: {
          'page': '$page',
          'per_page': '$perPage',
          'active_only': 'true',
        },
      ).toString(),
      revision,
      session,
    );
    return LabReadPage<LabCatalogTest>.fromEnvelope(
      raw,
      requestedPage: page,
      requestedPerPage: perPage,
      parse: (row) => LabCatalogTest.fromJson(
        row,
        expectedClinicId: session.clinicContextId!,
      ),
      idOf: (test) => test.id,
    );
  }

  Future<LabReadPage<LabPatientOrder>> fetchPatientOrders({
    required int patientId,
    int page = 1,
    int perPage = 20,
  }) async {
    _id(patientId, 'patientId');
    _pagination(page, perPage);
    final revision = sessionNavigationGuard.revision;
    final session = await _requireSession(revision);
    final raw = await _get(
      Uri(
        path: 'api/v1/lab/orders',
        queryParameters: {
          'patient_id': '$patientId',
          'page': '$page',
          'per_page': '$perPage',
        },
      ).toString(),
      revision,
      session,
    );
    return LabReadPage<LabPatientOrder>.fromEnvelope(
      raw,
      requestedPage: page,
      requestedPerPage: perPage,
      parse: (row) => LabPatientOrder.fromJson(
        row,
        expectedClinicId: session.clinicContextId!,
        expectedPatientId: patientId,
      ),
      idOf: (order) => order.id,
    );
  }

  Future<LabPatientOrder> fetchOrder({
    required int patientId,
    required int orderId,
  }) async {
    _id(patientId, 'patientId');
    _id(orderId, 'orderId');
    final revision = sessionNavigationGuard.revision;
    final session = await _requireSession(revision);
    final envelope = await _get(
      'api/v1/lab/orders/$orderId',
      revision,
      session,
    );
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid laboratory detail envelope.');
    }
    return LabPatientOrder.fromJson(
      envelope['data'] as Map<String, dynamic>,
      expectedClinicId: session.clinicContextId!,
      expectedPatientId: patientId,
      expectedId: orderId,
    );
  }
}
