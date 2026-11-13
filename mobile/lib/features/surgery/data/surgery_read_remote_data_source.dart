import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/surgery_read_models.dart';

/// Auth/session mismatch always suppresses old Surgery results and credentials.
class SurgeryReadDenied implements Exception {
  const SurgeryReadDenied();
}

/// Reuses the existing dashboard API, bearer token and session guard.
/// There are no POSTs, retries, local caches or background queue operations.
class SurgeryReadRemoteDataSource {
  const SurgeryReadRemoteDataSource({required this.dashboardSource});

  final DashboardRemoteDataSource dashboardSource;

  bool get canOpen =>
      dashboardSource.sessionNavigationGuard?.canAccessProtectedRoutes == true;

  Future<AuthTokenResponse> _authorizedSession() async {
    try {
      final state = await dashboardSource.loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          !surgeryCanReadCases(session.role)) {
        throw const SurgeryReadDenied();
      }
      return session;
    } on SurgeryReadDenied {
      rethrow;
    } on Object {
      throw const SurgeryReadDenied();
    }
  }

  bool _same(AuthTokenResponse first, AuthTokenResponse second) =>
      first.userId == second.userId &&
      first.role.toLowerCase() == second.role.toLowerCase() &&
      first.clinicContextId == second.clinicContextId &&
      first.accessToken == second.accessToken;

  Future<Map<String, dynamic>> _get(String path) async {
    final guard = dashboardSource.sessionNavigationGuard;
    if (guard == null || !guard.canAccessProtectedRoutes) {
      throw const SurgeryReadDenied();
    }
    final revision = guard.revision;
    final before = await _authorizedSession();
    if (!guard.canAccessProtectedRoutes || guard.revision != revision) {
      throw const SurgeryReadDenied();
    }
    final response = await dashboardSource.apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    if (!guard.canAccessProtectedRoutes || guard.revision != revision) {
      throw const SurgeryReadDenied();
    }
    final after = await _authorizedSession();
    if (!guard.canAccessProtectedRoutes ||
        guard.revision != revision ||
        !_same(before, after)) {
      throw const SurgeryReadDenied();
    }
    if (response['success'] != true ||
        response['data'] is! Map<String, dynamic>) {
      throw const FormatException('Surgery read response is invalid.');
    }
    // Parsing happens inside the calling method, after the session guard.
    return response['data'] as Map<String, dynamic>;
  }

  Future<SurgeryWorklist> fetchWorklist({
    int limit = 20,
    int offset = 0,
  }) async {
    if (limit < 1 || limit > 50 || offset < 0 || offset > 5000) {
      throw ArgumentError('Surgery page request is invalid.');
    }
    final revision = dashboardSource.sessionNavigationGuard?.revision;
    final before = await _authorizedSession();
    if (!canOpen) throw const SurgeryReadDenied();
    final data = await _get(
      'api/v1/dashboard/surgery?limit=$limit&offset=$offset',
    );
    final current = await _authorizedSession();
    if (!canOpen ||
        dashboardSource.sessionNavigationGuard?.revision != revision ||
        !_same(before, current)) {
      throw const SurgeryReadDenied();
    }
    return SurgeryWorklist.fromJson(
      data,
      expectedClinic: current.clinicContextId!,
      role: current.role,
    );
  }

  Future<SurgeryCaseDetail> fetchCase(int caseId) async {
    if (caseId <= 0) {
      throw ArgumentError.value(caseId, 'caseId', 'Must be positive.');
    }
    final revision = dashboardSource.sessionNavigationGuard?.revision;
    final before = await _authorizedSession();
    if (!canOpen) throw const SurgeryReadDenied();
    final data = await _get('api/v1/dashboard/surgery/cases/$caseId');
    final current = await _authorizedSession();
    if (!canOpen ||
        dashboardSource.sessionNavigationGuard?.revision != revision ||
        !_same(before, current)) {
      throw const SurgeryReadDenied();
    }
    return SurgeryCaseDetail.fromJson(
      data,
      expectedClinic: current.clinicContextId!,
      expectedCase: caseId,
    );
  }
}
