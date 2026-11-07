import 'package:drift/drift.dart' show driftRuntimeOptions;
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_lifecycle.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_create_queue_state_read_model.dart';

class _SecureStorage implements SecureKeyValueStorage {
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

  final now = DateTime.utc(2026, 11, 7, 12);
  final own = OperationPayloadContext(userId: '21', clinicContextId: '3');
  final otherClinic = OperationPayloadContext(
    userId: '21',
    clinicContextId: '4',
  );
  final otherUser = OperationPayloadContext(userId: '22', clinicContextId: '3');

  late AppDatabase database;
  late OperationQueueStore store;
  late _SecureStorage secrets;
  var databaseClosed = false;

  setUp(() {
    databaseClosed = false;
    database = AppDatabase(NativeDatabase.memory());
    secrets = _SecureStorage();
    store = OperationQueueStore(
      database,
      payloadCrypto: OperationPayloadCrypto(secureStorage: secrets),
    );
  });

  tearDown(() async {
    if (!databaseClosed) {
      await database.close();
    }
  });

  Future<void> seed(
    String id, {
    OperationPayloadContext? context,
    String operationType = 'appointment.create',
    DateTime? createdAt,
  }) async {
    final result = await store.enqueueEncrypted(
      OperationIdentity(
        operationId: id,
        operationType: operationType,
        idempotencyKey: 'key-$id',
      ),
      context: context ?? own,
      payload: <String, dynamic>{'synthetic_test_only': true},
      now: createdAt ?? now,
    );
    expect(result, OperationQueueEnqueueResult.inserted);
  }

  AppointmentCreateQueueStateReadModel reader({
    Future<AppointmentCreateQueueReadSession?> Function()? loadSession,
  }) {
    return AppointmentCreateQueueStateReadModel(
      queueStore: store,
      loadSession:
          loadSession ??
          () async =>
              AppointmentCreateQueueReadSession(context: own, revision: 1),
    );
  }

  Future<void> start(String id) async {
    await store.transition(
      operationId: id,
      expectedState: OperationLifecycleState.queued,
      nextState: OperationLifecycleState.inFlight,
      context: own,
      now: now,
    );
  }

  test('only current owner and clinic appointment creates appear', () async {
    await seed('own');
    await seed('different-clinic', context: otherClinic);
    await seed('different-user', context: otherUser);
    await seed('other-operation', operationType: 'other.write');

    final page = await reader().loadPage();
    expect(page.available, isTrue);
    expect(page.items.map((item) => item.operationId), <String>['own']);
    expect(page.items.single.state, AppointmentCreateQueueDisplayState.pending);
  });

  test('pending metadata is visible even when older than review age', () async {
    await seed('old', createdAt: DateTime.utc(2020, 1, 1));
    final page = await reader().loadPage();
    expect(page.items.single.operationId, 'old');
    expect(page.items.single.state, AppointmentCreateQueueDisplayState.pending);
  });

  test('sending state is distinct from booking acknowledgement', () async {
    await seed('sending');
    await start('sending');
    final page = await reader().loadPage();
    expect(page.items.single.state, AppointmentCreateQueueDisplayState.sending);
    expect(page.items.single.attemptsCompleted, 1);
  });

  test('delayed retry is included without a due-time filter', () async {
    await seed('retry');
    await start('retry');
    await store.recordFailure(
      operationId: 'retry',
      failure: OperationAttemptFailure.confirmedTransientNoEffect,
      now: now,
    );
    final page = await reader().loadPage();
    expect(
      page.items.single.state,
      AppointmentCreateQueueDisplayState.retryScheduled,
    );
    expect(page.items.single.nextAttemptAt, isNotNull);
  });

  test('unknown outcome requires reconciliation instead of booking', () async {
    await seed('unknown');
    await start('unknown');
    await store.recordFailure(
      operationId: 'unknown',
      failure: OperationAttemptFailure.outcomeUnknown,
      now: now,
    );
    final page = await reader().loadPage();
    expect(
      page.items.single.state,
      AppointmentCreateQueueDisplayState.reconciliationRequired,
    );
  });

  test('classified schedule conflicts are displayed for review', () async {
    await seed('conflict');
    await start('conflict');
    await store.recordFailure(
      operationId: 'conflict',
      failure: OperationAttemptFailure.appointmentPatientScheduleConflict,
      now: now,
    );
    final page = await reader().loadPage();
    expect(
      page.items.single.state,
      AppointmentCreateQueueDisplayState.conflictReview,
    );
  });

  test('non-conflict intervention remains a separate state', () async {
    await seed('session-recovery');
    await start('session-recovery');
    await store.recordFailure(
      operationId: 'session-recovery',
      failure: OperationAttemptFailure.sessionExpired,
      now: now,
    );
    final page = await reader().loadPage();
    expect(
      page.items.single.state,
      AppointmentCreateQueueDisplayState.userInterventionRequired,
    );
  });

  test('acknowledged and cancelled terminal metadata remain visible', () async {
    await seed('ack');
    await seed('cancelled');
    await start('ack');
    await store.transition(
      operationId: 'ack',
      expectedState: OperationLifecycleState.inFlight,
      nextState: OperationLifecycleState.acknowledged,
      now: now,
    );
    await store.transition(
      operationId: 'cancelled',
      expectedState: OperationLifecycleState.queued,
      nextState: OperationLifecycleState.cancelled,
      now: now,
    );
    final page = await reader().loadPage();
    expect(
      page.items.map((item) => item.state).toSet(),
      <AppointmentCreateQueueDisplayState>{
        AppointmentCreateQueueDisplayState.acknowledged,
        AppointmentCreateQueueDisplayState.cancelled,
      },
    );
  });

  test('pagination is stable and hasNext uses one extra row', () async {
    for (var index = 0; index < 5; index++) {
      await seed('item-$index', createdAt: now.add(Duration(minutes: index)));
    }
    final model = reader();
    final first = await model.loadPage(page: 1, pageSize: 2);
    final second = await model.loadPage(page: 2, pageSize: 2);
    final third = await model.loadPage(page: 3, pageSize: 2);
    expect(first.items.map((item) => item.operationId), <String>[
      'item-4',
      'item-3',
    ]);
    expect(second.items.map((item) => item.operationId), <String>[
      'item-2',
      'item-1',
    ]);
    expect(third.items.map((item) => item.operationId), <String>['item-0']);
    expect(first.hasNext, isTrue);
    expect(second.hasNext, isTrue);
    expect(third.hasNext, isFalse);
  });

  test('no active session returns no metadata', () async {
    await seed('private');
    final page = await reader(loadSession: () async => null).loadPage();
    expect(page.available, isFalse);
    expect(page.items, isEmpty);
  });

  test('same owner and clinic with changed revision fails closed', () async {
    await seed('private');
    var calls = 0;
    final page = await reader(
      loadSession: () async {
        calls++;
        return AppointmentCreateQueueReadSession(context: own, revision: calls);
      },
    ).loadPage();
    expect(calls, 2);
    expect(page.available, isFalse);
    expect(page.items, isEmpty);
  });

  test('clinic switch while reading fails closed', () async {
    await seed('private');
    var calls = 0;
    final page = await reader(
      loadSession: () async {
        calls++;
        return AppointmentCreateQueueReadSession(
          context: calls == 1 ? own : otherClinic,
          revision: 7,
        );
      },
    ).loadPage();
    expect(page.available, isFalse);
    expect(page.items, isEmpty);
  });

  test(
    'missing keys do not trigger payload decryption during metadata read',
    () async {
      await seed('safe-metadata');
      secrets.values.clear();
      final page = await reader().loadPage();
      expect(page.available, isTrue);
      expect(page.items.single.operationId, 'safe-metadata');
    },
  );

  test('invalid page boundaries are rejected before a query', () async {
    await expectLater(reader().loadPage(page: 0), throwsArgumentError);
    await expectLater(reader().loadPage(pageSize: 51), throwsArgumentError);
  });

  test('store only reads requested scoped operation type', () async {
    await seed('a');
    await seed('b', operationType: 'other.write');
    final rows = await store.listScopedOperationPage(
      context: own,
      operationType: 'appointment.create',
    );
    expect(rows.map((row) => row.operationId), <String>['a']);
    await expectLater(
      store.listScopedOperationPage(context: own, operationType: ''),
      throwsArgumentError,
    );
  });

  test('database failure does not reveal any cached rows', () async {
    await seed('private');
    await database.close();
    databaseClosed = true;
    final page = await reader().loadPage();
    expect(page.available, isFalse);
    expect(page.items, isEmpty);
  });
}
