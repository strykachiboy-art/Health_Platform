import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/clinical_order_summary.dart';

class ClinicalOrdersSessionException implements Exception {
  const ClinicalOrdersSessionException();
  @override
  String toString() => 'Clinical orders access unavailable for this session.';
}

/// GET-only unified CPOE adapter. Never persists clinical order content.
class ClinicalOrdersRemoteDataSource {
  const ClinicalOrdersRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _roles = <String>{'admin', 'doctor', 'nurse'};

  bool _active(int revision) =>
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
          session.accessToken.trim() != session.accessToken ||
          !_roles.contains(session.role.trim().toLowerCase())) {
        throw const ClinicalOrdersSessionException();
      }
      return session;
    } on ClinicalOrdersSessionException {
      rethrow;
    } on Object {
      throw const ClinicalOrdersSessionException();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> authorizedSession() async {
    final revision = sessionNavigationGuard.revision;
    if (!_active(revision)) {
      throw const ClinicalOrdersSessionException();
    }
    final before = await _authorized();
    if (!_active(revision)) {
      throw const ClinicalOrdersSessionException();
    }
    return before;
  }

  Future<ClinicalOrderTimeline> fetchPatientOrders({
    required int patientId,
    String? type,
    String? status,
  }) async {
    if (patientId <= 0 || patientId > 2147483647) {
      throw ArgumentError.value(patientId, 'patientId');
    }
    if (type != null && !ClinicalOrderSummary.allowedTypes.contains(type)) {
      throw ArgumentError.value(type, 'type');
    }
    if (status != null &&
        !ClinicalOrderSummary.allowedStatuses.contains(status)) {
      throw ArgumentError.value(status, 'status');
    }
    final revision = sessionNavigationGuard.revision;
    if (!_active(revision)) {
      throw const ClinicalOrdersSessionException();
    }
    final before = await _authorized();
    if (!_active(revision)) {
      throw const ClinicalOrdersSessionException();
    }
    final path = Uri(
      path: 'api/v1/clinical-orders/patients/$patientId',
      queryParameters: <String, String>{'order_type': ?type, 'status': ?status},
    ).toString();
    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    if (!_active(revision)) {
      throw const ClinicalOrdersSessionException();
    }
    final after = await _authorized();
    if (!_active(revision) || !_same(before, after)) {
      throw const ClinicalOrdersSessionException();
    }
    return ClinicalOrderTimeline.fromJson(
      response,
      expectedClinicId: before.clinicContextId!,
      expectedPatientId: patientId,
      type: type,
      status: status,
    );
  }
}
