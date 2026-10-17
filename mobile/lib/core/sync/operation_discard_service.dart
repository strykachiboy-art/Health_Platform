import '../networking/api_client.dart';
import '../networking/api_exception.dart';
import 'operation_lifecycle.dart';
import 'operation_payload_crypto.dart';
import 'operation_queue_store.dart';
import '../storage/app_database.dart';
import '../../features/auth/data/models/auth_token_response.dart';

typedef OperationDiscardSessionLoader = Future<AuthTokenResponse?> Function();

typedef OperationDiscardDispositionSender =
    Future<Map<String, dynamic>> Function({
      required String path,
      required Map<String, dynamic> body,
      required String bearerToken,
    });

enum OperationDiscardStatus {
  deleted,
  deletedKeyRetainedForDependencies,
  deletedKeyCleanupPending,
  alreadyAbsentKeyDeleted,
  alreadyAbsentKeyRetainedForDependencies,
  alreadyAbsentKeyCleanupPending,
  serverDispositionNotConfirmed,
  outcomeRequiresReconciliation,
  manualReconciliationRequired,
  unsupportedOperation,
  contextMismatch,
  acknowledgedOperation,
  sessionUnavailable,
  invalidServerReceipt,
  localCleanupFailed,
}

class OperationDiscardResult {
  const OperationDiscardResult(
    this.status, {
    this.category,
    this.serverDispositionConfirmed = false,
  });

  final OperationDiscardStatus status;

  /// Fixed, non-sensitive reason. Never contains payload or raw server content.
  final String? category;

  /// True only when this call validated an authoritative server receipt.
  final bool serverDispositionConfirmed;

  bool get localRecordRemoved =>
      status == OperationDiscardStatus.deleted ||
      status == OperationDiscardStatus.deletedKeyRetainedForDependencies ||
      status == OperationDiscardStatus.deletedKeyCleanupPending;
}

/// Coordinates explicit offline discard with the authoritative backend first.
///
/// A local delete is attempted only after a matching server receipt confirms
/// `confirmed_no_effect`. This service never claims that an appointment was
/// cancelled on the server. Only operation types with a registered backend
/// disposition policy are allowed.
class OperationDiscardService {
  factory OperationDiscardService({
    required OperationQueueStore queueStore,
    required OperationDiscardSessionLoader loadSession,
    required OperationDiscardDispositionSender sendDisposition,
  }) {
    return OperationDiscardService._(queueStore, loadSession, sendDisposition);
  }

  OperationDiscardService._(
    this._queueStore,
    this._loadSession,
    this._sendDisposition,
  );

  factory OperationDiscardService.withApiClient({
    required OperationQueueStore queueStore,
    required OperationDiscardSessionLoader loadSession,
    required ApiClient apiClient,
  }) {
    return OperationDiscardService(
      queueStore: queueStore,
      loadSession: loadSession,
      sendDisposition:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
          }) {
            return apiClient.postJson(
              path,
              body: body,
              bearerToken: bearerToken,
            );
          },
    );
  }

  static const String appointmentCreateType = 'appointment.create';
  static const Set<String> _acceptedDispositionReasonCodes = <String>{
    'verified_not_sent',
    'manual_reconciliation_no_effect',
  };

  final OperationQueueStore _queueStore;
  final OperationDiscardSessionLoader _loadSession;
  final OperationDiscardDispositionSender _sendDisposition;

  OperationDiscardResult _confirmedResult(
    OperationDiscardStatus status, {
    String? category,
  }) => OperationDiscardResult(
    status,
    category: category,
    serverDispositionConfirmed: true,
  );

  Future<OperationDiscardResult> discard(
    String operationId, {
    bool manualNoEffectConfirmed = false,
  }) async {
    if (operationId.isEmpty ||
        operationId != operationId.trim() ||
        operationId.length > 128 ||
        operationId.runes.any((rune) => rune < 32)) {
      return const OperationDiscardResult(
        OperationDiscardStatus.serverDispositionNotConfirmed,
        category: 'invalidOperationId',
      );
    }

    final AuthTokenResponse? session;
    try {
      session = await _loadSession();
    } on Object {
      return const OperationDiscardResult(
        OperationDiscardStatus.sessionUnavailable,
      );
    }
    if (session == null ||
        session.accessToken.trim().isEmpty ||
        session.userId <= 0 ||
        session.clinicContextId == null ||
        session.clinicContextId! <= 0) {
      return const OperationDiscardResult(
        OperationDiscardStatus.sessionUnavailable,
      );
    }

    final context = OperationPayloadContext(
      userId: session.userId.toString(),
      clinicContextId: session.clinicContextId.toString(),
    );

    final OperationQueueEntryData? entry;
    try {
      entry = await _queueStore.findByOperationId(operationId);
    } on Object {
      return const OperationDiscardResult(
        OperationDiscardStatus.localCleanupFailed,
        category: 'queueReadFailed',
      );
    }

    // Recovery path for a previous successful local deletion whose secure-key
    // cleanup failed or was interrupted. It performs only a dependency-checked
    // key cleanup and never issues a new server operation without queue identity.
    if (entry == null) {
      try {
        final cleanup = await _queueStore.cleanupContextKeyIfUnused(
          context: context,
        );
        return switch (cleanup) {
          OperationQueueKeyCleanupStatus.deleted =>
            const OperationDiscardResult(
              OperationDiscardStatus.alreadyAbsentKeyDeleted,
            ),
          OperationQueueKeyCleanupStatus.retainedForPayloadDependencies =>
            const OperationDiscardResult(
              OperationDiscardStatus.alreadyAbsentKeyRetainedForDependencies,
            ),
          OperationQueueKeyCleanupStatus.failed => const OperationDiscardResult(
            OperationDiscardStatus.alreadyAbsentKeyCleanupPending,
            category: 'secureStorageDeleteFailed',
          ),
        };
      } on Object {
        return const OperationDiscardResult(
          OperationDiscardStatus.localCleanupFailed,
          category: 'keyCleanupCheckFailed',
        );
      }
    }

    if (entry.ownerUserId != context.userId ||
        entry.clinicContextId != context.clinicContextId) {
      return const OperationDiscardResult(
        OperationDiscardStatus.contextMismatch,
      );
    }
    if (entry.operationType != appointmentCreateType) {
      return const OperationDiscardResult(
        OperationDiscardStatus.unsupportedOperation,
      );
    }
    if (entry.lifecycleState == OperationLifecycleState.acknowledged.name) {
      return const OperationDiscardResult(
        OperationDiscardStatus.acknowledgedOperation,
      );
    }

    final neverSent =
        entry.lifecycleState == OperationLifecycleState.queued.name &&
        entry.attemptsCompleted == 0;
    if (!neverSent && !manualNoEffectConfirmed) {
      return const OperationDiscardResult(
        OperationDiscardStatus.manualReconciliationRequired,
        category: 'explicitManualNoEffectConfirmationRequired',
      );
    }

    final reasonCode = neverSent
        ? 'verified_not_sent'
        : 'manual_reconciliation_no_effect';
    final path =
        'api/v1/offline-operations/${Uri.encodeComponent(operationId)}/disposition';
    final body = <String, dynamic>{
      'operation_type': entry.operationType,
      'idempotency_key': entry.idempotencyKey,
      'confirmed_no_effect': true,
      'reason_code': reasonCode,
    };

    final Map<String, dynamic> receipt;
    try {
      receipt = await _sendDisposition(
        path: path,
        body: body,
        bearerToken: session.accessToken,
      );
    } on ApiException catch (error) {
      if (error.serverCode ==
          'OFFLINE_OPERATION_OUTCOME_REQUIRES_RECONCILIATION') {
        return const OperationDiscardResult(
          OperationDiscardStatus.outcomeRequiresReconciliation,
          category: 'serverOutcomeRequiresReconciliation',
        );
      }
      return const OperationDiscardResult(
        OperationDiscardStatus.serverDispositionNotConfirmed,
        category: 'serverDispositionRequestFailed',
      );
    } on Object {
      // A timeout can mean the backend committed but its response was lost.
      // Keep the local queue intact; a retry for this operation identity can
      // recover the authoritative record even if local state changed.
      return const OperationDiscardResult(
        OperationDiscardStatus.serverDispositionNotConfirmed,
        category: 'serverDispositionResponseUnavailable',
      );
    }

    final data = receipt['data'];
    if (receipt['success'] != true || data is! Map<String, dynamic>) {
      return const OperationDiscardResult(
        OperationDiscardStatus.invalidServerReceipt,
        category: 'invalidDispositionEnvelope',
      );
    }
    final dispositionId = data['id'];
    final policyVersion = data['policy_version'];
    if (dispositionId is! int ||
        dispositionId <= 0 ||
        data['operation_id'] != operationId ||
        data['operation_type'] != entry.operationType ||
        data['resolution'] != 'confirmed_no_effect' ||
        !_acceptedDispositionReasonCodes.contains(data['reason_code']) ||
        policyVersion is! String ||
        policyVersion.isEmpty) {
      return const OperationDiscardResult(
        OperationDiscardStatus.invalidServerReceipt,
        category: 'dispositionReceiptMismatch',
      );
    }

    try {
      final cleanupStatus = await _queueStore.discardAfterConfirmedDisposition(
        operationId: operationId,
        context: context,
        serverDispositionId: dispositionId,
      );
      return switch (cleanupStatus) {
        OperationQueueDiscardCleanupStatus.removedAndKeyDeleted =>
          _confirmedResult(OperationDiscardStatus.deleted),
        OperationQueueDiscardCleanupStatus.removedKeyRetainedForDependencies =>
          _confirmedResult(
            OperationDiscardStatus.deletedKeyRetainedForDependencies,
          ),
        OperationQueueDiscardCleanupStatus.removedKeyCleanupPending =>
          _confirmedResult(
            OperationDiscardStatus.deletedKeyCleanupPending,
            category: 'secureStorageDeleteFailed',
          ),
        OperationQueueDiscardCleanupStatus.alreadyAbsentAndKeyDeleted =>
          _confirmedResult(OperationDiscardStatus.alreadyAbsentKeyDeleted),
        OperationQueueDiscardCleanupStatus
            .alreadyAbsentKeyRetainedForDependencies =>
          _confirmedResult(
            OperationDiscardStatus.alreadyAbsentKeyRetainedForDependencies,
          ),
        OperationQueueDiscardCleanupStatus.alreadyAbsentKeyCleanupPending =>
          _confirmedResult(
            OperationDiscardStatus.alreadyAbsentKeyCleanupPending,
            category: 'secureStorageDeleteFailed',
          ),
        OperationQueueDiscardCleanupStatus.contextMismatch => _confirmedResult(
          OperationDiscardStatus.contextMismatch,
        ),
        OperationQueueDiscardCleanupStatus.acknowledgedOperation =>
          _confirmedResult(OperationDiscardStatus.acknowledgedOperation),
      };
    } on Object {
      // Server disposition is durable, but a failed local DB transaction leaves
      // queue data in place. A repeat request will receive the same receipt.
      return const OperationDiscardResult(
        OperationDiscardStatus.localCleanupFailed,
        category: 'localQueueDeleteFailed',
      );
    }
  }
}
