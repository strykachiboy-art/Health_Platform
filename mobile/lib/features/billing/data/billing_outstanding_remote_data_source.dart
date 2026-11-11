import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/outstanding_invoice.dart';

class BillingReadSessionUnavailable implements Exception {
  const BillingReadSessionUnavailable();
}

/// P23I.S02 only: admin-only GET /billing/invoices/outstanding.
/// Never creates, updates, pays, caches or estimates an invoice.
class BillingOutstandingRemoteDataSource {
  const BillingOutstandingRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _requireAdmin(int revision) async {
    if (!_current(revision)) throw const BillingReadSessionUnavailable();
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          session.role.trim().toLowerCase() != 'admin' ||
          !_current(revision)) {
        throw const BillingReadSessionUnavailable();
      }
      return session;
    } on BillingReadSessionUnavailable {
      rethrow;
    } on Object {
      throw const BillingReadSessionUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<OutstandingInvoicePage> fetchOutstanding({
    int page = 1,
    int perPage = 20,
  }) async {
    if (page < 1 || perPage < 1 || perPage > 100) {
      throw ArgumentError('Invalid outstanding-invoice pagination.');
    }
    final revision = sessionNavigationGuard.revision;
    final before = await _requireAdmin(revision);
    final path =
        'api/v1/billing/invoices/outstanding'
        '?page=$page&per_page=$perPage';
    final envelope = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    final after = await _requireAdmin(revision);
    if (!_same(before, after) || !_current(revision)) {
      throw const BillingReadSessionUnavailable();
    }
    return OutstandingInvoicePage.fromEnvelope(
      envelope,
      expectedClinicId: before.clinicContextId!,
      requestedPage: page,
      requestedPerPage: perPage,
    );
  }
}
