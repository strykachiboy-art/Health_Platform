import '../networking/api_client.dart';
import '../networking/api_exception.dart';
import '../../features/auth/data/models/auth_token_response.dart';
import 'operation_lifecycle.dart';
import 'operation_payload_crypto.dart';
import 'operation_queue_store.dart';
import '../storage/app_database.dart';

typedef OperationReplaySessionLoader = Future<AuthTokenResponse?> Function();

typedef OperationReplayAppointmentSender =
    Future<Map<String, dynamic>> Function({
      required String path,
      required Map<String, dynamic> body,
      required String bearerToken,
      required String idempotencyKey,
    });

enum OperationReplayDisposition {
  acknowledged,
  retryScheduled,
  reconciliationRequired,
  rejected,
  userInterventionRequired,
  blockedInvalidPayload,
  staleOperation,
}

class OperationReplayItemResult {
  const OperationReplayItemResult({
    required this.operationId,
    required this.disposition,
    this.category,
  });

  final String operationId;
  final OperationReplayDisposition disposition;

  /// Fixed, non-sensitive category. Never contains payloads or raw errors.
  final String? category;
}

class OperationReplayRunResult {
  OperationReplayRunResult({
    required List<OperationReplayItemResult> items,
    this.blockedReason,
    this.nextRetryAt,
  }) : items = List<OperationReplayItemResult>.unmodifiable(items);

  final List<OperationReplayItemResult> items;
  final String? blockedReason;

  /// Earliest durable retry time for the active owner and clinic.
  final DateTime? nextRetryAt;

  int get acknowledged => items
      .where(
        (item) => item.disposition == OperationReplayDisposition.acknowledged,
      )
      .length;

  int get retryScheduled => items
      .where(
        (item) => item.disposition == OperationReplayDisposition.retryScheduled,
      )
      .length;

  int get reconciliationRequired => items
      .where(
        (item) =>
            item.disposition ==
            OperationReplayDisposition.reconciliationRequired,
      )
      .length;

  int get userInterventionRequired => items
      .where(
        (item) =>
            item.disposition ==
            OperationReplayDisposition.userInterventionRequired,
      )
      .length;
}

enum OperationReconciliationDisposition {
  acknowledged,
  invalidOperationId,
  sessionUnavailable,
  clinicContextUnavailable,
  contextMismatch,
  sessionChangedBeforeSend,
  operationNotFound,
  unsupportedOperation,
  notReconciliationRequired,
  payloadUnavailableOrInvalid,
  storageUnavailable,
  alreadyRunning,
  unresolved,
  userInterventionRequired,
  acknowledgementPersistenceUncertain,
}

class OperationReconciliationResult {
  const OperationReconciliationResult({
    required this.operationId,
    required this.disposition,
    this.category,
  });

  final String operationId;
  final OperationReconciliationDisposition disposition;

  /// Fixed, non-sensitive diagnostic category.
  final String? category;

  bool get isAcknowledged =>
      disposition == OperationReconciliationDisposition.acknowledged;
}

typedef OperationReconciliationRunner =
    Future<OperationReconciliationResult> Function(String operationId);

/// Replays only appointment creation, the mutation whose backend stable-key
/// idempotency contract has been verified.
///
/// The caller supplies the current session when each run begins. Bearer
/// tokens are not persisted in the operation queue.
class _OperationReconciliationEntry {
  const _OperationReconciliationEntry(this.entry);

  final OperationQueueEntryData entry;
}

class _OperationReconciliationStop {
  const _OperationReconciliationStop(this.result);

  final OperationReconciliationResult result;
}

class OperationReplayWorker {
  factory OperationReplayWorker({
    required OperationQueueStore queueStore,
    required OperationReplaySessionLoader loadSession,
    required OperationReplayAppointmentSender sendAppointmentCreate,
    DateTime Function()? clock,
  }) {
    return OperationReplayWorker._(
      queueStore,
      loadSession,
      sendAppointmentCreate,
      clock: clock,
    );
  }

  OperationReplayWorker._(
    this._queueStore,
    this._loadSession,
    this._sendAppointmentCreate, {
    DateTime Function()? clock,
  }) : _clock = clock ?? DateTime.now;

  factory OperationReplayWorker.withApiClient({
    required OperationQueueStore queueStore,
    required OperationReplaySessionLoader loadSession,
    required ApiClient apiClient,
    DateTime Function()? clock,
  }) {
    return OperationReplayWorker(
      queueStore: queueStore,
      loadSession: loadSession,
      clock: clock,
      sendAppointmentCreate:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) {
            return apiClient.postJson(
              path,
              body: body,
              bearerToken: bearerToken,
              idempotencyKey: idempotencyKey,
            );
          },
    );
  }

  static const String appointmentCreateType = 'appointment.create';
  static const String appointmentCreatePath = 'api/v1/appointments/';

  static const OperationReplayContract _appointmentContract =
      OperationReplayContract.endpointSupportsStableIdempotencyKey;

  final OperationQueueStore _queueStore;
  final OperationReplaySessionLoader _loadSession;
  final OperationReplayAppointmentSender _sendAppointmentCreate;
  final DateTime Function() _clock;

  final Set<String> _activeReconciliations = <String>{};

  Future<OperationReplayRunResult> runOnce() async {
    final results = <OperationReplayItemResult>[];

    final AuthTokenResponse? session;
    try {
      session = await _loadSession();
    } catch (_) {
      return OperationReplayRunResult(
        items: results,
        blockedReason: 'sessionUnavailable',
      );
    }

    if (session == null || session.accessToken.trim().isEmpty) {
      return OperationReplayRunResult(
        items: results,
        blockedReason: 'sessionUnavailable',
      );
    }

    final clinicId = session.clinicContextId;

    if (session.userId <= 0 || clinicId == null || clinicId <= 0) {
      return OperationReplayRunResult(
        items: results,
        blockedReason: 'clinicContextUnavailable',
      );
    }

    final context = OperationPayloadContext(
      userId: session.userId.toString(),
      clinicContextId: clinicId.toString(),
    );

    final now = _clock().toUtc();

    // A process may exit either before sending or after the server commits.
    // Both cases are unknown outcomes; replay safety is endpoint-specific.
    final interrupted = await _queueStore.listInFlight(
      context: context,
      limit: 100,
    );

    for (final entry in interrupted) {
      final contract = entry.operationType == appointmentCreateType
          ? _appointmentContract
          : OperationReplayContract.notVerified;

      final updated = await _queueStore.recordFailure(
        operationId: entry.operationId,
        failure: OperationAttemptFailure.outcomeUnknown,
        now: now,
        replayContract: contract,
        jitterSample: 0.5,
      );

      results.add(
        OperationReplayItemResult(
          operationId: entry.operationId,
          disposition: _dispositionFor(updated.lifecycleState),
          category: 'interruptedInFlight',
        ),
      );
    }

    // Do not automatically replay patient mutations or other unverified APIs.
    final ready = await _queueStore.listReady(
      context: context,
      now: now,
      limit: 100,
      operationTypes: const <String>{appointmentCreateType},
    );

    for (final entry in ready) {
      AuthTokenResponse? operationSession;
      try {
        operationSession = await _loadSession();
      } catch (_) {
        break;
      }

      if (!_sessionMatchesContext(operationSession, context)) {
        break;
      }

      Map<String, dynamic> payload;

      try {
        final loaded = await _queueStore.loadPayload(
          operationId: entry.operationId,
          context: context,
        );

        _validateAppointmentPayload(loaded);
        payload = loaded;
      } catch (_) {
        // Never send data that could not be decrypted and validated.
        results.add(
          OperationReplayItemResult(
            operationId: entry.operationId,
            disposition: OperationReplayDisposition.blockedInvalidPayload,
            category: 'payloadUnavailableOrInvalid',
          ),
        );
        continue;
      }

      final OperationLifecycleState expectedState;

      try {
        expectedState = OperationLifecycleState.values.byName(
          entry.lifecycleState,
        );
      } on ArgumentError {
        results.add(
          OperationReplayItemResult(
            operationId: entry.operationId,
            disposition: OperationReplayDisposition.blockedInvalidPayload,
            category: 'lifecycleStateInvalid',
          ),
        );
        continue;
      }

      // Persist inFlight before the network call so a crash is recoverable.
      try {
        await _queueStore.transition(
          operationId: entry.operationId,
          expectedState: expectedState,
          nextState: OperationLifecycleState.inFlight,
          context: context,
          now: now,
        );
      } catch (_) {
        results.add(
          OperationReplayItemResult(
            operationId: entry.operationId,
            disposition: OperationReplayDisposition.staleOperation,
            category: 'stateChangedBeforeSend',
          ),
        );
        continue;
      }

      AuthTokenResponse? sendSession;
      try {
        sendSession = await _loadSession();
      } catch (_) {
        sendSession = null;
      }

      if (!_sessionMatchesContext(sendSession, context)) {
        final unsent = await _queueStore.recordFailure(
          operationId: entry.operationId,
          failure: OperationAttemptFailure.confirmedTransientNoEffect,
          now: _clock().toUtc(),
          replayContract: _appointmentContract,
          jitterSample: 0.5,
        );

        results.add(
          OperationReplayItemResult(
            operationId: entry.operationId,
            disposition: _dispositionFor(unsent.lifecycleState),
            category: 'sessionChangedBeforeSend',
          ),
        );
        break;
      }

      OperationAttemptFailure? failure;
      try {
        await _sendAppointmentCreate(
          path: appointmentCreatePath,
          body: payload,
          bearerToken: sendSession!.accessToken,
          idempotencyKey: entry.idempotencyKey,
        );
      } on ApiException catch (exception) {
        failure = _classifyApiFailure(exception);
      } catch (_) {
        // Unknown transport failures do not prove that no effect occurred.
        failure = OperationAttemptFailure.outcomeUnknown;
      }

      if (failure == null) {
        try {
          final acknowledged = await _queueStore.transition(
            operationId: entry.operationId,
            expectedState: OperationLifecycleState.inFlight,
            nextState: OperationLifecycleState.acknowledged,
            now: _clock().toUtc(),
          );

          results.add(
            OperationReplayItemResult(
              operationId: entry.operationId,
              disposition: _dispositionFor(acknowledged.lifecycleState),
            ),
          );
        } catch (_) {
          // Do not invent an acknowledgement if persistence is uncertain.
          results.add(
            OperationReplayItemResult(
              operationId: entry.operationId,
              disposition: OperationReplayDisposition.staleOperation,
              category: 'acknowledgementPersistenceUncertain',
            ),
          );
        }

        continue;
      }

      final updated = await _queueStore.recordFailure(
        operationId: entry.operationId,
        failure: failure,
        now: _clock().toUtc(),
        replayContract: _appointmentContract,
        jitterSample: 0.5,
      );

      results.add(
        OperationReplayItemResult(
          operationId: entry.operationId,
          disposition: _dispositionFor(updated.lifecycleState),
          category: _failureCategory(failure),
        ),
      );
    }

    final nextRetryAt = await _queueStore.nextScheduledAttempt(
      context: context,
    );

    return OperationReplayRunResult(items: results, nextRetryAt: nextRetryAt);
  }

  /// Makes one explicit, user-requested reconciliation attempt.
  ///
  /// It sends only the original authenticated encrypted payload and original
  /// idempotency key. Unresolved outcomes remain in reconciliationRequired;
  /// recognized conflicts requiring a human decision transition to
  /// userInterventionRequired only after the durable state is confirmed.
  Future<OperationReconciliationResult> reconcileAppointmentCreate(
    String operationId,
  ) async {
    final id = operationId.trim();

    OperationReconciliationResult result(
      OperationReconciliationDisposition disposition, {
      String? category,
      String? resultId,
    }) {
      return OperationReconciliationResult(
        operationId: resultId ?? id,
        disposition: disposition,
        category: category,
      );
    }

    if (id.isEmpty || id.length > 128 || id != operationId) {
      return result(
        OperationReconciliationDisposition.invalidOperationId,
        category: 'invalidOperationId',
        resultId: '',
      );
    }

    if (!_activeReconciliations.add(id)) {
      return result(
        OperationReconciliationDisposition.alreadyRunning,
        category: 'alreadyRunning',
      );
    }

    try {
      AuthTokenResponse? session;
      try {
        session = await _loadSession();
      } catch (_) {
        return result(
          OperationReconciliationDisposition.sessionUnavailable,
          category: 'sessionUnavailable',
        );
      }

      if (session == null ||
          session.accessToken.trim().isEmpty ||
          session.userId <= 0) {
        return result(
          OperationReconciliationDisposition.sessionUnavailable,
          category: 'sessionUnavailable',
        );
      }

      final clinicId = session.clinicContextId;
      if (clinicId == null || clinicId <= 0) {
        return result(
          OperationReconciliationDisposition.clinicContextUnavailable,
          category: 'clinicContextUnavailable',
        );
      }

      final context = OperationPayloadContext(
        userId: session.userId.toString(),
        clinicContextId: clinicId.toString(),
      );

      final entry = await _findForReconciliation(id, result);
      if (entry is _OperationReconciliationStop) {
        return entry.result;
      }
      final initialEntry = (entry as _OperationReconciliationEntry).entry;

      if (initialEntry.ownerUserId != context.userId ||
          initialEntry.clinicContextId != context.clinicContextId) {
        return result(
          OperationReconciliationDisposition.contextMismatch,
          category: 'contextMismatch',
        );
      }

      if (initialEntry.operationType != appointmentCreateType) {
        return result(
          OperationReconciliationDisposition.unsupportedOperation,
          category: 'unsupportedOperation',
        );
      }

      if (initialEntry.lifecycleState !=
          OperationLifecycleState.reconciliationRequired.name) {
        if (initialEntry.lifecycleState ==
            OperationLifecycleState.acknowledged.name) {
          return result(OperationReconciliationDisposition.acknowledged);
        }
        return result(
          OperationReconciliationDisposition.notReconciliationRequired,
          category: 'notReconciliationRequired',
        );
      }

      Map<String, dynamic> payload;
      try {
        payload = await _queueStore.loadPayload(
          operationId: id,
          context: context,
        );
        _validateAppointmentPayload(payload);
      } catch (_) {
        return result(
          OperationReconciliationDisposition.payloadUnavailableOrInvalid,
          category: 'payloadUnavailableOrInvalid',
        );
      }

      // Revalidate the authenticated context immediately before any request.
      AuthTokenResponse? sendSession;
      try {
        sendSession = await _loadSession();
      } catch (_) {
        sendSession = null;
      }

      if (!_sessionMatchesContext(sendSession, context)) {
        return result(
          OperationReconciliationDisposition.sessionChangedBeforeSend,
          category: 'sessionChangedBeforeSend',
        );
      }

      // Re-read state to catch changes while decrypting and validating.
      final currentResult = await _findForReconciliation(id, result);
      if (currentResult is _OperationReconciliationStop) {
        return currentResult.result;
      }
      final current = (currentResult as _OperationReconciliationEntry).entry;

      if (current.ownerUserId != context.userId ||
          current.clinicContextId != context.clinicContextId) {
        return result(
          OperationReconciliationDisposition.contextMismatch,
          category: 'contextMismatch',
        );
      }

      if (current.lifecycleState == OperationLifecycleState.acknowledged.name) {
        return result(OperationReconciliationDisposition.acknowledged);
      }

      if (current.operationType != appointmentCreateType) {
        return result(
          OperationReconciliationDisposition.unsupportedOperation,
          category: 'unsupportedOperation',
        );
      }

      if (current.lifecycleState !=
          OperationLifecycleState.reconciliationRequired.name) {
        return result(
          OperationReconciliationDisposition.notReconciliationRequired,
          category: 'notReconciliationRequired',
        );
      }

      if (current.idempotencyKey != initialEntry.idempotencyKey) {
        return result(
          OperationReconciliationDisposition.unresolved,
          category: 'operationIdentityChanged',
        );
      }

      try {
        await _sendAppointmentCreate(
          path: appointmentCreatePath,
          body: payload,
          bearerToken: sendSession!.accessToken,
          idempotencyKey: initialEntry.idempotencyKey,
        );
      } on ApiException catch (exception) {
        final failure = _classifyApiFailure(exception);
        final category = _failureCategory(failure);

        if (_requiresConflictUserIntervention(failure)) {
          try {
            await _queueStore.transition(
              operationId: id,
              expectedState: OperationLifecycleState.reconciliationRequired,
              nextState: OperationLifecycleState.userInterventionRequired,
              now: _clock().toUtc(),
              failure: failure,
              replayContract: _appointmentContract,
            );

            return result(
              OperationReconciliationDisposition.userInterventionRequired,
              category: category,
            );
          } catch (_) {
            // Resolve a concurrent state change before reporting persistence
            // uncertainty; never claim the transition without durable proof.
            try {
              final latest = await _queueStore.findByOperationId(id);

              if (latest?.lifecycleState ==
                  OperationLifecycleState.acknowledged.name) {
                return result(OperationReconciliationDisposition.acknowledged);
              }

              if (latest?.lifecycleState ==
                  OperationLifecycleState.userInterventionRequired.name) {
                return result(
                  OperationReconciliationDisposition.userInterventionRequired,
                  category: category,
                );
              }
            } catch (_) {
              // Durable state still cannot be established.
            }

            return result(
              OperationReconciliationDisposition.unresolved,
              category: 'conflictStatePersistenceUncertain',
            );
          }
        }

        return result(
          OperationReconciliationDisposition.unresolved,
          category: category,
        );
      } catch (_) {
        return result(
          OperationReconciliationDisposition.unresolved,
          category: 'outcomeUnknown',
        );
      }

      try {
        await _queueStore.transition(
          operationId: id,
          expectedState: OperationLifecycleState.reconciliationRequired,
          nextState: OperationLifecycleState.acknowledged,
          now: _clock().toUtc(),
        );
        return result(OperationReconciliationDisposition.acknowledged);
      } catch (_) {
        // Another explicit reconciliation may have persisted the same
        // acknowledgement. Read the durable state before reporting success.
        try {
          final latest = await _queueStore.findByOperationId(id);
          if (latest?.lifecycleState ==
              OperationLifecycleState.acknowledged.name) {
            return result(OperationReconciliationDisposition.acknowledged);
          }
        } catch (_) {
          // The acknowledgement status remains uncertain.
        }

        return result(
          OperationReconciliationDisposition
              .acknowledgementPersistenceUncertain,
          category: 'acknowledgementPersistenceUncertain',
        );
      }
    } finally {
      _activeReconciliations.remove(id);
    }
  }

  Future<Object> _findForReconciliation(
    String id,
    OperationReconciliationResult Function(
      OperationReconciliationDisposition disposition, {
      String? category,
      String? resultId,
    })
    result,
  ) async {
    try {
      final entry = await _queueStore.findByOperationId(id);
      if (entry == null) {
        return _OperationReconciliationStop(
          result(
            OperationReconciliationDisposition.operationNotFound,
            category: 'operationNotFound',
          ),
        );
      }
      return _OperationReconciliationEntry(entry);
    } catch (_) {
      return _OperationReconciliationStop(
        result(
          OperationReconciliationDisposition.storageUnavailable,
          category: 'storageUnavailable',
        ),
      );
    }
  }

  static bool _sessionMatchesContext(
    AuthTokenResponse? session,
    OperationPayloadContext context,
  ) {
    return session != null &&
        session.userId > 0 &&
        session.accessToken.trim().isNotEmpty &&
        session.userId.toString() == context.userId &&
        session.clinicContextId?.toString() == context.clinicContextId;
  }

  static OperationAttemptFailure _classifyApiFailure(ApiException exception) {
    if (exception.kind != ApiFailureKind.httpStatus) {
      return OperationAttemptFailure.outcomeUnknown;
    }

    final status = exception.statusCode;

    if (status == null) return OperationAttemptFailure.outcomeUnknown;
    if (status == 401) return OperationAttemptFailure.sessionExpired;
    if (status == 403) return OperationAttemptFailure.authorizationDenied;

    if (status == 409) {
      switch (exception.serverCode) {
        case 'APPOINTMENT_PATIENT_SCHEDULE_CONFLICT':
          return OperationAttemptFailure.appointmentPatientScheduleConflict;
        case 'APPOINTMENT_STAFF_SCHEDULE_CONFLICT':
          return OperationAttemptFailure.appointmentStaffScheduleConflict;
        case 'IDEMPOTENCY_KEY_PAYLOAD_MISMATCH':
          return OperationAttemptFailure.idempotencyKeyPayloadMismatch;
        case 'IDEMPOTENCY_OUTCOME_UNRESOLVED':
          return OperationAttemptFailure.idempotencyOutcomeUnresolved;
        case 'IDEMPOTENCY_RECORD_UNRESOLVED':
          return OperationAttemptFailure.idempotencyRecordUnresolved;
        default:
          // Older servers and unknown codes keep the conservative fallback.
          return OperationAttemptFailure.serverConflict;
      }
    }

    if (status == 408 || status == 429 || status >= 500) {
      return OperationAttemptFailure.outcomeUnknown;
    }

    if (status >= 400 && status < 500) {
      return OperationAttemptFailure.definitiveRejectionWithoutEffect;
    }

    return OperationAttemptFailure.outcomeUnknown;
  }

  static String _failureCategory(OperationAttemptFailure failure) {
    return switch (failure) {
      OperationAttemptFailure.confirmedTransientNoEffect =>
        'confirmedTransientFailure',
      OperationAttemptFailure.outcomeUnknown => 'outcomeUnknown',
      OperationAttemptFailure.definitiveRejectionWithoutEffect =>
        'definitiveRejection',
      OperationAttemptFailure.sessionExpired => 'sessionExpired',
      OperationAttemptFailure.authorizationDenied => 'authorizationDenied',
      OperationAttemptFailure.serverConflict => 'serverConflict',
      OperationAttemptFailure.appointmentPatientScheduleConflict =>
        'appointmentPatientScheduleConflict',
      OperationAttemptFailure.appointmentStaffScheduleConflict =>
        'appointmentStaffScheduleConflict',
      OperationAttemptFailure.idempotencyKeyPayloadMismatch =>
        'idempotencyKeyPayloadMismatch',
      OperationAttemptFailure.idempotencyOutcomeUnresolved =>
        'idempotencyOutcomeUnresolved',
      OperationAttemptFailure.idempotencyRecordUnresolved =>
        'idempotencyRecordUnresolved',
    };
  }

  static bool _requiresConflictUserIntervention(
    OperationAttemptFailure failure,
  ) {
    return const <OperationAttemptFailure>{
      OperationAttemptFailure.appointmentPatientScheduleConflict,
      OperationAttemptFailure.appointmentStaffScheduleConflict,
      OperationAttemptFailure.idempotencyKeyPayloadMismatch,
      OperationAttemptFailure.idempotencyRecordUnresolved,
    }.contains(failure);
  }

  static OperationReplayDisposition _dispositionFor(String state) {
    return switch (state) {
      'acknowledged' => OperationReplayDisposition.acknowledged,
      'retryScheduled' => OperationReplayDisposition.retryScheduled,
      'reconciliationRequired' =>
        OperationReplayDisposition.reconciliationRequired,
      'rejected' => OperationReplayDisposition.rejected,
      'userInterventionRequired' =>
        OperationReplayDisposition.userInterventionRequired,
      _ => OperationReplayDisposition.blockedInvalidPayload,
    };
  }

  static void _validateAppointmentPayload(Map<String, dynamic> payload) {
    const allowedKeys = <String>{
      'patient_id',
      'staff_id',
      'scheduled_start',
      'scheduled_end',
      'appointment_type',
      'reason',
      'notes',
    };

    if (payload.keys.any((key) => !allowedKeys.contains(key))) {
      throw const FormatException('Unexpected appointment payload field.');
    }

    final patientId = payload['patient_id'];
    final staffId = payload['staff_id'];

    if (patientId is! int || patientId <= 0) {
      throw const FormatException('Invalid patient_id.');
    }

    if (staffId is! int || staffId <= 0) {
      throw const FormatException('Invalid staff_id.');
    }

    final startValue = payload['scheduled_start'];
    final endValue = payload['scheduled_end'];

    if (startValue is! String || endValue is! String) {
      throw const FormatException('Invalid appointment schedule.');
    }

    final offsetSuffix = RegExp(r'(?:[zZ]|[+-]\d{2}:?\d{2})$');

    if (!offsetSuffix.hasMatch(startValue) ||
        !offsetSuffix.hasMatch(endValue)) {
      throw const FormatException('Schedule timestamps need explicit offsets.');
    }

    final start = DateTime.tryParse(startValue);
    final end = DateTime.tryParse(endValue);

    if (start == null || end == null || !start.isBefore(end)) {
      throw const FormatException('Invalid appointment time range.');
    }

    if (payload.containsKey('appointment_type')) {
      final type = payload['appointment_type'];

      if (type is! String || type.trim().isEmpty || type.trim() != type) {
        throw const FormatException('Invalid appointment_type.');
      }
    }

    _validateOptionalText(payload, 'reason', maximumLength: 255);
    _validateOptionalText(payload, 'notes', maximumLength: 2000);
  }

  static void _validateOptionalText(
    Map<String, dynamic> payload,
    String field, {
    required int maximumLength,
  }) {
    if (!payload.containsKey(field) || payload[field] == null) return;

    final value = payload[field];

    if (value is! String ||
        value.trim().isEmpty ||
        value.trim() != value ||
        value.length > maximumLength) {
      throw FormatException('Invalid $field.');
    }
  }
}
