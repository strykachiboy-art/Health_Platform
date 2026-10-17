import 'dart:math';

import 'package:flutter/widgets.dart';

import 'operation_lifecycle.dart';
import 'operation_payload_crypto.dart';
import 'operation_queue_store.dart';
import 'operation_replay_scheduler.dart';
import 'operation_replay_worker.dart';

/// A single logical appointment-create intent.
///
/// Reuse the same command instance when retrying local enqueue. Its operation
/// ID and idempotency key are created once, not once per enqueue attempt.
class AppointmentCreateCommand {
  AppointmentCreateCommand({
    required this.patientId,
    required this.staffId,
    required this.scheduledStart,
    required this.scheduledEnd,
    String? appointmentType,
    String? reason,
    String? notes,
    String? operationId,
    String? idempotencyKey,
  }) : operationId = _normalizeIdentifier(
         operationId ?? _newToken('operation_'),
         'operationId',
         128,
       ),
       idempotencyKey = _normalizeIdentifier(
         idempotencyKey ?? _newToken('appointment_'),
         'idempotencyKey',
         255,
       ),
       appointmentType = _normalizeOptionalText(
         appointmentType,
         'appointmentType',
         120,
       ),
       reason = _normalizeOptionalText(reason, 'reason', 255),
       notes = _normalizeOptionalText(notes, 'notes', 2000) {
    if (patientId <= 0) {
      throw ArgumentError.value(patientId, 'patientId', 'Must be positive.');
    }
    if (staffId <= 0) {
      throw ArgumentError.value(staffId, 'staffId', 'Must be positive.');
    }
    if (!scheduledStart.isBefore(scheduledEnd)) {
      throw ArgumentError('scheduledStart must be before scheduledEnd.');
    }
  }

  static final Random _secureRandom = Random.secure();

  final int patientId;
  final int staffId;
  final DateTime scheduledStart;
  final DateTime scheduledEnd;
  final String? appointmentType;
  final String? reason;
  final String? notes;

  /// Stable local queue identity for this logical appointment request.
  final String operationId;

  /// Stable backend idempotency key for every delivery attempt.
  final String idempotencyKey;

  /// Produces only fields allowed by the verified appointment-create contract.
  ///
  /// Timestamps are normalized to UTC so each has an explicit offset.
  Map<String, dynamic> toPayload() {
    final payload = <String, dynamic>{
      'patient_id': patientId,
      'staff_id': staffId,
      'scheduled_start': scheduledStart.toUtc().toIso8601String(),
      'scheduled_end': scheduledEnd.toUtc().toIso8601String(),
    };

    if (appointmentType != null) {
      payload['appointment_type'] = appointmentType;
    }
    // Deliberately omit optional clinical free-text from persisted
    // offline payloads by default. Future inclusion requires explicit
    // design approval and a reviewed protection/retention/deletion policy.

    return payload;
  }

  static String _newToken(String prefix) {
    final bytes = List<int>.generate(
      32,
      (_) => _secureRandom.nextInt(256),
      growable: false,
    );
    final hex = bytes
        .map((value) => value.toRadixString(16).padLeft(2, '0'))
        .join();
    return '$prefix$hex';
  }

  static String _normalizeIdentifier(
    String value,
    String field,
    int maximumLength,
  ) {
    final normalized = value.trim();
    if (normalized.isEmpty || normalized.length > maximumLength) {
      throw ArgumentError.value(value, field, 'Invalid identifier.');
    }
    return normalized;
  }

  static String? _normalizeOptionalText(
    String? value,
    String field,
    int maximumLength,
  ) {
    if (value == null) return null;

    final normalized = value.trim();
    if (normalized.isEmpty) return null;
    if (normalized.length > maximumLength) {
      throw ArgumentError.value(value, field, 'Text is too long.');
    }
    return normalized;
  }
}

enum AppointmentCreateEnqueueStatus {
  queued,
  alreadyQueued,
  sessionUnavailable,
  conflict,
}

class AppointmentCreateEnqueueResult {
  const AppointmentCreateEnqueueResult({
    required this.status,
    required this.operationId,
    required this.idempotencyKey,
    this.persistenceResult,
  });

  final AppointmentCreateEnqueueStatus status;
  final String operationId;
  final String idempotencyKey;
  final OperationQueueEnqueueResult? persistenceResult;
}

/// Application entry point for durable, encrypted appointment creation.
///
/// A current authenticated owner and active clinic must be supplied by the
/// session layer. No bearer token is written to the queue. Replays use the
/// worker's endpoint-specific idempotency contract.
class AppointmentCreateQueueProducer {
  factory AppointmentCreateQueueProducer({
    required OperationQueueStore queueStore,
    required OperationReplayScheduler replayScheduler,
    required Future<OperationPayloadContext?> Function() loadContext,
    DateTime Function()? clock,
  }) {
    return AppointmentCreateQueueProducer._(
      queueStore,
      replayScheduler,
      loadContext,
      clock: clock,
    );
  }

  AppointmentCreateQueueProducer._(
    this._queueStore,
    this._replayScheduler,
    this._loadContext, {
    DateTime Function()? clock,
  }) : _clock = clock ?? DateTime.now;

  final OperationQueueStore _queueStore;
  final OperationReplayScheduler _replayScheduler;
  final Future<OperationPayloadContext?> Function() _loadContext;
  final DateTime Function() _clock;

  Future<AppointmentCreateEnqueueResult> enqueue(
    AppointmentCreateCommand command,
  ) async {
    final OperationPayloadContext? context;

    try {
      context = await _loadContext();
    } catch (_) {
      return _result(
        command,
        AppointmentCreateEnqueueStatus.sessionUnavailable,
      );
    }

    if (context == null) {
      return _result(
        command,
        AppointmentCreateEnqueueStatus.sessionUnavailable,
      );
    }

    final identity = OperationIdentity(
      operationId: command.operationId,
      operationType: OperationReplayWorker.appointmentCreateType,
      idempotencyKey: command.idempotencyKey,
    );

    // Encrypted payload and metadata are written atomically by the store.
    // Storage or cryptographic failures propagate; they are not reported as
    // queued, and no plaintext fallback is attempted.
    final persisted = await _queueStore.enqueueEncrypted(
      identity,
      context: context,
      payload: command.toPayload(),
      now: _clock(),
    );

    final AppointmentCreateEnqueueStatus status;
    if (persisted == OperationQueueEnqueueResult.inserted ||
        persisted == OperationQueueEnqueueResult.payloadAttached) {
      status = AppointmentCreateEnqueueStatus.queued;
    } else if (persisted == OperationQueueEnqueueResult.alreadyPresent) {
      status = AppointmentCreateEnqueueStatus.alreadyQueued;
    } else {
      status = AppointmentCreateEnqueueStatus.conflict;
    }

    if (status == AppointmentCreateEnqueueStatus.queued ||
        status == AppointmentCreateEnqueueStatus.alreadyQueued) {
      _replayScheduler.notifyOperationQueued();
    }

    return AppointmentCreateEnqueueResult(
      status: status,
      operationId: command.operationId,
      idempotencyKey: command.idempotencyKey,
      persistenceResult: persisted,
    );
  }

  AppointmentCreateEnqueueResult _result(
    AppointmentCreateCommand command,
    AppointmentCreateEnqueueStatus status,
  ) {
    return AppointmentCreateEnqueueResult(
      status: status,
      operationId: command.operationId,
      idempotencyKey: command.idempotencyKey,
    );
  }
}

/// Makes the appointment producer available to feature routes via
/// `AppointmentCreateQueueScope.of(context).producer`.
class AppointmentCreateQueueScope extends InheritedWidget {
  const AppointmentCreateQueueScope({
    super.key,
    required this.producer,
    required this.reconcileOperation,
    required super.child,
  });

  final AppointmentCreateQueueProducer producer;

  final OperationReconciliationRunner reconcileOperation;

  /// Makes an explicit reconciliation attempt for a pending operation.
  Future<OperationReconciliationResult> reconcile(String operationId) {
    return reconcileOperation(operationId);
  }

  static AppointmentCreateQueueScope of(BuildContext context) {
    final scope = context
        .dependOnInheritedWidgetOfExactType<AppointmentCreateQueueScope>();

    if (scope == null) {
      throw FlutterError(
        'AppointmentCreateQueueScope is missing above this context.',
      );
    }

    return scope;
  }

  @override
  bool updateShouldNotify(AppointmentCreateQueueScope oldWidget) {
    return !identical(producer, oldWidget.producer) ||
        reconcileOperation != oldWidget.reconcileOperation;
  }
}
