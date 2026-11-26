import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/blood_bank/data/blood_bank_source.dart';
import 'package:clinic_system_pro/features/blood_bank/data/blood_bank_contract.dart';

import '../../features/blood_bank/blood_bank_contract_test.dart' show policyRow;

import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/storage/app_database.dart';
import 'package:clinic_system_pro/core/sync/operation_payload_crypto.dart';
import 'package:clinic_system_pro/core/sync/operation_queue_store.dart';
import 'package:clinic_system_pro/core/sync/run2_blood_bank_fence.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

class _Storage implements SecureKeyValueStorage {
  final data = <String, String>{};
  @override
  Future<String?> read({required String key}) async => data[key];
  @override
  Future<void> write({required String key, required String value}) async {
    data[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    data.remove(key);
  }
}

AuthTokenResponse actor({
  String role = 'nurse',
  int clinic = 3,
  int user = 7,
  String tenant = '12345678-1234-1234-1234-123456789abc',
}) => AuthTokenResponse(
  accessToken: 'a',
  refreshToken: 'b',
  userId: user,
  role: role,
  clinicContextId: clinic,
  tenantId: tenant,
);

void main() {
  late AppDatabase db;
  late OperationQueueStore store;
  late Run2BloodBankFence fence;
  setUp(() {
    db = AppDatabase(NativeDatabase.memory());
    store = OperationQueueStore(
      db,
      payloadCrypto: OperationPayloadCrypto(secureStorage: _Storage()),
    );
    fence = Run2BloodBankFence(store);
  });
  tearDown(() async => db.close());

  test('actual BloodBankSource blocks second POST after reconstructed source and lost response', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
        return http.Response(jsonEncode({'error': 'uncertain'}), 500);
      }
      return http.Response(jsonEncode(policyRow()), 200);
    });
    addTearDown(client.close);
    BloodBankSource makeSource() => BloodBankSource(
      api: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
      loadSession: () async =>
          AuthSessionState.ready(actor(role: 'lab_technician')),
      guard: guard,
      fence: Run2BloodBankFence(store),
    );
    final first = await makeSource().open();
    await expectLater(
      first.submit(BbAction.expire, {}),
      throwsA(isA<BbUnknownOutcome>()),
    );
    final second = await makeSource().open();
    expect(second.writeBlocked, isTrue);
    await expectLater(
      second.submit(BbAction.expire, {}),
      throwsA(isA<BbUnknownOutcome>()),
    );
    expect(posts, 1);
  });
  test(
    'unconfirmed live action remains fenced in a newly constructed source',
    () async {
      final id = await fence.begin(actor());
      final restarted = Run2BloodBankFence(store);
      expect(await restarted.blocked(actor()), isTrue);
      await expectLater(restarted.begin(actor()), throwsStateError);
      expect(await store.findByOperationId(id), isNotNull);
    },
  );
  test(
    'role change cannot bypass fence and foreign tenant cannot clear it',
    () async {
      final id = await fence.begin(actor());
      expect(await fence.blocked(actor(role: 'doctor')), isTrue);
      expect(await fence.blocked(actor(clinic: 4)), isFalse);
      expect(await fence.blocked(actor(user: 8)), isFalse);
      final foreign = actor(tenant: '87654321-4321-4321-4321-cba987654321');
      expect(await fence.blocked(foreign), isFalse);
      await expectLater(fence.complete(foreign, id), throwsStateError);
      expect(await fence.blocked(actor()), isTrue);
    },
  );
  test(
    'only exact completed fence is removed; no clinical payload is stored',
    () async {
      final id = await fence.begin(actor());
      final row = (await store.findByOperationId(id))!;
      final context = OperationPayloadContext(
        userId: row.ownerUserId!,
        clinicContextId: row.clinicContextId!,
      );
      expect(await store.loadPayload(operationId: id, context: context), {
        'kind': 'blood_bank_write_fence',
      });
      await expectLater(
        store.removeRun2LocalOnly(
          operationId: id,
          context: context,
          type: Run2BloodBankFence.type,
        ),
        throwsStateError,
      );
      await expectLater(fence.complete(actor(), 'wrong'), throwsStateError);
      expect(await fence.blocked(actor()), isTrue);
      await fence.complete(actor(), id);
      expect(await fence.blocked(actor()), isFalse);
      expect(await store.findByOperationId(id), isNull);
    },
  );
  test(
    'fence record is never selected by appointment or preset delivery scope',
    () async {
      await fence.begin(actor());
      final base = OperationPayloadContext(
        userId: '7',
        clinicContextId: 'tenant-v1:${actor().tenantId}:3',
      );
      final ready = await store.listReady(
        context: base,
        operationTypes: const {
          'appointment.create',
          'feedback.product.preset',
          'chat.message.preset',
        },
      );
      expect(ready, isEmpty);
    },
  );
}
