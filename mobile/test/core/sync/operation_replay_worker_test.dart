import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_lifecycle.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_worker.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

final _now = DateTime.utc(2026, 10, 15, 10);

final _context = OperationPayloadContext(userId: '7', clinicContextId: '3');

const _session = AuthTokenResponse(
  accessToken: 'a.b.c',
  refreshToken: 'd.e.f',
  userId: 7,
  role: 'RECEPTIONIST',
  clinicContextId: 3,
);

const _appointmentPayload = <String, dynamic>{
  'patient_id': 15,
  'staff_id': 17,
  'scheduled_start': '2026-10-16T12:00:00Z',
  'scheduled_end': '2026-10-16T12:30:00Z',
  'appointment_type': 'IN_PERSON',
  'reason': 'Follow-up',
};

class _MemorySecureStorage implements SecureKeyValueStorage {
  final Map<String, String> values = <String, String>{};

  @override
  Future<String?> read({required String key}) async => values[key];

  @override
  Future<void> write({required String key, required String value}) async {
    values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    values.remove(key);
  }
}

void main() {
  late AppDatabase database;
  late OperationQueueStore queueStore;

  setUp(() {
    database = AppDatabase(NativeDatabase.memory());

    final crypto = OperationPayloadCrypto(
      secureStorage: _MemorySecureStorage(),
    );

    queueStore = OperationQueueStore(database, payloadCrypto: crypto);
  });

  tearDown(() async {
    await database.close();
  });

  OperationReplayWorker worker({
    required OperationReplayAppointmentSender sender,
    AuthTokenResponse? session = _session,
    DateTime Function()? clock,
  }) {
    return OperationReplayWorker(
      queueStore: queueStore,
      loadSession: () async => session,
      sendAppointmentCreate: sender,
      clock: clock ?? () => _now,
    );
  }

  Future<void> enqueue({
    String operationId = 'appointment-1',
    String operationType = OperationReplayWorker.appointmentCreateType,
    String? idempotencyKey,
    Map<String, dynamic> payload = _appointmentPayload,
  }) async {
    final result = await queueStore.enqueueEncrypted(
      OperationIdentity(
        operationId: operationId,
        operationType: operationType,
        idempotencyKey: idempotencyKey ?? '$operationId-key',
      ),
      context: _context,
      payload: payload,
      now: _now,
    );

    expect(result, OperationQueueEnqueueResult.inserted);
  }

  Future<void> markForReconciliationRequired({
    String operationId = 'appointment-1',
  }) async {
    await queueStore.transition(
      operationId: operationId,
      expectedState: OperationLifecycleState.queued,
      nextState: OperationLifecycleState.inFlight,
      context: _context,
      now: _now,
    );

    await queueStore.recordFailure(
      operationId: operationId,
      failure: OperationAttemptFailure.serverConflict,
      now: _now,
      replayContract:
          OperationReplayContract.endpointSupportsStableIdempotencyKey,
      jitterSample: 0.5,
    );

    final entry = await queueStore.findByOperationId(operationId);
    expect(
      entry?.lifecycleState,
      OperationLifecycleState.reconciliationRequired.name,
    );
  }

  ApiException httpFailure(int statusCode, {String? serverCode}) {
    return ApiException(
      kind: ApiFailureKind.httpStatus,
      message: 'Test HTTP failure',
      uri: Uri.parse('https://example.test/api/v1/appointments/'),
      statusCode: statusCode,
      serverCode: serverCode,
    );
  }

  Future<OperationReplayRunResult> replayConflict(String? serverCode) async {
    await enqueue();

    return worker(
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            throw httpFailure(409, serverCode: serverCode);
          },
    ).runOnce();
  }

  Future<void> expectConflictNeedsUserIntervention({
    required String serverCode,
    required String expectedCategory,
  }) async {
    final result = await replayConflict(serverCode);

    expect(result.userInterventionRequired, 1);
    expect(result.reconciliationRequired, 0);
    expect(result.items.single.category, expectedCategory);

    final entry = await queueStore.findByOperationId('appointment-1');

    expect(
      entry?.lifecycleState,
      OperationLifecycleState.userInterventionRequired.name,
    );
    expect(entry?.nextAttemptAt, isNull);
    expect(entry?.idempotencyKey, 'appointment-1-key');
    expect(
      await queueStore.loadPayload(
        operationId: 'appointment-1',
        context: _context,
      ),
      _appointmentPayload,
    );

    // The operation must not be retried automatically from this state.
    var repeatedCalls = 0;

    final repeated = await worker(
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            repeatedCalls++;
            return <String, dynamic>{'success': true};
          },
    ).runOnce();

    expect(repeatedCalls, 0);
    expect(repeated.items, isEmpty);
  }

  test('explicit reconciliation reuses the original key and payload', () async {
    await enqueue();
    await markForReconciliationRequired();

    final requests = <Map<String, dynamic>>[];

    final result = await worker(
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            requests.add(<String, dynamic>{
              'path': path,
              'body': body,
              'bearerToken': bearerToken,
              'idempotencyKey': idempotencyKey,
            });
            return <String, dynamic>{'success': true};
          },
    ).reconcileAppointmentCreate('appointment-1');

    expect(result.disposition, OperationReconciliationDisposition.acknowledged);
    expect(requests, hasLength(1));
    expect(
      requests.single['path'],
      OperationReplayWorker.appointmentCreatePath,
    );
    expect(requests.single['body'], _appointmentPayload);
    expect(requests.single['bearerToken'], _session.accessToken);
    expect(requests.single['idempotencyKey'], 'appointment-1-key');

    final entry = await queueStore.findByOperationId('appointment-1');
    expect(entry?.lifecycleState, OperationLifecycleState.acknowledged.name);
  });

  test('continuing HTTP 409 leaves reconciliation unresolved', () async {
    await enqueue();
    await markForReconciliationRequired();

    var calls = 0;
    final result = await worker(
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            calls++;
            throw httpFailure(409);
          },
    ).reconcileAppointmentCreate('appointment-1');

    expect(result.disposition, OperationReconciliationDisposition.unresolved);
    expect(result.category, 'serverConflict');
    expect(calls, 1);

    final entry = await queueStore.findByOperationId('appointment-1');
    expect(
      entry?.lifecycleState,
      OperationLifecycleState.reconciliationRequired.name,
    );
  });

  test('explicit reconciliation persists a recognized conflict as user intervention', () async {
    await enqueue();
    await markForReconciliationRequired();

    var calls = 0;

    final result = await worker(
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            calls++;
            throw httpFailure(
              409,
              serverCode: 'APPOINTMENT_PATIENT_SCHEDULE_CONFLICT',
            );
          },
    ).reconcileAppointmentCreate('appointment-1');

    expect(
      result.disposition,
      OperationReconciliationDisposition.userInterventionRequired,
    );
    expect(result.category, 'appointmentPatientScheduleConflict');
    expect(calls, 1);

    final entry = await queueStore.findByOperationId('appointment-1');

    expect(
      entry?.lifecycleState,
      OperationLifecycleState.userInterventionRequired.name,
    );
    expect(entry?.idempotencyKey, 'appointment-1-key');
    expect(
      await queueStore.loadPayload(
        operationId: 'appointment-1',
        context: _context,
      ),
      _appointmentPayload,
    );
  });

  test('reconciliation refuses a different authenticated owner', () async {
    await enqueue();
    await markForReconciliationRequired();

    const otherSession = AuthTokenResponse(
      accessToken: 'x.y.z',
      refreshToken: 'r.s.t',
      userId: 8,
      role: 'RECEPTIONIST',
      clinicContextId: 3,
    );

    var calls = 0;
    final result = await worker(
      session: otherSession,
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            calls++;
            return <String, dynamic>{'success': true};
          },
    ).reconcileAppointmentCreate('appointment-1');

    expect(
      result.disposition,
      OperationReconciliationDisposition.contextMismatch,
    );
    expect(calls, 0);

    final entry = await queueStore.findByOperationId('appointment-1');
    expect(
      entry?.lifecycleState,
      OperationLifecycleState.reconciliationRequired.name,
    );
  });

  test('reconciliation cannot send an ordinary queued operation', () async {
    await enqueue();

    var calls = 0;
    final result = await worker(
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            calls++;
            return <String, dynamic>{'success': true};
          },
    ).reconcileAppointmentCreate('appointment-1');

    expect(
      result.disposition,
      OperationReconciliationDisposition.notReconciliationRequired,
    );
    expect(calls, 0);

    final entry = await queueStore.findByOperationId('appointment-1');
    expect(entry?.lifecycleState, OperationLifecycleState.queued.name);
  });
  test('sends appointment with original body and idempotency key', () async {
    await enqueue();

    final requests = <Map<String, dynamic>>[];

    final result = await worker(
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            requests.add({
              'path': path,
              'body': body,
              'bearerToken': bearerToken,
              'idempotencyKey': idempotencyKey,
            });

            return {'success': true};
          },
    ).runOnce();

    expect(requests, hasLength(1));
    expect(requests.single['path'], 'api/v1/appointments/');
    expect(requests.single['body'], _appointmentPayload);
    expect(requests.single['bearerToken'], 'a.b.c');
    expect(requests.single['idempotencyKey'], 'appointment-1-key');
    expect(result.acknowledged, 1);

    expect(
      (await queueStore.findByOperationId('appointment-1'))?.lifecycleState,
      OperationLifecycleState.acknowledged.name,
    );
  });

  test('retries unknown outcome with the same key and payload', () async {
    await enqueue();

    var now = _now;
    var calls = 0;

    final keys = <String>[];
    final bodies = <Map<String, dynamic>>[];

    OperationReplayWorker makeWorker() => worker(
      clock: () => now,
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            calls++;
            keys.add(idempotencyKey);
            bodies.add(Map<String, dynamic>.from(body));

            if (calls == 1) {
              throw ApiException(
                kind: ApiFailureKind.connection,
                message: 'Simulated connection loss',
                uri: Uri.parse('https://example.test/api/v1/appointments/'),
              );
            }

            return {'success': true};
          },
    );

    final first = await makeWorker().runOnce();

    expect(first.retryScheduled, 1);

    final failed = await queueStore.findByOperationId('appointment-1');

    expect(failed?.lifecycleState, OperationLifecycleState.retryScheduled.name);
    expect(failed?.idempotencyKey, 'appointment-1-key');

    now = now.add(const Duration(days: 1));

    final second = await makeWorker().runOnce();

    expect(second.acknowledged, 1);
    expect(calls, 2);
    expect(keys, ['appointment-1-key', 'appointment-1-key']);
    expect(bodies, [_appointmentPayload, _appointmentPayload]);
  });

  test('routes HTTP 409 to reconciliation', () async {
    await enqueue();

    final result = await worker(
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            throw httpFailure(409);
          },
    ).runOnce();

    expect(result.reconciliationRequired, 1);
    expect(result.items.single.category, 'serverConflict');

    expect(
      (await queueStore.findByOperationId('appointment-1'))?.lifecycleState,
      OperationLifecycleState.reconciliationRequired.name,
    );
  });

  test(
    'patient schedule conflict requires intervention and preserves the payload',
    () async {
      await expectConflictNeedsUserIntervention(
        serverCode: 'APPOINTMENT_PATIENT_SCHEDULE_CONFLICT',
        expectedCategory: 'appointmentPatientScheduleConflict',
      );
    },
  );

  test(
    'staff schedule conflict requires intervention and preserves the payload',
    () async {
      await expectConflictNeedsUserIntervention(
        serverCode: 'APPOINTMENT_STAFF_SCHEDULE_CONFLICT',
        expectedCategory: 'appointmentStaffScheduleConflict',
      );
    },
  );

  test('idempotency payload mismatch requires intervention without rewriting payload', () async {
    await expectConflictNeedsUserIntervention(
      serverCode: 'IDEMPOTENCY_KEY_PAYLOAD_MISMATCH',
      expectedCategory: 'idempotencyKeyPayloadMismatch',
    );
  });

  test('unresolved idempotency record requires user intervention', () async {
    await expectConflictNeedsUserIntervention(
      serverCode: 'IDEMPOTENCY_RECORD_UNRESOLVED',
      expectedCategory: 'idempotencyRecordUnresolved',
    );
  });

  test('unresolved idempotency outcome remains in reconciliation', () async {
    final result = await replayConflict('IDEMPOTENCY_OUTCOME_UNRESOLVED');

    expect(result.reconciliationRequired, 1);
    expect(result.userInterventionRequired, 0);
    expect(result.items.single.category, 'idempotencyOutcomeUnresolved');

    final entry = await queueStore.findByOperationId('appointment-1');

    expect(
      entry?.lifecycleState,
      OperationLifecycleState.reconciliationRequired.name,
    );
    expect(entry?.nextAttemptAt, isNull);
    expect(entry?.idempotencyKey, 'appointment-1-key');
    expect(
      await queueStore.loadPayload(
        operationId: 'appointment-1',
        context: _context,
      ),
      _appointmentPayload,
    );
  });

  test(
    'unknown HTTP 409 codes retain generic reconciliation behavior',
    () async {
      final result = await replayConflict('FUTURE_UNRECOGNIZED_CONFLICT');

      expect(result.reconciliationRequired, 1);
      expect(result.items.single.category, 'serverConflict');

      expect(
        (await queueStore.findByOperationId('appointment-1'))?.lifecycleState,
        OperationLifecycleState.reconciliationRequired.name,
      );
    },
  );

  test('requires user intervention after HTTP 401', () async {
    await enqueue();

    final result = await worker(
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            throw httpFailure(401);
          },
    ).runOnce();

    expect(result.userInterventionRequired, 1);

    expect(
      (await queueStore.findByOperationId('appointment-1'))?.lifecycleState,
      OperationLifecycleState.userInterventionRequired.name,
    );
  });

  test('does not automatically replay patient mutations', () async {
    await enqueue(operationId: 'patient-1', operationType: 'patient.create');

    var sent = false;

    await worker(
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            sent = true;
            return {'success': true};
          },
    ).runOnce();

    expect(sent, isFalse);

    expect(
      (await queueStore.findByOperationId('patient-1'))?.lifecycleState,
      OperationLifecycleState.queued.name,
    );
  });

  test(
    'recovers interrupted attempts using the original idempotency key',
    () async {
      await enqueue();

      await queueStore.transition(
        operationId: 'appointment-1',
        expectedState: OperationLifecycleState.queued,
        nextState: OperationLifecycleState.inFlight,
        context: _context,
        now: _now,
      );

      var now = _now;
      var calls = 0;

      final keys = <String>[];

      OperationReplayWorker makeWorker() => worker(
        clock: () => now,
        sender:
            ({
              required String path,
              required Map<String, dynamic> body,
              required String bearerToken,
              required String idempotencyKey,
            }) async {
              calls++;
              keys.add(idempotencyKey);
              return {'success': true};
            },
      );

      final recovered = await makeWorker().runOnce();

      expect(recovered.retryScheduled, 1);
      expect(calls, 0);

      now = now.add(const Duration(days: 1));

      final replayed = await makeWorker().runOnce();

      expect(replayed.acknowledged, 1);
      expect(calls, 1);
      expect(keys, ['appointment-1-key']);
    },
  );

  test('does not send without an active clinic context', () async {
    await enqueue();

    final result = await worker(
      session: const AuthTokenResponse(
        accessToken: 'a.b.c',
        refreshToken: 'd.e.f',
        userId: 7,
        role: 'RECEPTIONIST',
        clinicContextId: null,
      ),
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            fail('Must not send without an active clinic context.');
          },
    ).runOnce();

    expect(result.blockedReason, 'clinicContextUnavailable');

    expect(
      (await queueStore.findByOperationId('appointment-1'))?.lifecycleState,
      OperationLifecycleState.queued.name,
    );
  });

  test('blocks invalid appointment payloads before sending', () async {
    await enqueue(payload: {..._appointmentPayload, 'staff_id': 'invalid-id'});

    var sent = false;

    final result = await worker(
      sender:
          ({
            required String path,
            required Map<String, dynamic> body,
            required String bearerToken,
            required String idempotencyKey,
          }) async {
            sent = true;
            return {'success': true};
          },
    ).runOnce();

    expect(sent, isFalse);

    expect(
      result.items.single.disposition,
      OperationReplayDisposition.blockedInvalidPayload,
    );
  });
}
