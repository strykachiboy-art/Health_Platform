import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/billing/data/billing_outstanding_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/models/invoice_creation.dart';

class BillingInvoiceCreateScopeUnavailable implements Exception {
  const BillingInvoiceCreateScopeUnavailable();
}

class BillingInvoiceCreateAlreadyAttempted implements Exception {
  const BillingInvoiceCreateAlreadyAttempted();
}

class BillingInvoiceCreateUnconfirmed implements Exception {
  const BillingInvoiceCreateUnconfirmed();
}

/// One invoice creation POST maximum per source lifetime. This source must be
/// injected/cached by its owning workflow; do not reconstruct it to retry.
/// An uncertain response is NEVER automatically replayed, even with a key.
class BillingInvoiceCreateRemoteDataSource {
  BillingInvoiceCreateRemoteDataSource({required this.reader});

  final BillingOutstandingRemoteDataSource reader;
  bool _writeAttempted = false;
  bool _reserved = false;
  bool get writeAttempted => _writeAttempted;

  bool _current(int revision) =>
      reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      reader.sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _admin(int revision) async {
    if (!_current(revision)) throw const BillingInvoiceCreateScopeUnavailable();
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
        throw const BillingInvoiceCreateScopeUnavailable();
      }
      return session;
    } on BillingInvoiceCreateScopeUnavailable {
      rethrow;
    } on Object {
      throw const BillingInvoiceCreateScopeUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<CreatedInvoice> createOnce({
    required InvoiceDraft draft,
    required String idempotencyKey,
  }) async {
    if (_writeAttempted || _reserved) {
      throw const BillingInvoiceCreateAlreadyAttempted();
    }
    final body = draft.toJson();
    if (idempotencyKey.isEmpty ||
        idempotencyKey.length > 255 ||
        idempotencyKey.trim() != idempotencyKey ||
        idempotencyKey.contains('\r') ||
        idempotencyKey.contains('\n')) {
      throw const FormatException('Invalid invoice idempotency key.');
    }
    _reserved = true; // Reserve synchronously before awaiting session reads.
    final revision = reader.sessionNavigationGuard.revision;
    AuthTokenResponse before;
    try {
      before = await _admin(revision);
      if (!_same(before, await _admin(revision)) || !_current(revision)) {
        throw const BillingInvoiceCreateScopeUnavailable();
      }
    } on Object {
      _reserved = false;
      throw const BillingInvoiceCreateScopeUnavailable();
    }
    _writeAttempted = true;
    try {
      final envelope = await reader.apiClient.postJson(
        'api/v1/billing/invoices',
        body: body,
        bearerToken: before.accessToken,
        idempotencyKey: idempotencyKey,
      );
      final after = await _admin(revision);
      if (!_same(before, after) || !_current(revision)) {
        throw const BillingInvoiceCreateUnconfirmed();
      }
      return CreatedInvoice.fromEnvelope(
        envelope,
        expectedClinicId: before.clinicContextId!,
        requested: draft,
      );
    } on BillingInvoiceCreateUnconfirmed {
      rethrow;
    } on BillingInvoiceCreateScopeUnavailable {
      throw const BillingInvoiceCreateUnconfirmed();
    } on Object {
      // Network, malformed response, HTTP rejection, and session uncertainty
      // require human-led server reconciliation, never client-side replay.
      throw const BillingInvoiceCreateUnconfirmed();
    }
  }
}
