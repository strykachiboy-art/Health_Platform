import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_discard_service.dart';
import 'package:clinic_system_pro/core/sync/operation_lifecycle.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_worker.dart';
import 'package:clinic_system_pro/core/sync/tenant_operation_scope.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/status/data/scoped_sync_status_source.dart';

const tenantA = '12345678-1234-1234-1234-123456789abc';
const tenantB = '87654321-4321-4321-4321-cba987654321';
final now = DateTime.utc(2026, 11, 26);

AuthTokenResponse session({
  String? tenant = tenantA,
  int user = 7,
  int? clinic = 3,
  String role = 'doctor',
}) => AuthTokenResponse(
  accessToken: 'access-test',
  refreshToken: 'refresh-test',
  userId: user,
  role: role,
  clinicContextId: clinic,
  tenantId: tenant,
);

class MemoryStorage implements SecureKeyValueStorage {
  final values = <String, String>{};
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

OperationIdentity identity(String id) => OperationIdentity(
  operationId: id,
  operationType: 'appointment.create',
  idempotencyKey: 'stable-key-$id',
);

void main() {
  late AppDatabase database;
  late OperationPayloadCrypto crypto;
  late OperationQueueStore store;
  final legacy = OperationPayloadContext(userId: '7', clinicContextId: '3');
  final a = TenantOperationScope.appointmentContext(session())!;
  final b = TenantOperationScope.appointmentContext(session(tenant: tenantB))!;

  setUp(() {
    database = AppDatabase(NativeDatabase.memory());
    crypto = OperationPayloadCrypto(secureStorage: MemoryStorage());
    store = OperationQueueStore(database, payloadCrypto: crypto);
  });
  tearDown(() async {
    await database.close();
  });

  Future<void> enqueue(String id, OperationPayloadContext context) async {
    final outcome = await store.enqueueEncrypted(
      identity(id),
      context: context,
      payload: <String, dynamic>{
        'patient_id': 15,
        'staff_id': 17,
        'scheduled_start': '2026-11-27T12:00:00Z',
        'scheduled_end': '2026-11-27T12:30:00Z',
      },
      now: now,
    );
    expect(outcome, OperationQueueEnqueueResult.inserted);
  }

  test('strict session scope rejects absent malformed tenant and clinic', () {
    expect(
      TenantOperationScope.appointmentContext(session(tenant: null)),
      isNull,
    );
    expect(
      TenantOperationScope.appointmentContext(session(tenant: 'bad')),
      isNull,
    );
    expect(
      TenantOperationScope.appointmentContext(session(clinic: null)),
      isNull,
    );
    expect(TenantOperationScope.appointmentContext(session(user: 0)), isNull);
    expect(TenantOperationScope.appointmentContext(null), isNull);
  });
  test('tenant namespace is disjoint from legacy and other tenant', () {
    expect(a.clinicContextId, 'tenant-v1:$tenantA:3');
    expect(a.clinicContextId, isNot(legacy.clinicContextId));
    expect(a.clinicContextId, isNot(b.clinicContextId));
    expect(
      TenantOperationScope.appointmentContext(
        session(tenant: tenantA.toUpperCase()),
      )!.clinicContextId,
      a.clinicContextId,
    );
  });
  test('same clinic and user do not authorize a foreign tenant', () {
    expect(TenantOperationScope.matchesAppointment(session(), a), isTrue);
    expect(
      TenantOperationScope.matchesAppointment(session(tenant: tenantB), a),
      isFalse,
    );
    expect(
      TenantOperationScope.matchesAppointment(session(user: 8), a),
      isFalse,
    );
    expect(
      TenantOperationScope.matchesAppointment(session(clinic: 4), a),
      isFalse,
    );
  });
  test('clinical appointment role allowlist stays narrow', () {
    for (final role in ['admin', 'doctor', 'nurse', 'receptionist']) {
      expect(
        TenantOperationScope.appointmentContext(session(role: role)),
        isNotNull,
      );
    }
    for (final role in [
      'patient',
      'lab_technician',
      'pharmacist',
      'super_admin',
      'other',
    ]) {
      expect(
        TenantOperationScope.appointmentContext(session(role: role)),
        isNull,
      );
    }
  });
  test(
    'ciphertext cannot be decrypted under a foreign tenant or legacy scope',
    () async {
      final encrypted = await crypto.encrypt(
        identity: identity('cipher'),
        context: a,
        payload: {'value': 'synthetic'},
      );
      await crypto.encrypt(
        identity: identity('cipher-other'),
        context: b,
        payload: {'value': 'other'},
      );
      expect(
        await crypto.decrypt(
          identity: identity('cipher'),
          context: a,
          encrypted: encrypted,
        ),
        {'value': 'synthetic'},
      );
      await expectLater(
        crypto.decrypt(
          identity: identity('cipher'),
          context: b,
          encrypted: encrypted,
        ),
        throwsA(isA<OperationPayloadAuthenticationException>()),
      );
      await expectLater(
        crypto.decrypt(
          identity: identity('cipher'),
          context: legacy,
          encrypted: encrypted,
        ),
        throwsA(isA<OperationPayloadKeyUnavailableException>()),
      );
    },
  );
  test(
    'queue metadata and status counts exclude other tenant and legacy rows',
    () async {
      await enqueue('a', a);
      await enqueue('b', b);
      await enqueue('old', legacy);
      final rows = await store.listScopedOperationPage(
        context: a,
        operationType: 'appointment.create',
      );
      expect(rows.map((row) => row.operationId), ['a']);
      final counts = await ScopedSyncStatusSource(database: database)
          .loadForContext(a);
      expect(counts.pending, 1);
      expect(counts.needsReview, 0);
      expect(
        await store.hasUnresolvedLegacyScope(userId: 7, clinicId: 3),
        isTrue,
      );
      expect(
        await store.hasUnresolvedLegacyScope(userId: 8, clinicId: 3),
        isFalse,
      );
    },
  );
  test(
    'legacy hold blocks replay and preserves both old and new entries',
    () async {
      await enqueue('old', legacy);
      await enqueue('new', a);
      var calls = 0;
      final worker = OperationReplayWorker(
        queueStore: store,
        loadSession: () async => session(),
        clock: () => now,
        sendAppointmentCreate:
            ({
              required path,
              required body,
              required bearerToken,
              required idempotencyKey,
            }) async {
              calls++;
              return {'success': true};
            },
      );
      final result = await worker.runOnce();
      expect(result.blockedReason, 'legacyScopeRequiresReview');
      expect(calls, 0);
      expect(
        (await store.findByOperationId('old'))?.lifecycleState,
        OperationLifecycleState.queued.name,
      );
      expect(
        (await store.findByOperationId('new'))?.lifecycleState,
        OperationLifecycleState.queued.name,
      );
      expect(
        await store.loadPayload(operationId: 'old', context: legacy),
        isNotEmpty,
      );
    },
  );
  test(
    'foreign tenant replay never sends another tenant queued work',
    () async {
      await enqueue('foreign', a);
      var calls = 0;
      final worker = OperationReplayWorker(
        queueStore: store,
        loadSession: () async => session(tenant: tenantB),
        clock: () => now,
        sendAppointmentCreate:
            ({
              required path,
              required body,
              required bearerToken,
              required idempotencyKey,
            }) async {
              calls++;
              return {'success': true};
            },
      );
      await worker.runOnce();
      expect(calls, 0);
      expect(
        (await store.findByOperationId('foreign'))?.lifecycleState,
        OperationLifecycleState.queued.name,
      );
    },
  );
  test(
    'discard cannot act on unbound legacy record or another tenant',
    () async {
      await enqueue('legacy', legacy);
      await enqueue('foreign', b);
      var calls = 0;
      final service = OperationDiscardService(
        queueStore: store,
        loadSession: () async => session(),
        sendDisposition:
            ({required path, required body, required bearerToken}) async {
              calls++;
              return {'success': true};
            },
      );
      expect(
        (await service.discard('legacy')).status,
        OperationDiscardStatus.contextMismatch,
      );
      expect(
        (await service.discard('foreign')).status,
        OperationDiscardStatus.contextMismatch,
      );
      expect(calls, 0);
      expect(await store.findByOperationId('legacy'), isNotNull);
      expect(await store.findByOperationId('foreign'), isNotNull);
    },
  );
  test(
    'tenant switch before disposition prevents network and deletion',
    () async {
      await enqueue('switch', a);
      var reads = 0;
      var calls = 0;
      final service = OperationDiscardService(
        queueStore: store,
        loadSession: () async =>
            reads++ == 0 ? session() : session(tenant: tenantB),
        sendDisposition:
            ({required path, required body, required bearerToken}) async {
              calls++;
              return {'success': true};
            },
      );
      expect(
        (await service.discard('switch')).status,
        OperationDiscardStatus.sessionUnavailable,
      );
      expect(calls, 0);
      expect(await store.findByOperationId('switch'), isNotNull);
    },
  );
}
