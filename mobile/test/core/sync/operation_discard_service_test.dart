import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_discard_service.dart';
import 'package:clinic_system_pro/core/sync/operation_lifecycle.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

final _now = DateTime.utc(2026, 10, 17, 8);

const _session = AuthTokenResponse(
  accessToken: 'access-token',
  refreshToken: 'refresh-token',
  userId: 7,
  role: 'DOCTOR',
  clinicContextId: 3,
);

OperationPayloadContext _context({
  String userId = '7',
  String clinicId = '3',
}) => OperationPayloadContext(userId: userId, clinicContextId: clinicId);

class _MemorySecureStorage implements SecureKeyValueStorage {
  final Map<String, String> values = <String, String>{};
  Object? deleteFailure;

  @override
  Future<String?> read({required String key}) async => values[key];

  @override
  Future<void> write({required String key, required String value}) async {
    values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    if (deleteFailure != null) throw deleteFailure!;
    values.remove(key);
  }
}

Map<String, dynamic> _receipt({
  required String operationId,
  required String operationType,
  required String reasonCode,
}) => <String, dynamic>{
  'success': true,
  'data': <String, dynamic>{
    'id': 41,
    'operation_id': operationId,
    'operation_type': operationType,
    'resolution': 'confirmed_no_effect',
    'reason_code': reasonCode,
    'policy_version': '1',
    'created_at': '2026-10-17T08:00:00+00:00',
  },
};

void main() {
  late AppDatabase database;
  late _MemorySecureStorage secureStorage;
  late OperationPayloadCrypto crypto;
  late OperationQueueStore store;

  setUp(() {
    database = AppDatabase(NativeDatabase.memory());
    secureStorage = _MemorySecureStorage();
    crypto = OperationPayloadCrypto(secureStorage: secureStorage);
    store = OperationQueueStore(database, payloadCrypto: crypto);
  });

  tearDown(() async => database.close());

  Future<void> enqueue(
    String id, {
    String operationType = 'appointment.create',
    OperationPayloadContext? context,
  }) async {
    await store.enqueueEncrypted(
      OperationIdentity(
        operationId: id,
        operationType: operationType,
        idempotencyKey: 'key-$id',
      ),
      context: context ?? _context(),
      payload: <String, dynamic>{'test_value': id},
      now: _now,
    );
  }

  test('server confirms never-sent no-effect before local deletion and key cleanup', () async {
    await enqueue('discard-never-sent');
    Map<String, dynamic>? sentBody;
    String? sentPath;
    final service = OperationDiscardService(
      queueStore: store,
      loadSession: () async => _session,
      sendDisposition:
          ({required path, required body, required bearerToken}) async {
            sentPath = path;
            sentBody = body;
            expect(bearerToken, _session.accessToken);
            return _receipt(
              operationId: 'discard-never-sent',
              operationType: 'appointment.create',
              reasonCode: 'verified_not_sent',
            );
          },
    );

    final result = await service.discard('discard-never-sent');

    expect(result.status, OperationDiscardStatus.deleted);
    expect(
      sentPath,
      'api/v1/offline-operations/discard-never-sent/disposition',
    );
    expect(sentBody!['reason_code'], 'verified_not_sent');
    expect(sentBody!['confirmed_no_effect'], isTrue);
    expect(await store.findByOperationId('discard-never-sent'), isNull);
    expect(
      await database.select(database.operationQueuePayload).get(),
      isEmpty,
    );
    expect(secureStorage.values, isEmpty);
    expect(result.serverDispositionConfirmed, isTrue);
  });

  test('recovers a lost disposition response when a retry reason changes', () async {
    const operationId = 'discard-lost-disposition-response';
    await enqueue(operationId);

    final firstAttempt = OperationDiscardService(
      queueStore: store,
      loadSession: () async => _session,
      sendDisposition:
          ({required path, required body, required bearerToken}) async {
            expect(body['reason_code'], 'verified_not_sent');
            // Simulates the backend committing while the response is lost.
            throw StateError('injected response loss after server commit');
          },
    );
    final firstResult = await firstAttempt.discard(operationId);
    expect(
      firstResult.status,
      OperationDiscardStatus.serverDispositionNotConfirmed,
    );
    expect(await store.findByOperationId(operationId), isNotNull);

    // Simulate a concurrent worker advancing the local state after the first
    // request. The next explicit no-effect confirmation now uses the other
    // supported reason, while the server returns the original durable receipt.
    await store.transition(
      operationId: operationId,
      expectedState: OperationLifecycleState.queued,
      nextState: OperationLifecycleState.inFlight,
      context: _context(),
      now: _now,
    );

    Map<String, dynamic>? retryBody;
    final retryAttempt = OperationDiscardService(
      queueStore: store,
      loadSession: () async => _session,
      sendDisposition:
          ({required path, required body, required bearerToken}) async {
            retryBody = body;
            return _receipt(
              operationId: operationId,
              operationType: 'appointment.create',
              reasonCode: 'verified_not_sent',
            );
          },
    );
    final retryResult = await retryAttempt.discard(
      operationId,
      manualNoEffectConfirmed: true,
    );

    expect(retryBody!['reason_code'], 'manual_reconciliation_no_effect');
    expect(retryResult.status, OperationDiscardStatus.deleted);
    expect(await store.findByOperationId(operationId), isNull);
    expect(
      await database.select(database.operationQueuePayload).get(),
      isEmpty,
    );
    expect(secureStorage.values, isEmpty);
  });

  test('network failure leaves queue, ciphertext, and key intact', () async {
    await enqueue('discard-server-unavailable');
    final service = OperationDiscardService(
      queueStore: store,
      loadSession: () async => _session,
      sendDisposition:
          ({required path, required body, required bearerToken}) async {
            throw StateError('injected transport failure');
          },
    );

    final result = await service.discard('discard-server-unavailable');

    expect(result.status, OperationDiscardStatus.serverDispositionNotConfirmed);
    expect(
      await store.findByOperationId('discard-server-unavailable'),
      isNotNull,
    );
    expect(
      await database.select(database.operationQueuePayload).get(),
      hasLength(1),
    );
    expect(secureStorage.values, hasLength(1));
  });

  test(
    'attempted operation requires explicit manual no-effect confirmation',
    () async {
      await enqueue('discard-attempted');
      await store.transition(
        operationId: 'discard-attempted',
        expectedState: OperationLifecycleState.queued,
        nextState: OperationLifecycleState.inFlight,
        context: _context(),
        now: _now,
      );
      var sent = false;
      final service = OperationDiscardService(
        queueStore: store,
        loadSession: () async => _session,
        sendDisposition:
            ({required path, required body, required bearerToken}) async {
              sent = true;
              return _receipt(
                operationId: 'discard-attempted',
                operationType: 'appointment.create',
                reasonCode: 'manual_reconciliation_no_effect',
              );
            },
      );

      final result = await service.discard('discard-attempted');

      expect(
        result.status,
        OperationDiscardStatus.manualReconciliationRequired,
      );
      expect(sent, isFalse);
      expect(await store.findByOperationId('discard-attempted'), isNotNull);
    },
  );

  test(
    'manual reconciliation confirmation uses shared endpoint then deletes',
    () async {
      await enqueue('discard-manual-confirmed');
      await store.transition(
        operationId: 'discard-manual-confirmed',
        expectedState: OperationLifecycleState.queued,
        nextState: OperationLifecycleState.inFlight,
        context: _context(),
        now: _now,
      );
      await store.recordFailure(
        operationId: 'discard-manual-confirmed',
        failure: OperationAttemptFailure.outcomeUnknown,
        now: _now,
      );
      Map<String, dynamic>? sentBody;
      final service = OperationDiscardService(
        queueStore: store,
        loadSession: () async => _session,
        sendDisposition:
            ({required path, required body, required bearerToken}) async {
              sentBody = body;
              return _receipt(
                operationId: 'discard-manual-confirmed',
                operationType: 'appointment.create',
                reasonCode: 'manual_reconciliation_no_effect',
              );
            },
      );

      final result = await service.discard(
        'discard-manual-confirmed',
        manualNoEffectConfirmed: true,
      );

      expect(result.status, OperationDiscardStatus.deleted);
      expect(sentBody!['reason_code'], 'manual_reconciliation_no_effect');
      expect(await store.findByOperationId('discard-manual-confirmed'), isNull);
      expect(secureStorage.values, isEmpty);
    },
  );

  test(
    'retains context key when another encrypted payload still depends on it',
    () async {
      await enqueue('discard-one');
      await enqueue('retain-two');
      final service = OperationDiscardService(
        queueStore: store,
        loadSession: () async => _session,
        sendDisposition:
            ({required path, required body, required bearerToken}) async =>
                _receipt(
                  operationId: 'discard-one',
                  operationType: 'appointment.create',
                  reasonCode: 'verified_not_sent',
                ),
      );

      final result = await service.discard('discard-one');

      expect(
        result.status,
        OperationDiscardStatus.deletedKeyRetainedForDependencies,
      );
      expect(await store.findByOperationId('discard-one'), isNull);
      expect(await store.findByOperationId('retain-two'), isNotNull);
      expect(secureStorage.values, hasLength(1));
      expect(
        await store.loadPayload(operationId: 'retain-two', context: _context()),
        <String, dynamic>{'test_value': 'retain-two'},
      );
    },
  );

  test('secure-storage delete failure is reported and retry cleanup is recoverable', () async {
    await enqueue('discard-key-failure');
    secureStorage.deleteFailure = StateError(
      'injected secure-storage delete failure',
    );
    var sendCount = 0;
    final service = OperationDiscardService(
      queueStore: store,
      loadSession: () async => _session,
      sendDisposition:
          ({required path, required body, required bearerToken}) async {
            sendCount++;
            return _receipt(
              operationId: 'discard-key-failure',
              operationType: 'appointment.create',
              reasonCode: 'verified_not_sent',
            );
          },
    );

    final first = await service.discard('discard-key-failure');
    expect(first.status, OperationDiscardStatus.deletedKeyCleanupPending);
    expect(await store.findByOperationId('discard-key-failure'), isNull);
    expect(secureStorage.values, hasLength(1));

    secureStorage.deleteFailure = null;
    final retry = await service.discard('discard-key-failure');
    expect(retry.status, OperationDiscardStatus.alreadyAbsentKeyDeleted);
    expect(secureStorage.values, isEmpty);
    expect(sendCount, 1);
  });

  test(
    'unsupported operation type is retained and never sent to backend',
    () async {
      await enqueue(
        'discard-unsupported',
        operationType: 'prescription.create',
      );
      var sent = false;
      final service = OperationDiscardService(
        queueStore: store,
        loadSession: () async => _session,
        sendDisposition:
            ({required path, required body, required bearerToken}) async {
              sent = true;
              return <String, dynamic>{};
            },
      );

      final result = await service.discard('discard-unsupported');

      expect(result.status, OperationDiscardStatus.unsupportedOperation);
      expect(sent, isFalse);
      expect(await store.findByOperationId('discard-unsupported'), isNotNull);
    },
  );

  test(
    'wrong context does not contact server or delete another user queue',
    () async {
      await enqueue('discard-other-owner', context: _context(userId: '8'));
      var sent = false;
      final service = OperationDiscardService(
        queueStore: store,
        loadSession: () async => _session,
        sendDisposition:
            ({required path, required body, required bearerToken}) async {
              sent = true;
              return <String, dynamic>{};
            },
      );

      final result = await service.discard('discard-other-owner');

      expect(result.status, OperationDiscardStatus.contextMismatch);
      expect(sent, isFalse);
      expect(await store.findByOperationId('discard-other-owner'), isNotNull);
    },
  );

  test('acknowledged operation cannot use no-effect discard', () async {
    await enqueue('discard-acknowledged');
    await store.transition(
      operationId: 'discard-acknowledged',
      expectedState: OperationLifecycleState.queued,
      nextState: OperationLifecycleState.inFlight,
      context: _context(),
      now: _now,
    );
    await store.transition(
      operationId: 'discard-acknowledged',
      expectedState: OperationLifecycleState.inFlight,
      nextState: OperationLifecycleState.acknowledged,
      now: _now,
    );
    var sent = false;
    final service = OperationDiscardService(
      queueStore: store,
      loadSession: () async => _session,
      sendDisposition:
          ({required path, required body, required bearerToken}) async {
            sent = true;
            return <String, dynamic>{};
          },
    );

    final result = await service.discard('discard-acknowledged');

    expect(result.status, OperationDiscardStatus.acknowledgedOperation);
    expect(sent, isFalse);
    expect(await store.findByOperationId('discard-acknowledged'), isNotNull);
  });

  test('invalid server receipt leaves local queue intact', () async {
    await enqueue('discard-invalid-receipt');
    final service = OperationDiscardService(
      queueStore: store,
      loadSession: () async => _session,
      sendDisposition:
          ({required path, required body, required bearerToken}) async =>
              _receipt(
                operationId: 'wrong-operation-id',
                operationType: 'appointment.create',
                reasonCode: 'verified_not_sent',
              ),
    );

    final result = await service.discard('discard-invalid-receipt');

    expect(result.status, OperationDiscardStatus.invalidServerReceipt);
    expect(await store.findByOperationId('discard-invalid-receipt'), isNotNull);
    expect(
      await database.select(database.operationQueuePayload).get(),
      hasLength(1),
    );
    expect(secureStorage.values, hasLength(1));
  });

  test(
    'server reconciliation conflict keeps local encrypted operation',
    () async {
      await enqueue('discard-reconciliation-conflict');
      final service = OperationDiscardService(
        queueStore: store,
        loadSession: () async => _session,
        sendDisposition:
            ({required path, required body, required bearerToken}) async {
              throw ApiException(
                kind: ApiFailureKind.httpStatus,
                message: 'conflict',
                uri: Uri.parse(
                  'https://clinic.example/api/v1/offline-operations',
                ),
                statusCode: 409,
                serverCode: 'OFFLINE_OPERATION_OUTCOME_REQUIRES_RECONCILIATION',
              );
            },
      );

      final result = await service.discard('discard-reconciliation-conflict');

      expect(
        result.status,
        OperationDiscardStatus.outcomeRequiresReconciliation,
      );
      expect(
        await store.findByOperationId('discard-reconciliation-conflict'),
        isNotNull,
      );
      expect(
        await database.select(database.operationQueuePayload).get(),
        hasLength(1),
      );
      expect(secureStorage.values, hasLength(1));
    },
  );
}
