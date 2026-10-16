import 'dart:io';

import 'package:drift/drift.dart' show driftRuntimeOptions;
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_lifecycle.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';

final _now = DateTime.utc(2026, 10, 15, 10);

OperationIdentity _identity({
  required String operationId,
  String? operationType,
  String? idempotencyKey,
}) {
  return OperationIdentity(
    operationId: operationId,
    operationType: operationType ?? 'test.create',
    idempotencyKey: idempotencyKey ?? 'key-$operationId',
  );
}

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

OperationPayloadContext _testContext({
  String userId = 'test-user',
  String clinicContextId = 'test-clinic',
}) {
  return OperationPayloadContext(
    userId: userId,
    clinicContextId: clinicContextId,
  );
}

Map<String, dynamic> _testPayload() => <String, dynamic>{
  'test_request_value': 'non-sensitive test fixture',
};

Future<OperationQueueEnqueueResult> _enqueueEncrypted(
  OperationQueueStore store,
  OperationIdentity identity, {
  DateTime? now,
}) {
  return store.enqueueEncrypted(
    identity,
    context: _testContext(),
    payload: _testPayload(),
    now: now,
  );
}

void main() {
  driftRuntimeOptions.dontWarnAboutMultipleDatabases = true;
  late AppDatabase database;
  late OperationQueueStore store;
  late _MemorySecureStorage secureStorage;
  late OperationPayloadCrypto payloadCrypto;

  setUp(() {
    database = AppDatabase(NativeDatabase.memory());
    secureStorage = _MemorySecureStorage();
    payloadCrypto = OperationPayloadCrypto(secureStorage: secureStorage);
    store = OperationQueueStore(database, payloadCrypto: payloadCrypto);
  });

  tearDown(() async {
    await database.close();
  });

  group('OperationQueueStore.enqueue', () {
    test('inserts one logical operation with stable identity', () async {
      final identity = _identity(operationId: 'op-001');

      expect(
        await _enqueueEncrypted(store, identity, now: _now),
        OperationQueueEnqueueResult.inserted,
      );

      final row = await store.findByOperationId('op-001');

      expect(row, isNotNull);
      expect(row!.operationType, 'test.create');
      expect(row.idempotencyKey, 'key-op-001');
      expect(row.lifecycleState, OperationLifecycleState.queued.name);
      expect(row.attemptsCompleted, 0);
      expect(row.createdAt.toUtc(), _now);
      expect(row.updatedAt.toUtc(), _now);
      expect(row.nextAttemptAt, isNull);
      expect(row.lastFailureCategory, isNull);
    });

    test('re-enqueue of the identical identity does not reset state', () async {
      final identity = _identity(operationId: 'op-002');

      await _enqueueEncrypted(store, identity, now: _now);
      await store.transition(
        operationId: 'op-002',
        expectedState: OperationLifecycleState.queued,
        nextState: OperationLifecycleState.inFlight,
        context: _testContext(),
        now: _now,
      );

      expect(
        await _enqueueEncrypted(
          store,
          identity,
          now: _now.add(const Duration(minutes: 2)),
        ),
        OperationQueueEnqueueResult.alreadyPresent,
      );

      final row = await store.findByOperationId('op-002');

      expect(row!.lifecycleState, OperationLifecycleState.inFlight.name);
      expect(row.attemptsCompleted, 1);
      expect(row.createdAt.toUtc(), _now);
    });

    test('rejects reuse of an operation ID for different identity', () async {
      await _enqueueEncrypted(
        store,
        _identity(operationId: 'op-003'),
        now: _now,
      );

      expect(
        await _enqueueEncrypted(
          store,
          _identity(
            operationId: 'op-003',
            operationType: 'test.update',
            idempotencyKey: 'different-key',
          ),
          now: _now,
        ),
        OperationQueueEnqueueResult.operationIdConflict,
      );

      final rows = await database.select(database.operationQueueEntry).get();
      expect(rows, hasLength(1));
      expect(rows.single.operationType, 'test.create');
      expect(rows.single.idempotencyKey, 'key-op-003');
    });

    test('rejects reuse of an idempotency key for another operation', () async {
      await _enqueueEncrypted(
        store,
        _identity(operationId: 'op-004', idempotencyKey: 'shared-key'),
        now: _now,
      );

      expect(
        await _enqueueEncrypted(
          store,
          _identity(operationId: 'op-005', idempotencyKey: 'shared-key'),
          now: _now,
        ),
        OperationQueueEnqueueResult.idempotencyKeyConflict,
      );

      final rows = await database.select(database.operationQueueEntry).get();
      expect(rows, hasLength(1));
      expect(rows.single.operationId, 'op-004');
    });

    test('rejects invalid ready-item limits', () {
      expect(
        () => store.listReady(context: _testContext(), limit: 0),
        throwsArgumentError,
      );
      expect(
        () => store.listReady(context: _testContext(), limit: 101),
        throwsArgumentError,
      );
    });
  });

  group('encrypted payload delivery eligibility', () {
    test(
      'metadata-only operations are neither ready nor allowed inFlight',
      () async {
        const operationId = 'metadata-only';
        await store.enqueue(_identity(operationId: operationId), now: _now);

        expect(
          await store.listReady(context: _testContext(), now: _now),
          isEmpty,
        );

        await expectLater(
          store.transition(
            operationId: operationId,
            expectedState: OperationLifecycleState.queued,
            nextState: OperationLifecycleState.inFlight,
            context: _testContext(),
            now: _now,
          ),
          throwsA(isA<StateError>()),
        );
      },
    );

    test(
      'ready listing requires payload and exact active user/clinic',
      () async {
        const operationId = 'scoped-payload';
        await _enqueueEncrypted(
          store,
          _identity(operationId: operationId),
          now: _now,
        );

        final ready = await store.listReady(context: _testContext(), now: _now);
        expect(ready.map((row) => row.operationId), [operationId]);

        expect(
          await store.listReady(
            context: _testContext(clinicContextId: 'another-clinic'),
            now: _now,
          ),
          isEmpty,
        );
        expect(
          await store.listReady(
            context: _testContext(userId: 'another-user'),
            now: _now,
          ),
          isEmpty,
        );

        expect(
          await store.loadPayload(
            operationId: operationId,
            context: _testContext(),
          ),
          _testPayload(),
        );

        await expectLater(
          store.loadPayload(
            operationId: operationId,
            context: _testContext(clinicContextId: 'another-clinic'),
          ),
          throwsA(isA<StateError>()),
        );
      },
    );

    test(
      're-enqueue rejects a changed payload instead of replacing it',
      () async {
        final identity = _identity(operationId: 'immutable-payload');
        final firstPayload = <String, dynamic>{'quantity': 1};
        final changedPayload = <String, dynamic>{'quantity': 2};

        expect(
          await store.enqueueEncrypted(
            identity,
            context: _testContext(),
            payload: firstPayload,
            now: _now,
          ),
          OperationQueueEnqueueResult.inserted,
        );

        expect(
          await store.enqueueEncrypted(
            identity,
            context: _testContext(),
            payload: firstPayload,
            now: _now,
          ),
          OperationQueueEnqueueResult.alreadyPresent,
        );

        expect(
          await store.enqueueEncrypted(
            identity,
            context: _testContext(),
            payload: changedPayload,
            now: _now,
          ),
          OperationQueueEnqueueResult.payloadConflict,
        );

        expect(
          await store.loadPayload(
            operationId: identity.operationId,
            context: _testContext(),
          ),
          firstPayload,
        );
      },
    );
  });
  group('OperationQueueStore.transition', () {
    test('increments attempt count when entering in-flight', () async {
      await _enqueueEncrypted(
        store,
        _identity(operationId: 'op-010'),
        now: _now,
      );

      final row = await store.transition(
        operationId: 'op-010',
        expectedState: OperationLifecycleState.queued,
        nextState: OperationLifecycleState.inFlight,
        context: _testContext(),
        now: _now,
      );

      expect(row.lifecycleState, OperationLifecycleState.inFlight.name);
      expect(row.attemptsCompleted, 1);
    });

    test('rejects illegal transitions and stale expected states', () async {
      await _enqueueEncrypted(
        store,
        _identity(operationId: 'op-011'),
        now: _now,
      );

      await expectLater(
        store.transition(
          operationId: 'op-011',
          expectedState: OperationLifecycleState.queued,
          nextState: OperationLifecycleState.acknowledged,
          now: _now,
        ),
        throwsStateError,
      );

      await store.transition(
        operationId: 'op-011',
        expectedState: OperationLifecycleState.queued,
        nextState: OperationLifecycleState.inFlight,
        context: _testContext(),
        now: _now,
      );

      await expectLater(
        store.transition(
          operationId: 'op-011',
          expectedState: OperationLifecycleState.queued,
          nextState: OperationLifecycleState.cancelled,
          now: _now,
        ),
        throwsStateError,
      );
    });

    test('requires confirmation before resuming reconciliation', () async {
      await _enqueueEncrypted(
        store,
        _identity(operationId: 'op-012'),
        now: _now,
      );
      await store.transition(
        operationId: 'op-012',
        expectedState: OperationLifecycleState.queued,
        nextState: OperationLifecycleState.inFlight,
        context: _testContext(),
        now: _now,
      );

      await store.recordFailure(
        operationId: 'op-012',
        failure: OperationAttemptFailure.outcomeUnknown,
        now: _now,
      );

      await expectLater(
        store.transition(
          operationId: 'op-012',
          expectedState: OperationLifecycleState.reconciliationRequired,
          nextState: OperationLifecycleState.queued,
          now: _now,
        ),
        throwsStateError,
      );

      final resumed = await store.transition(
        operationId: 'op-012',
        expectedState: OperationLifecycleState.reconciliationRequired,
        nextState: OperationLifecycleState.queued,
        now: _now,
        reconciliationConfirmed: true,
      );

      expect(resumed.lifecycleState, OperationLifecycleState.queued.name);
      expect(resumed.lastFailureCategory, isNull);
      expect(resumed.attemptsCompleted, 1);
    });

    test(
      'requires session revalidation before user-intervention resume',
      () async {
        await _enqueueEncrypted(
          store,
          _identity(operationId: 'op-013'),
          now: _now,
        );
        await store.transition(
          operationId: 'op-013',
          expectedState: OperationLifecycleState.queued,
          nextState: OperationLifecycleState.inFlight,
          context: _testContext(),
          now: _now,
        );
        await store.recordFailure(
          operationId: 'op-013',
          failure: OperationAttemptFailure.sessionExpired,
          now: _now,
        );

        await expectLater(
          store.transition(
            operationId: 'op-013',
            expectedState: OperationLifecycleState.userInterventionRequired,
            nextState: OperationLifecycleState.queued,
            now: _now,
          ),
          throwsStateError,
        );

        final resumed = await store.transition(
          operationId: 'op-013',
          expectedState: OperationLifecycleState.userInterventionRequired,
          nextState: OperationLifecycleState.queued,
          now: _now,
          sessionRevalidated: true,
        );

        expect(resumed.lifecycleState, OperationLifecycleState.queued.name);
      },
    );
  });

  group('OperationQueueStore.recordFailure', () {
    Future<void> start(String operationId) async {
      await _enqueueEncrypted(
        store,
        _identity(operationId: operationId),
        now: _now,
      );
      await store.transition(
        operationId: operationId,
        expectedState: OperationLifecycleState.queued,
        nextState: OperationLifecycleState.inFlight,
        context: _testContext(),
        now: _now,
      );
    }

    test('unknown outcome requires reconciliation by default', () async {
      await start('op-020');

      final row = await store.recordFailure(
        operationId: 'op-020',
        failure: OperationAttemptFailure.outcomeUnknown,
        now: _now,
      );

      expect(
        row.lifecycleState,
        OperationLifecycleState.reconciliationRequired.name,
      );
      expect(row.lastFailureCategory, 'outcomeUnknown');
      expect(row.nextAttemptAt, isNull);
    });

    test('confirmed transient failure schedules bounded backoff', () async {
      await start('op-021');

      final row = await store.recordFailure(
        operationId: 'op-021',
        failure: OperationAttemptFailure.confirmedTransientNoEffect,
        now: _now,
        jitterSample: 0.5,
      );

      expect(row.lifecycleState, OperationLifecycleState.retryScheduled.name);
      expect(row.attemptsCompleted, 1);
      expect(row.nextAttemptAt!.toUtc(), _now.add(const Duration(seconds: 1)));
      expect(row.lastFailureCategory, 'confirmedTransientNoEffect');
    });

    test(
      'unknown outcome retries only with declared stable-key support',
      () async {
        await start('op-022');

        final row = await store.recordFailure(
          operationId: 'op-022',
          failure: OperationAttemptFailure.outcomeUnknown,
          now: _now,
          replayContract:
              OperationReplayContract.endpointSupportsStableIdempotencyKey,
        );

        expect(row.lifecycleState, OperationLifecycleState.retryScheduled.name);
        expect(row.idempotencyKey, 'key-op-022');
        expect(
          row.nextAttemptAt!.toUtc(),
          _now.add(const Duration(seconds: 1)),
        );
      },
    );

    test(
      'definitive rejection is never scheduled as an automatic retry',
      () async {
        await start('op-023');

        final row = await store.recordFailure(
          operationId: 'op-023',
          failure: OperationAttemptFailure.definitiveRejectionWithoutEffect,
          now: _now,
        );

        expect(row.lifecycleState, OperationLifecycleState.rejected.name);
        expect(row.nextAttemptAt, isNull);
      },
    );

    test('exhausted retry budget requires user intervention', () async {
      final limitedStore = OperationQueueStore(
        database,
        retryPolicy: OperationRetryPolicy(maxAttempts: 1),
        payloadCrypto: payloadCrypto,
      );

      await _enqueueEncrypted(
        limitedStore,
        _identity(operationId: 'op-024'),
        now: _now,
      );
      await limitedStore.transition(
        operationId: 'op-024',
        expectedState: OperationLifecycleState.queued,
        nextState: OperationLifecycleState.inFlight,
        context: _testContext(),
        now: _now,
      );

      final row = await limitedStore.recordFailure(
        operationId: 'op-024',
        failure: OperationAttemptFailure.confirmedTransientNoEffect,
        now: _now,
      );

      expect(
        row.lifecycleState,
        OperationLifecycleState.userInterventionRequired.name,
      );
      expect(row.attemptsCompleted, 1);
      expect(row.nextAttemptAt, isNull);
    });

    test('rejects a retry time earlier than the required backoff', () async {
      await start('op-025');

      await expectLater(
        store.transition(
          operationId: 'op-025',
          expectedState: OperationLifecycleState.inFlight,
          nextState: OperationLifecycleState.retryScheduled,
          now: _now,
          failure: OperationAttemptFailure.confirmedTransientNoEffect,
          nextAttemptAt: _now,
        ),
        throwsArgumentError,
      );
    });
  });

  test('listReady returns only queued and due retry entries', () async {
    await _enqueueEncrypted(
      store,
      _identity(operationId: 'a-retry'),
      now: _now,
    );
    await store.transition(
      operationId: 'a-retry',
      expectedState: OperationLifecycleState.queued,
      nextState: OperationLifecycleState.inFlight,
      context: _testContext(),
      now: _now,
    );
    await store.recordFailure(
      operationId: 'a-retry',
      failure: OperationAttemptFailure.confirmedTransientNoEffect,
      now: _now,
    );

    await _enqueueEncrypted(
      store,
      _identity(operationId: 'b-ready'),
      now: _now,
    );

    final beforeDue = await store.listReady(context: _testContext(), now: _now);
    expect(beforeDue.map((row) => row.operationId), ['b-ready']);

    final afterDue = await store.listReady(
      context: _testContext(),
      now: _now.add(const Duration(seconds: 1)),
    );
    expect(afterDue.map((row) => row.operationId).toSet(), {
      'a-retry',
      'b-ready',
    });
  });

  test(
    'queue metadata survives closing and reopening a file database',
    () async {
      final directory = await Directory.systemTemp.createTemp(
        'clinic_queue_store_test_',
      );
      final file = File(
        '${directory.path}${Platform.pathSeparator}queue.sqlite',
      );

      final firstDatabase = AppDatabase(NativeDatabase(file));

      try {
        final firstStore = OperationQueueStore(
          firstDatabase,
          payloadCrypto: payloadCrypto,
        );
        await _enqueueEncrypted(
          firstStore,
          _identity(operationId: 'persisted-op'),
          now: _now,
        );
        await firstStore.transition(
          operationId: 'persisted-op',
          expectedState: OperationLifecycleState.queued,
          nextState: OperationLifecycleState.inFlight,
          context: _testContext(),
          now: _now,
        );
        await firstStore.recordFailure(
          operationId: 'persisted-op',
          failure: OperationAttemptFailure.confirmedTransientNoEffect,
          now: _now,
        );
      } finally {
        await firstDatabase.close();
      }

      try {
        final reopenedDatabase = AppDatabase(NativeDatabase(file));

        try {
          final reopenedStore = OperationQueueStore(
            reopenedDatabase,
            payloadCrypto: payloadCrypto,
          );
          final row = await reopenedStore.findByOperationId('persisted-op');

          expect(row, isNotNull);
          expect(row!.idempotencyKey, 'key-persisted-op');
          expect(
            row.lifecycleState,
            OperationLifecycleState.retryScheduled.name,
          );
          expect(row.attemptsCompleted, 1);
          expect(
            row.nextAttemptAt!.toUtc(),
            _now.add(const Duration(seconds: 1)),
          );
        } finally {
          await reopenedDatabase.close();
        }
      } finally {
        await directory.delete(recursive: true);
      }
    },
  );

  group('purgeExpiredTerminalOperations', () {
    test(
      'purges expired terminal entries and their encrypted payloads',
      () async {
        final cutoff = _now.subtract(const Duration(days: 10));
        final expiredAt = cutoff.subtract(const Duration(seconds: 1));

        Future<void> createTerminal(
          String id,
          OperationLifecycleState terminal,
          DateTime timestamp,
        ) async {
          await _enqueueEncrypted(
            store,
            _identity(operationId: id),
            now: timestamp,
          );

          if (terminal == OperationLifecycleState.cancelled) {
            await store.transition(
              operationId: id,
              expectedState: OperationLifecycleState.queued,
              nextState: OperationLifecycleState.cancelled,
              now: timestamp,
            );
            return;
          }

          await store.transition(
            operationId: id,
            expectedState: OperationLifecycleState.queued,
            nextState: OperationLifecycleState.inFlight,
            context: _testContext(),
            now: timestamp,
          );

          if (terminal == OperationLifecycleState.rejected) {
            await store.transition(
              operationId: id,
              expectedState: OperationLifecycleState.inFlight,
              nextState: OperationLifecycleState.rejected,
              failure: OperationAttemptFailure.definitiveRejectionWithoutEffect,
              now: timestamp,
            );
          } else {
            await store.transition(
              operationId: id,
              expectedState: OperationLifecycleState.inFlight,
              nextState: terminal,
              now: timestamp,
            );
          }
        }

        await createTerminal(
          'expired-ack',
          OperationLifecycleState.acknowledged,
          expiredAt,
        );
        await createTerminal(
          'expired-rejected',
          OperationLifecycleState.rejected,
          expiredAt,
        );
        await createTerminal(
          'expired-cancelled',
          OperationLifecycleState.cancelled,
          expiredAt,
        );
        await createTerminal(
          'cutoff-ack',
          OperationLifecycleState.acknowledged,
          cutoff,
        );

        final removed = await store.purgeExpiredTerminalOperations(
          olderThan: cutoff,
        );

        expect(removed, 3);
        final entries = await database
            .select(database.operationQueueEntry)
            .get();
        expect(entries.map((row) => row.operationId).toSet(), {'cutoff-ack'});

        final payloads = await database
            .select(database.operationQueuePayload)
            .get();
        expect(payloads.map((row) => row.operationId).toSet(), {'cutoff-ack'});
      },
    );

    test('preserves every nonterminal state regardless of age', () async {
      final oldAt = _now.subtract(const Duration(days: 30));
      final cutoff = _now.subtract(const Duration(days: 10));

      Future<void> startInFlight(String id) async {
        await _enqueueEncrypted(store, _identity(operationId: id), now: oldAt);
        await store.transition(
          operationId: id,
          expectedState: OperationLifecycleState.queued,
          nextState: OperationLifecycleState.inFlight,
          context: _testContext(),
          now: oldAt,
        );
      }

      await _enqueueEncrypted(
        store,
        _identity(operationId: 'keep-queued'),
        now: oldAt,
      );
      await startInFlight('keep-in-flight');

      await startInFlight('keep-retry');
      await store.recordFailure(
        operationId: 'keep-retry',
        failure: OperationAttemptFailure.confirmedTransientNoEffect,
        now: oldAt,
      );

      await startInFlight('keep-reconciliation');
      await store.recordFailure(
        operationId: 'keep-reconciliation',
        failure: OperationAttemptFailure.outcomeUnknown,
        now: oldAt,
      );

      await startInFlight('keep-intervention');
      await store.recordFailure(
        operationId: 'keep-intervention',
        failure: OperationAttemptFailure.sessionExpired,
        now: oldAt,
      );

      final removed = await store.purgeExpiredTerminalOperations(
        olderThan: cutoff,
      );

      expect(removed, 0);
      final entries = await database.select(database.operationQueueEntry).get();
      expect(entries, hasLength(5));
      expect(entries.map((row) => row.lifecycleState).toSet(), {
        OperationLifecycleState.queued.name,
        OperationLifecycleState.inFlight.name,
        OperationLifecycleState.retryScheduled.name,
        OperationLifecycleState.reconciliationRequired.name,
        OperationLifecycleState.userInterventionRequired.name,
      });
      final payloads = await database
          .select(database.operationQueuePayload)
          .get();
      expect(payloads, hasLength(5));
    });
    group('retention policy and unresolved age review', () {
      test(
        'applies the 30-day terminal cutoff and preserves its exact boundary',
        () async {
          final expiredAt = _now.subtract(const Duration(days: 31));
          final boundaryAt = _now.subtract(const Duration(days: 30));

          Future<void> createAcknowledged(String id, DateTime timestamp) async {
            await _enqueueEncrypted(
              store,
              _identity(operationId: id),
              now: timestamp,
            );
            await store.transition(
              operationId: id,
              expectedState: OperationLifecycleState.queued,
              nextState: OperationLifecycleState.inFlight,
              context: _testContext(),
              now: timestamp,
            );
            await store.transition(
              operationId: id,
              expectedState: OperationLifecycleState.inFlight,
              nextState: OperationLifecycleState.acknowledged,
              now: timestamp,
            );
          }

          await createAcknowledged('expired-terminal', expiredAt);
          await createAcknowledged('boundary-terminal', boundaryAt);

          final removed = await store.purgeTerminalOperationsPastRetention(
            now: _now,
          );

          expect(removed, 1);
          expect(await store.findByOperationId('expired-terminal'), isNull);
          expect(await store.findByOperationId('boundary-terminal'), isNotNull);

          final payloads = await database
              .select(database.operationQueuePayload)
              .get();
          expect(payloads.map((row) => row.operationId).toSet(), {
            'boundary-terminal',
          });
        },
      );

      test('aged queued and retry entries require review and are excluded from readiness', () async {
        final agedAt = _now.subtract(const Duration(days: 30));
        final recentAt = agedAt.add(const Duration(seconds: 1));

        await _enqueueEncrypted(
          store,
          _identity(operationId: 'aged-queued'),
          now: agedAt,
        );
        await _enqueueEncrypted(
          store,
          _identity(operationId: 'recent-queued'),
          now: recentAt,
        );
        await _enqueueEncrypted(
          store,
          _identity(operationId: 'aged-retry'),
          now: agedAt,
        );

        await store.transition(
          operationId: 'aged-retry',
          expectedState: OperationLifecycleState.queued,
          nextState: OperationLifecycleState.inFlight,
          context: _testContext(),
          now: agedAt,
        );
        await store.recordFailure(
          operationId: 'aged-retry',
          failure: OperationAttemptFailure.confirmedTransientNoEffect,
          now: agedAt,
        );

        final ready = await store.listReady(context: _testContext(), now: _now);
        expect(ready.map((row) => row.operationId), ['recent-queued']);

        final review = await store.listReviewRequired(
          context: _testContext(),
          now: _now,
        );
        expect(review.map((row) => row.operationId).toSet(), {
          'aged-queued',
          'aged-retry',
        });
        expect(
          await store.nextScheduledAttempt(context: _testContext(), now: _now),
          isNull,
        );
      });

      test(
        'review listing remains scoped to the active user and clinic',
        () async {
          final agedAt = _now.subtract(const Duration(days: 31));
          await store.enqueueEncrypted(
            _identity(operationId: 'another-context'),
            context: _testContext(
              userId: 'other-user',
              clinicContextId: 'other-clinic',
            ),
            payload: _testPayload(),
            now: agedAt,
          );

          expect(
            await store.listReviewRequired(context: _testContext(), now: _now),
            isEmpty,
          );
          expect(
            (await store.listReviewRequired(
              context: _testContext(
                userId: 'other-user',
                clinicContextId: 'other-clinic',
              ),
              now: _now,
            )).map((row) => row.operationId),
            ['another-context'],
          );
        },
      );
    });
  });
}
