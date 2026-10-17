import 'dart:async';
import 'dart:convert';

import 'package:drift/native.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/appointment_create_queue_producer.dart';
import 'package:clinic_system_pro/core/sync/operation_lifecycle.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_scheduler.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_worker.dart';

class _MemorySecureStorage implements SecureKeyValueStorage {
  final Map<String, String> _values = <String, String>{};

  @override
  Future<void> write({required String key, required String value}) async {
    _values[key] = value;
  }

  @override
  Future<String?> read({required String key}) async => _values[key];

  @override
  Future<void> delete({required String key}) async {
    _values.remove(key);
  }
}

class _FakeConnectivity implements DeviceConnectivitySource {
  _FakeConnectivity(this.current);

  DeviceNetworkStatus current;
  final StreamController<DeviceNetworkStatus> _controller =
      StreamController<DeviceNetworkStatus>.broadcast(sync: true);

  @override
  Future<DeviceNetworkStatus> checkStatus() async => current;

  @override
  Stream<DeviceNetworkStatus> get statusChanges => _controller.stream;

  void emit(DeviceNetworkStatus status) {
    current = status;
    _controller.add(status);
  }

  Future<void> close() => _controller.close();
}

Future<void> _settle() =>
    Future<void>.delayed(const Duration(milliseconds: 25));

class _Harness {
  _Harness(DeviceNetworkStatus initialStatus)
    : database = AppDatabase(NativeDatabase.memory()),
      storage = _MemorySecureStorage(),
      connectivity = _FakeConnectivity(initialStatus) {
    queueStore = OperationQueueStore(
      database,
      payloadCrypto: OperationPayloadCrypto(secureStorage: storage),
    );
    scheduler = OperationReplayScheduler(
      connectivitySource: connectivity,
      runReplay: () async {
        replayCalls++;
        return OperationReplayRunResult(
          items: const <OperationReplayItemResult>[],
        );
      },
    );
  }

  final AppDatabase database;
  final _MemorySecureStorage storage;
  final _FakeConnectivity connectivity;

  late final OperationQueueStore queueStore;
  late final OperationReplayScheduler scheduler;
  int replayCalls = 0;

  AppointmentCreateQueueProducer producer({
    required Future<OperationPayloadContext?> Function() loadContext,
  }) {
    return AppointmentCreateQueueProducer(
      queueStore: queueStore,
      replayScheduler: scheduler,
      loadContext: loadContext,
    );
  }

  Future<void> start() async {
    await scheduler.start();
    await _settle();
  }

  Future<void> close() async {
    await scheduler.dispose();
    await connectivity.close();
    await database.close();
  }
}

AppointmentCreateCommand _command() {
  return AppointmentCreateCommand(
    patientId: 101,
    staffId: 9,
    scheduledStart: DateTime.utc(2026, 10, 20, 10),
    scheduledEnd: DateTime.utc(2026, 10, 20, 10, 30),
    appointmentType: 'consultation',
    reason: 'Follow-up',
    notes: 'Bring previous results',
  );
}

Future<OperationPayloadContext?> _activeContext() async {
  return OperationPayloadContext(userId: '21', clinicContextId: '8');
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test(
    'persists an encrypted, clinic-scoped operation and wakes replay',
    () async {
      final harness = _Harness(DeviceNetworkStatus.connected);

      try {
        await harness.start();
        expect(harness.replayCalls, 1);

        final command = _command();
        final result = await harness
            .producer(loadContext: _activeContext)
            .enqueue(command);

        expect(result.status, AppointmentCreateEnqueueStatus.queued);
        expect(result.persistenceResult, OperationQueueEnqueueResult.inserted);
        expect(result.operationId, command.operationId);
        expect(result.idempotencyKey, command.idempotencyKey);

        final commandPayload = command.toPayload();
        expect(commandPayload.containsKey('reason'), isFalse);
        expect(commandPayload.containsKey('notes'), isFalse);
        expect(commandPayload['appointment_type'], 'consultation');

        final entry = await harness.queueStore.findByOperationId(
          command.operationId,
        );
        expect(entry, isNotNull);
        expect(entry!.operationType, 'appointment.create');
        expect(entry.idempotencyKey, command.idempotencyKey);
        expect(entry.lifecycleState, OperationLifecycleState.queued.name);
        expect(entry.ownerUserId, '21');
        expect(entry.clinicContextId, '8');

        final persistedPayload = await harness.queueStore.loadPayload(
          operationId: command.operationId,
          context: OperationPayloadContext(userId: '21', clinicContextId: '8'),
        );
        expect(persistedPayload.containsKey('reason'), isFalse);
        expect(persistedPayload.containsKey('notes'), isFalse);

        final encryptedRows = await harness.database
            .select(harness.database.operationQueuePayload)
            .get();
        expect(encryptedRows, hasLength(1));
        expect(encryptedRows.single.ciphertext, isNotEmpty);

        final cleartext = utf8.encode(jsonEncode(command.toPayload()));
        expect(encryptedRows.single.ciphertext, isNot(equals(cleartext)));

        await _settle();
        expect(harness.replayCalls, 2);
      } finally {
        await harness.close();
      }
    },
  );

  test(
    're-enqueueing the same command preserves its identity and one row',
    () async {
      final harness = _Harness(DeviceNetworkStatus.disconnected);

      try {
        final producer = harness.producer(loadContext: _activeContext);
        final command = _command();

        final first = await producer.enqueue(command);
        final second = await producer.enqueue(command);

        expect(first.status, AppointmentCreateEnqueueStatus.queued);
        expect(second.status, AppointmentCreateEnqueueStatus.alreadyQueued);
        expect(second.operationId, first.operationId);
        expect(second.idempotencyKey, first.idempotencyKey);
        expect(
          await harness.database
              .select(harness.database.operationQueueEntry)
              .get(),
          hasLength(1),
        );
        expect(
          await harness.database
              .select(harness.database.operationQueuePayload)
              .get(),
          hasLength(1),
        );
      } finally {
        await harness.close();
      }
    },
  );

  test(
    'fails closed when the authenticated owner or clinic is unavailable',
    () async {
      final harness = _Harness(DeviceNetworkStatus.disconnected);

      try {
        final result = await harness
            .producer(loadContext: () async => null)
            .enqueue(_command());

        expect(
          result.status,
          AppointmentCreateEnqueueStatus.sessionUnavailable,
        );
        expect(
          await harness.database
              .select(harness.database.operationQueueEntry)
              .get(),
          isEmpty,
        );
        expect(
          await harness.database
              .select(harness.database.operationQueuePayload)
              .get(),
          isEmpty,
        );
      } finally {
        await harness.close();
      }
    },
  );

  test('rejects invalid appointment identifiers and time ranges', () {
    expect(
      () => AppointmentCreateCommand(
        patientId: 0,
        staffId: 9,
        scheduledStart: DateTime.utc(2026, 10, 20, 10),
        scheduledEnd: DateTime.utc(2026, 10, 20, 10, 30),
      ),
      throwsArgumentError,
    );

    expect(
      () => AppointmentCreateCommand(
        patientId: 101,
        staffId: 9,
        scheduledStart: DateTime.utc(2026, 10, 20, 10, 30),
        scheduledEnd: DateTime.utc(2026, 10, 20, 10),
      ),
      throwsArgumentError,
    );
  });

  testWidgets('exposes the producer through the app scope', (tester) async {
    final harness = _Harness(DeviceNetworkStatus.disconnected);

    try {
      final producer = harness.producer(loadContext: _activeContext);

      await tester.pumpWidget(
        Directionality(
          textDirection: TextDirection.ltr,
          child: AppointmentCreateQueueScope(
            producer: producer,
            reconcileOperation: (operationId) async =>
                OperationReconciliationResult(
                  operationId: operationId,
                  disposition: OperationReconciliationDisposition
                      .notReconciliationRequired,
                ),
            child: Builder(
              builder: (context) {
                final resolved = AppointmentCreateQueueScope.of(context)
                    .producer;
                return Text(
                  identical(resolved, producer) ? 'available' : 'missing',
                );
              },
            ),
          ),
        ),
      );

      expect(find.text('available'), findsOneWidget);
    } finally {
      await tester.pumpWidget(const SizedBox.shrink());
      await harness.close();
    }
  });
}
