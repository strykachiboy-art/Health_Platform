// P23F.S01 — GET-only eMAR source. No administration, scanner or offline replay.
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

class EmarReadSessionException implements Exception {
  const EmarReadSessionException();

  @override
  String toString() => 'eMAR session unavailable or changed.';
}

/// Transient GET-only readback with an authenticated, revision-bound context.
/// Role UI checks are not a substitute for server-side authorization.
class EmarReadRemoteDataSource {
  const EmarReadRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _readRoles = <String>{'doctor', 'nurse'};

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _authorized() async {
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
          !_readRoles.contains(session.role.trim().toLowerCase())) {
        throw const EmarReadSessionException();
      }
      return session;
    } on EmarReadSessionException {
      rethrow;
    } on Object {
      throw const EmarReadSessionException();
    }
  }

  bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  static void _id(int value, String name) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, name, 'Must be a positive identifier.');
    }
  }

  Future<T> _get<T>(
    String path,
    T Function(Map<String, dynamic> json, int clinicId) parse,
  ) async {
    final revision = sessionNavigationGuard.revision;
    if (!_current(revision)) throw const EmarReadSessionException();
    final before = await _authorized();
    if (!_current(revision)) throw const EmarReadSessionException();
    final json = await apiClient.getJson(path, bearerToken: before.accessToken);
    if (!_current(revision)) throw const EmarReadSessionException();
    final after = await _authorized();
    if (!_current(revision) || !_same(before, after)) {
      throw const EmarReadSessionException();
    }
    return parse(json, before.clinicContextId!);
  }

  Future<EmarAdministrationPage> listForAdmission({
    required int admissionId,
    int page = 1,
    int perPage = 50,
    EmarAdministrationStatus? status,
    int? expectedPatientId,
  }) {
    _id(admissionId, 'admissionId');
    if (expectedPatientId != null) _id(expectedPatientId, 'expectedPatientId');
    if (page < 1 || perPage < 1 || perPage > 500) {
      throw ArgumentError('Invalid eMAR pagination.');
    }
    final path = Uri(
      path: 'api/v1/emar/admissions/$admissionId',
      queryParameters: <String, String>{
        'page': '$page',
        'per_page': '$perPage',
        if (status != null) 'status': status.name,
      },
    ).toString();
    return _get(path, (json, clinicId) {
      return EmarAdministrationPage.fromJson(
        json,
        expectedClinicId: clinicId,
        expectedAdmissionId: admissionId,
        expectedPatientId: expectedPatientId,
        requestedPage: page,
        requestedPerPage: perPage,
      );
    });
  }

  Future<EmarAdministration> getAdministration({
    required int administrationId,
    int? expectedPatientId,
    int? expectedAdmissionId,
  }) {
    _id(administrationId, 'administrationId');
    if (expectedPatientId != null) _id(expectedPatientId, 'expectedPatientId');
    if (expectedAdmissionId != null) {
      _id(expectedAdmissionId, 'expectedAdmissionId');
    }
    return _get('api/v1/emar/$administrationId', (json, clinicId) {
      final record = json['administration'];
      if (record is! Map<String, dynamic>) {
        throw const FormatException('Missing eMAR detail envelope.');
      }
      return EmarAdministration.fromJson(
        record,
        expectedClinicId: clinicId,
        expectedId: administrationId,
        expectedPatientId: expectedPatientId,
        expectedAdmissionId: expectedAdmissionId,
      );
    });
  }

  Future<EmarBarcodeHistory> getBarcodeHistory({
    required int administrationId,
  }) {
    _id(administrationId, 'administrationId');
    return _get('api/v1/emar/$administrationId/barcode-events', (
      json,
      clinicId,
    ) {
      return EmarBarcodeHistory.fromJson(
        json,
        expectedClinicId: clinicId,
        expectedAdministrationId: administrationId,
      );
    });
  }
}
