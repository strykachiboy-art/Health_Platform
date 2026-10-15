import '../networking/api_client.dart';
import '../networking/api_exception.dart';
import '../../features/auth/data/models/auth_token_response.dart';
import 'operation_lifecycle.dart';
import 'operation_payload_crypto.dart';
import 'operation_queue_store.dart';

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

/// Replays only appointment creation, the mutation whose backend stable-key
/// idempotency contract has been verified.
///
/// The caller supplies the current session when each run begins. Bearer
/// tokens are not persisted in the operation queue.
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
    if (status == 409) return OperationAttemptFailure.serverConflict;

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
    };
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
