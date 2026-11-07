import 'dart:async';

import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_lifecycle.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_create_queue_state_read_model.dart';
import 'package:drift/drift.dart' show driftRuntimeOptions;
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';

/// S06 appointment-create security/failure matrix. Synthetic metadata only.
/// No real network, credentials, clinical text, or automatic mutation replay.
class _MemoryKeys implements SecureKeyValueStorage {
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
  TestWidgetsFlutterBinding.ensureInitialized();
  driftRuntimeOptions.dontWarnAboutMultipleDatabases = true;
  final now = DateTime.utc(2026, 11, 7, 8);
  final own = OperationPayloadContext(userId: '41', clinicContextId: '7');
  final anotherUser = OperationPayloadContext(
    userId: '42',
    clinicContextId: '7',
  );
  final anotherClinic = OperationPayloadContext(
    userId: '41',
    clinicContextId: '8',
  );

  late AppDatabase database;
  late OperationQueueStore queue;
  late _MemoryKeys keys;
  var databaseClosed = false;

  setUp(() {
    databaseClosed = false;
    database = AppDatabase(NativeDatabase.memory());
    keys = _MemoryKeys();
    queue = OperationQueueStore(
      database,
      payloadCrypto: OperationPayloadCrypto(secureStorage: keys),
    );
  });

  tearDown(() async {
    if (!databaseClosed) await database.close();
  });

  OperationIdentity identity(
    String id, {
    String operationType = 'appointment.create',
    String? idempotencyKey,
  }) => OperationIdentity(
    operationId: id,
    operationType: operationType,
    idempotencyKey: idempotencyKey ?? 'stable-$id',
  );

  Future<OperationQueueEnqueueResult> add(
    String id, {
    OperationPayloadContext? context,
    String operationType = 'appointment.create',
    DateTime? at,
    Map<String, dynamic>? payload,
    String? idempotencyKey,
  }) => queue.enqueueEncrypted(
    identity(id, operationType: operationType, idempotencyKey: idempotencyKey),
    context: context ?? own,
    payload: payload ?? <String, dynamic>{'synthetic': id},
    now: at ?? now,
  );

  AppointmentCreateQueueReadSession stamp(
    OperationPayloadContext context, {
    int revision = 11,
  }) => AppointmentCreateQueueReadSession(context: context, revision: revision);

  AppointmentCreateQueueStateReadModel model(
    Future<AppointmentCreateQueueReadSession?> Function() loader,
  ) => AppointmentCreateQueueStateReadModel(
    queueStore: queue,
    loadSession: loader,
  );

  AppointmentCreateQueueStateReadModel ownModel() =>
      model(() async => stamp(own));

  Future<void> started(String id) => queue
      .transition(
        operationId: id,
        expectedState: OperationLifecycleState.queued,
        nextState: OperationLifecycleState.inFlight,
        context: own,
        now: now,
      )
      .then((_) {});

  group('S06 scoped metadata and identity isolation', () {
    test('same clinic but different owner never leaks into a page', () async {
      await add('owned');
      await add('foreign-user', context: anotherUser);
      final page = await ownModel().loadPage();
      expect(page.available, isTrue);
      expect(page.items.map((item) => item.operationId), <String>['owned']);
    });

    test('same owner but different clinic never leaks into a page', () async {
      await add('owned');
      await add('foreign-clinic', context: anotherClinic);
      final page = await ownModel().loadPage();
      expect(page.available, isTrue);
      expect(page.items.map((item) => item.operationId), <String>['owned']);
    });

    test(
      'other operation types are excluded even for matching identity',
      () async {
        await add('create');
        await add('status-write', operationType: 'appointment.cancel');
        final page = await ownModel().loadPage();
        expect(page.items.map((item) => item.operationId), <String>['create']);
      },
    );

    test('foreign session cannot read a known operation identifier', () async {
      await add('private');
      final page = await model(() async => stamp(anotherUser)).loadPage();
      expect(page.available, isTrue);
      expect(page.items, isEmpty);
    });

    test('foreign clinic cannot read a known operation identifier', () async {
      await add('private');
      final page = await model(() async => stamp(anotherClinic)).loadPage();
      expect(page.available, isTrue);
      expect(page.items, isEmpty);
    });

    test('scope and operation-type filters apply before pagination', () async {
      await add('owned-one', at: now);
      await add(
        'foreign-newer',
        context: anotherUser,
        at: now.add(const Duration(hours: 3)),
      );
      await add(
        'other-type',
        operationType: 'appointment.reschedule',
        at: now.add(const Duration(hours: 2)),
      );
      await add('owned-two', at: now.add(const Duration(hours: 1)));
      final page = await ownModel().loadPage(pageSize: 1);
      expect(page.available, isTrue);
      expect(page.hasNext, isTrue);
      expect(page.items.single.operationId, 'owned-two');
      final next = await ownModel().loadPage(page: 2, pageSize: 1);
      expect(next.items.single.operationId, 'owned-one');
      expect(next.hasNext, isFalse);
    });
  });

  group('S06 logout, revocation and asynchronous session races', () {
    test('absent session fails closed before metadata can appear', () async {
      await add('private');
      final page = await model(() async => null).loadPage();
      expect(page.available, isFalse);
      expect(page.items, isEmpty);
    });

    test(
      'session storage exception returns unavailable rather than data',
      () async {
        await add('private');
        final page = await model(
          () async => throw StateError('synthetic storage failure'),
        ).loadPage();
        expect(page.available, isFalse);
        expect(page.items, isEmpty);
      },
    );

    test('logout detected after database query clears the response', () async {
      await add('private');
      var calls = 0;
      final page = await model(() async {
        calls++;
        return calls == 1 ? stamp(own) : null;
      }).loadPage();
      expect(calls, 2);
      expect(page.available, isFalse);
      expect(page.items, isEmpty);
    });

    test('owner switch during database query rejects stale metadata', () async {
      await add('private');
      var calls = 0;
      final page = await model(() async {
        calls++;
        return stamp(calls == 1 ? own : anotherUser);
      }).loadPage();
      expect(page.available, isFalse);
      expect(page.items, isEmpty);
    });

    test(
      'clinic switch during database query rejects stale metadata',
      () async {
        await add('private');
        var calls = 0;
        final page = await model(() async {
          calls++;
          return stamp(calls == 1 ? own : anotherClinic);
        }).loadPage();
        expect(page.available, isFalse);
        expect(page.items, isEmpty);
      },
    );

    test('guard revision changes invalidate same-owner response', () async {
      await add('private');
      var calls = 0;
      final page = await model(() async => stamp(own, revision: ++calls))
          .loadPage();
      expect(calls, 2);
      expect(page.available, isFalse);
      expect(page.items, isEmpty);
    });

    test(
      'delayed session validation after sign-out never reads metadata',
      () async {
        await add('private');
        final gate = Completer<AppointmentCreateQueueReadSession?>();
        final pending = model(() => gate.future).loadPage();
        gate.complete(null);
        final result = await pending;
        expect(result.available, isFalse);
        expect(result.items, isEmpty);
      },
    );
  });

  group('S06 duplicate prevention and no unsafe automatic replay', () {
    test(
      'same encrypted identity and payload remains one logical operation',
      () async {
        expect(await add('once'), OperationQueueEnqueueResult.inserted);
        expect(await add('once'), OperationQueueEnqueueResult.alreadyPresent);
        final page = await ownModel().loadPage();
        expect(page.items.length, 1);
      },
    );

    test(
      'simultaneous identical enqueues persist one logical operation',
      () async {
        final results = await Future.wait(<Future<OperationQueueEnqueueResult>>[
          add('race'),
          add('race'),
        ]);
        expect(results.toSet(), <OperationQueueEnqueueResult>{
          OperationQueueEnqueueResult.inserted,
          OperationQueueEnqueueResult.alreadyPresent,
        });
        expect((await ownModel().loadPage()).items.length, 1);
      },
    );

    test(
      'operation ID reuse across clinics is rejected without overwrite',
      () async {
        await add('same-id');
        final result = await add('same-id', context: anotherClinic);
        expect(result, OperationQueueEnqueueResult.contextConflict);
        expect((await ownModel().loadPage()).items.length, 1);
        expect(
          (await model(() async => stamp(anotherClinic)).loadPage()).items,
          isEmpty,
        );
      },
    );

    test(
      'same operation ID with changed encrypted payload is rejected',
      () async {
        await add('same-id', payload: <String, dynamic>{'synthetic': 'before'});
        final result = await add(
          'same-id',
          payload: <String, dynamic>{'synthetic': 'after'},
        );
        expect(result, OperationQueueEnqueueResult.payloadConflict);
        expect((await ownModel().loadPage()).items.length, 1);
      },
    );

    test('idempotency key reuse for another operation is rejected', () async {
      await add('first', idempotencyKey: 'shared-key');
      expect(
        await add('second', idempotencyKey: 'shared-key'),
        OperationQueueEnqueueResult.idempotencyKeyConflict,
      );
      expect((await ownModel().loadPage()).items.length, 1);
    });

    test(
      'unknown server outcome requires review and is not ready to replay',
      () async {
        await add('unknown');
        await started('unknown');
        final row = await queue.recordFailure(
          operationId: 'unknown',
          failure: OperationAttemptFailure.outcomeUnknown,
          now: now,
        );
        expect(
          row.lifecycleState,
          OperationLifecycleState.reconciliationRequired.name,
        );
        expect(row.nextAttemptAt, isNull);
        final ready = await queue.listReady(
          context: own,
          now: now.add(const Duration(minutes: 10)),
          operationTypes: <String>{'appointment.create'},
        );
        expect(ready, isEmpty);
        final page = await ownModel().loadPage();
        expect(
          page.items.single.state,
          AppointmentCreateQueueDisplayState.reconciliationRequired,
        );
      },
    );

    test(
      'schedule conflict is a review state and never a confirmed booking',
      () async {
        await add('conflict');
        await started('conflict');
        await queue.recordFailure(
          operationId: 'conflict',
          failure: OperationAttemptFailure.appointmentPatientScheduleConflict,
          now: now,
        );
        final page = await ownModel().loadPage();
        expect(
          page.items.single.state,
          AppointmentCreateQueueDisplayState.conflictReview,
        );
        expect(
          page.items.single.state,
          isNot(AppointmentCreateQueueDisplayState.acknowledged),
        );
      },
    );
  });

  group('S06 storage failures and bounded read defenses', () {
    test('missing encryption keys cannot expose or decrypt payload in metadata reads', () async {
      await add('without-key');
      keys.values.clear();
      final page = await ownModel().loadPage();
      expect(page.available, isTrue);
      expect(page.items.single.operationId, 'without-key');
      expect(
        page.items.single.state,
        AppointmentCreateQueueDisplayState.pending,
      );
    });

    test(
      'database failure fails closed without leaking cached metadata',
      () async {
        await add('private');
        await database.close();
        databaseClosed = true;
        final page = await ownModel().loadPage();
        expect(page.available, isFalse);
        expect(page.items, isEmpty);
      },
    );

    test(
      'invalid pagination bounds are rejected without loading session',
      () async {
        var loads = 0;
        final reader = model(() async {
          loads++;
          return stamp(own);
        });
        await expectLater(reader.loadPage(page: 0), throwsArgumentError);
        await expectLater(reader.loadPage(page: 10001), throwsArgumentError);
        await expectLater(reader.loadPage(pageSize: 0), throwsArgumentError);
        await expectLater(reader.loadPage(pageSize: 51), throwsArgumentError);
        expect(loads, 0);
      },
    );

    test('store rejects invalid limits, offset and operation type', () async {
      await expectLater(
        queue.listScopedOperationPage(
          context: own,
          operationType: 'appointment.create',
          limit: 0,
        ),
        throwsArgumentError,
      );
      await expectLater(
        queue.listScopedOperationPage(
          context: own,
          operationType: 'appointment.create',
          limit: 101,
        ),
        throwsArgumentError,
      );
      await expectLater(
        queue.listScopedOperationPage(
          context: own,
          operationType: 'appointment.create',
          offset: -1,
        ),
        throwsArgumentError,
      );
      await expectLater(
        queue.listScopedOperationPage(
          context: own,
          operationType: ' appointment.create',
        ),
        throwsArgumentError,
      );
    });

    test(
      'aged queued entry is still surfaced, never called acknowledged',
      () async {
        await add('aged', at: DateTime.utc(2021, 1, 1));
        final page = await ownModel().loadPage();
        expect(page.available, isTrue);
        expect(
          page.items.single.state,
          AppointmentCreateQueueDisplayState.pending,
        );
        expect(page.items.single.attemptsCompleted, 0);
      },
    );
  });
}
