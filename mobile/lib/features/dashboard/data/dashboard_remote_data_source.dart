import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/dashboard_snapshot.dart';

/// Refuses to show a response fetched for an older user, role, or clinic.
/// No credentials or response body are included in error messages.
class DashboardSessionMismatchException implements Exception {
  const DashboardSessionMismatchException();
}

/// P22.S03: GET-only, no local PHI cache, no automatic retry, no new auth stack.
class DashboardRemoteDataSource {
  const DashboardRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard? sessionNavigationGuard;

  bool _isCurrentGuard(int? revision) {
    final guard = sessionNavigationGuard;
    return guard == null ||
        (guard.canAccessProtectedRoutes && guard.revision == revision);
  }

  Future<AuthTokenResponse> _requireSession() async {
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.accessToken.trim().isEmpty ||
          session.accessToken.trim() != session.accessToken) {
        throw const DashboardSessionMismatchException();
      }
      // Reject unknown role *before* making a request.
      DashboardFamily.fromRole(session.role);
      return session;
    } on DashboardSessionMismatchException {
      rethrow;
    } on Object {
      throw const DashboardSessionMismatchException();
    }
  }

  bool _sameSession(AuthTokenResponse a, AuthTokenResponse b) {
    return a.userId == b.userId &&
        a.role.toLowerCase() == b.role.toLowerCase() &&
        a.clinicContextId == b.clinicContextId &&
        a.accessToken == b.accessToken;
  }

  Future<DashboardSnapshot> fetchDashboard() async {
    final revision = sessionNavigationGuard?.revision;
    if (!_isCurrentGuard(revision)) {
      throw const DashboardSessionMismatchException();
    }
    final before = await _requireSession();
    if (!_isCurrentGuard(revision)) {
      throw const DashboardSessionMismatchException();
    }
    final response = await apiClient.getJson(
      'api/v1/dashboard',
      bearerToken: before.accessToken,
    );
    if (!_isCurrentGuard(revision)) {
      throw const DashboardSessionMismatchException();
    }
    final after = await _requireSession();
    if (!_isCurrentGuard(revision) || !_sameSession(before, after)) {
      throw const DashboardSessionMismatchException();
    }
    final snapshot = DashboardSnapshot.fromEnvelope(response);
    if (snapshot.context.role.toLowerCase() != before.role.toLowerCase()) {
      throw const FormatException('Dashboard role does not match session.');
    }
    // Super admin's dashboard is system-scoped even after a clinic selection.
    if (snapshot.context.family != DashboardFamily.system &&
        before.clinicContextId != null &&
        snapshot.context.clinicId != before.clinicContextId) {
      throw const FormatException('Dashboard clinic does not match session.');
    }
    return snapshot;
  }
}
