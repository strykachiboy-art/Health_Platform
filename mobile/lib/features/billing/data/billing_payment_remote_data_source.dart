import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/billing/data/billing_outstanding_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/models/billing_payment.dart';
import 'package:clinic_system_pro/features/billing/data/models/outstanding_invoice.dart';

class BillingPaymentScopeUnavailable implements Exception {
  const BillingPaymentScopeUnavailable();
}

class BillingPaymentEvidenceStale implements Exception {
  const BillingPaymentEvidenceStale();
}

class BillingPaymentAlreadyAttempted implements Exception {
  const BillingPaymentAlreadyAttempted();
}

class BillingPaymentOutcomeUnconfirmed implements Exception {
  const BillingPaymentOutcomeUnconfirmed();
}

/// Admin-only, online-only payment source. ONE financial POST per instance.
/// Preflight uses the existing outstanding-invoice GET, never a fabricated
/// invoice lookup. The source must be cached for its workflow lifetime.
class BillingPaymentRemoteDataSource {
  BillingPaymentRemoteDataSource({required this.reader});

  final BillingOutstandingRemoteDataSource reader;
  bool _reserved = false;
  bool _writeAttempted = false;
  bool get writeAttempted => _writeAttempted;

  bool _current(int revision) =>
      reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      reader.sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _admin(int revision) async {
    if (!_current(revision)) throw const BillingPaymentScopeUnavailable();
    try {
      final state = await reader.loadSession();
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
        throw const BillingPaymentScopeUnavailable();
      }
      return session;
    } on BillingPaymentScopeUnavailable {
      rethrow;
    } on Object {
      throw const BillingPaymentScopeUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<BillingPaymentConfirmation> submitOnce({
    required BillingPaymentDraft draft,
    required String idempotencyKey,
    required int sourcePage,
    required int sourcePerPage,
  }) async {
    if (_reserved || _writeAttempted) {
      throw const BillingPaymentAlreadyAttempted();
    }
    final body = draft.toJson();
    if (idempotencyKey.isEmpty ||
        idempotencyKey.length > 255 ||
        idempotencyKey != idempotencyKey.trim() ||
        idempotencyKey.contains('\n') ||
        idempotencyKey.contains('\r') ||
        sourcePage < 1 ||
        sourcePerPage < 1 ||
        sourcePerPage > 100) {
      throw const FormatException('Invalid payment submission evidence.');
    }
    // This reservation is synchronous; duplicate taps cannot race the GET.
    _reserved = true;
    final revision = reader.sessionNavigationGuard.revision;
    AuthTokenResponse before;
    try {
      before = await _admin(revision);
      if (draft.invoice.clinicId != before.clinicContextId) {
        throw const BillingPaymentScopeUnavailable();
      }
      final page = await reader.fetchOutstanding(
        page: sourcePage,
        perPage: sourcePerPage,
      );
      OutstandingInvoice? fresh;
      for (final row in page.items) {
        if (row.id == draft.invoice.id) {
          fresh = row;
          break;
        }
      }
      if (fresh == null ||
          fresh.clinicId != draft.invoice.clinicId ||
          fresh.patientId != draft.invoice.patientId ||
          fresh.invoiceNumber != draft.invoice.invoiceNumber ||
          fresh.currency != draft.invoice.currency ||
          fresh.totalAmount != draft.invoice.totalAmount ||
          fresh.amountPaid != draft.invoice.amountPaid ||
          fresh.status != draft.invoice.status) {
        throw const BillingPaymentEvidenceStale();
      }
      if (!_same(before, await _admin(revision)) || !_current(revision)) {
        throw const BillingPaymentScopeUnavailable();
      }
    } on BillingPaymentEvidenceStale {
      _reserved = false;
      rethrow;
    } on BillingPaymentScopeUnavailable {
      _reserved = false;
      rethrow;
    } on Object {
      _reserved = false;
      throw const BillingPaymentScopeUnavailable();
    }
    _writeAttempted = true;
    try {
      final gateway = draft.flow == BillingPaymentFlow.gatewayInitialize;
      final envelope = await reader.apiClient.postJson(
        gateway
            ? 'api/v1/billing/payments/initialize'
            : 'api/v1/billing/payments',
        body: body,
        bearerToken: before.accessToken,
        idempotencyKey: idempotencyKey,
      );
      final after = await _admin(revision);
      if (!_same(before, after) || !_current(revision)) {
        throw const BillingPaymentOutcomeUnconfirmed();
      }
      return BillingPaymentConfirmation.fromEnvelope(
        envelope,
        requested: draft,
      );
    } on BillingPaymentOutcomeUnconfirmed {
      rethrow;
    } on Object {
      // This includes HTTP 422 and uncertain network replies. No replay.
      throw const BillingPaymentOutcomeUnconfirmed();
    }
  }
}
