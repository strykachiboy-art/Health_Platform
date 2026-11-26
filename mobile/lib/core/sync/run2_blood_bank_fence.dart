import 'dart:convert';

import 'package:cryptography/cryptography.dart';

import '../../features/auth/data/models/auth_token_response.dart';
import 'operation_lifecycle.dart';
import 'operation_payload_crypto.dart';
import 'operation_queue_store.dart';
import 'tenant_operation_scope.dart';

/// Local pre-send safety fence, not an offline Blood Bank mutation. The marker
/// contains no patient, order, barcode, component, admission or clinical data.
/// No automatic replay and no user-facing clear without authoritative review.
class Run2BloodBankFence {
  const Run2BloodBankFence(this.store);
  final OperationQueueStore store;
  static const type = 'local.blood_bank.fence';

  OperationPayloadContext _context(AuthTokenResponse session) {
    final context = TenantOperationScope.fromSession(session);
    if (context == null) {
      throw StateError('Blood Bank fence scope unavailable.');
    }
    // Separate from all replay/read/draft scopes and intentionally independent
    // of role, so a different staff role cannot bypass an uncertain write.
    return OperationPayloadContext(
      userId: context.userId,
      clinicContextId: '${context.clinicContextId}:blood-bank-fence',
    );
  }

  Future<String> _id(OperationPayloadContext context) async {
    final hash = await Sha256().hash(
      utf8.encode(jsonEncode([type, context.userId, context.clinicContextId])),
    );
    return 'bb_fence_${base64UrlEncode(hash.bytes).replaceAll('=', '')}';
  }

  Future<bool> blocked(AuthTokenResponse session) async {
    final context = _context(session);
    final rows = await store.listScopedOperationPage(
      context: context,
      operationType: type,
      limit: 1,
    );
    return rows.isNotEmpty;
  }

  Future<String> begin(AuthTokenResponse session) async {
    final context = _context(session);
    final id = await _id(context);
    final result = await store.enqueueEncrypted(
      OperationIdentity(
        operationId: id,
        operationType: type,
        idempotencyKey: id,
      ),
      context: context,
      payload: const {'kind': 'blood_bank_write_fence'},
    );
    if (result != OperationQueueEnqueueResult.inserted) {
      throw StateError('A Blood Bank write requires server review.');
    }
    return id;
  }

  /// Called only after the actual online mutation response passes the existing
  /// action-specific parser and the same authenticated session is revalidated.
  Future<void> complete(AuthTokenResponse session, String id) async {
    final context = _context(session);
    if (id != await _id(context)) {
      throw StateError('Blood Bank fence identity mismatch.');
    }
    await store.completeRun2BloodBankFence(operationId: id, context: context);
  }
}
