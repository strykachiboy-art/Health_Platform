import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_store.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const _tenantA = '123e4567-e89b-42d3-a456-426614174001';
const _tenantB = '123e4567-e89b-42d3-a456-426614174002';

class _SecureStorage implements SecureKeyValueStorage {
  final data = <String, String>{};
  @override
  Future<void> write({required String key, required String value}) async {
    data[key] = value;
  }

  @override
  Future<String?> read({required String key}) async => data[key];
  @override
  Future<void> delete({required String key}) async {
    data.remove(key);
  }
}

const _original = AuthTokenResponse(
  accessToken: 'aaa.bbb.ccc',
  refreshToken: 'ddd.eee.fff',
  userId: 19,
  role: 'DOCTOR',
  clinicContextId: null,
);

AuthRemoteDataSource _source(MockClient client) => AuthRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
);

String _tokens(String? tenantId, int? clinicId, {String role = 'DOCTOR'}) =>
    jsonEncode({
      'success': true,
      'data': {
        'access_token': 'ggg.hhh.iii',
        'refresh_token': 'jjj.kkk.lll',
        'user_id': 19,
        'role': role,
        'tenant_id': tenantId,
        'clinic_context_id': clinicId,
      },
    });

void main() {
  test('GAP-B tenant ID parser rejects forged or malformed identifiers', () {
    expect(
      AuthTokenResponse.fromJson({
        'access_token': 'aaa.bbb.ccc',
        'refresh_token': 'ddd.eee.fff',
        'user_id': 19,
        'role': 'DOCTOR',
        'clinic_context_id': null,
        'tenant_id': _tenantA,
      }).tenantId,
      _tenantA,
    );
    for (final value in ['not-a-uuid', '', 3, true]) {
      expect(
        () => AuthTokenResponse.fromJson({
          'access_token': 'aaa.bbb.ccc',
          'refresh_token': 'ddd.eee.fff',
          'user_id': 19,
          'role': 'DOCTOR',
          'clinic_context_id': null,
          'tenant_id': value,
        }),
        throwsFormatException,
      );
    }
  });

  test(
    'GAP-B tenant context GET parses only canonical bound identifiers',
    () async {
      final mock = MockClient((request) async {
        expect(request.url.path, '/api/v1/auth/tenant-context');
        expect(request.headers['authorization'], 'Bearer aaa.bbb.ccc');
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {'tenant_id': _tenantA, 'clinic_id': 7},
          }),
          200,
        );
      });
      addTearDown(mock.close);
      final context = await _source(mock)
          .getTenantContext(accessToken: _original.accessToken);
      expect(context.tenantId, _tenantA);
      expect(context.clinicId, 7);
    },
  );

  test(
    'GAP-B selects organization once, rotates both tokens and persists scope',
    () async {
      var calls = 0;
      final mock = MockClient((request) async {
        calls++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/auth/tenant-context');
        expect(jsonDecode(request.body), {'organization_id': _tenantA});
        return http.Response(_tokens(_tenantA, null), 200);
      });
      addTearDown(mock.close);
      final store = AuthSessionStore(storage: _SecureStorage());
      await store.save(_original);
      final coordinator = AuthSessionCoordinator(
        sessionStore: store,
        remoteDataSource: _source(mock),
      );
      final outcome = await coordinator.changeTenantContext(tenantId: _tenantA);
      expect(outcome.disposition, AuthSessionDisposition.ready);
      expect(outcome.session?.tenantId, _tenantA);
      expect((await store.read())?.tenantId, _tenantA);
      expect((await store.read())?.clinicContextId, isNull);
      expect((await store.readRecord())?.refreshPending, isFalse);
      expect(calls, 1);
    },
  );

  test(
    'GAP-B selects clinic only for existing selected organization',
    () async {
      var calls = 0;
      final mock = MockClient((request) async {
        calls++;
        expect(request.url.path, '/api/v1/auth/tenant-context/clinic');
        expect(jsonDecode(request.body), {'clinic_id': 7});
        return http.Response(_tokens(_tenantA, 7), 200);
      });
      addTearDown(mock.close);
      final store = AuthSessionStore(storage: _SecureStorage());
      await store.save(
        const AuthTokenResponse(
          accessToken: 'aaa.bbb.ccc',
          refreshToken: 'ddd.eee.fff',
          userId: 19,
          role: 'DOCTOR',
          tenantId: _tenantA,
          clinicContextId: null,
        ),
      );
      final coordinator = AuthSessionCoordinator(
        sessionStore: store,
        remoteDataSource: _source(mock),
      );
      final outcome = await coordinator.changeTenantContext(clinicId: 7);
      expect(outcome.disposition, AuthSessionDisposition.ready);
      expect(outcome.session?.clinicContextId, 7);
      expect(calls, 1);
    },
  );

  test('GAP-B rejects forged new tenant in clinic switch and leaves recovery marker', () async {
    final mock = MockClient(
      (_) async => http.Response(_tokens(_tenantB, 7), 200),
    );
    addTearDown(mock.close);
    final store = AuthSessionStore(storage: _SecureStorage());
    await store.save(
      const AuthTokenResponse(
        accessToken: 'aaa.bbb.ccc',
        refreshToken: 'ddd.eee.fff',
        userId: 19,
        role: 'DOCTOR',
        tenantId: _tenantA,
        clinicContextId: null,
      ),
    );
    final coordinator = AuthSessionCoordinator(
      sessionStore: store,
      remoteDataSource: _source(mock),
    );
    final outcome = await coordinator.changeTenantContext(clinicId: 7);
    expect(outcome.disposition, AuthSessionDisposition.recoveryRequired);
    expect((await store.readRecord())?.refreshPending, isTrue);
  });

  test(
    'GAP-B unselected organization blocks clinic POST before transport',
    () async {
      var calls = 0;
      final mock = MockClient((_) async {
        calls++;
        throw StateError('No request allowed');
      });
      addTearDown(mock.close);
      final store = AuthSessionStore(storage: _SecureStorage());
      await store.save(_original);
      final coordinator = AuthSessionCoordinator(
        sessionStore: store,
        remoteDataSource: _source(mock),
      );
      final outcome = await coordinator.changeTenantContext(clinicId: 7);
      expect(outcome.disposition, AuthSessionDisposition.recoveryRequired);
      expect(calls, 0);
    },
  );

  test(
    'GAP-B timeout keeps old workspace locked and never auto retries',
    () async {
      var calls = 0;
      final mock = MockClient((_) async {
        calls++;
        throw http.ClientException('timeout');
      });
      addTearDown(mock.close);
      final store = AuthSessionStore(storage: _SecureStorage());
      await store.save(_original);
      final coordinator = AuthSessionCoordinator(
        sessionStore: store,
        remoteDataSource: _source(mock),
      );
      final outcome = await coordinator.changeTenantContext(tenantId: _tenantA);
      expect(outcome.disposition, AuthSessionDisposition.recoveryRequired);
      expect((await store.readRecord())?.refreshPending, isTrue);
      expect(calls, 1);
    },
  );

  test(
    'GAP-B secure record accepts existing v2 session without implicit grant',
    () async {
      final storage = _SecureStorage();
      storage.data[AuthSessionStore.storageKey] = jsonEncode({
        'schema_version': 2,
        'refresh_pending': false,
        'access_token': 'aaa.bbb.ccc',
        'refresh_token': 'ddd.eee.fff',
        'user_id': 19,
        'role': 'DOCTOR',
        'clinic_context_id': null,
      });
      final previous = await AuthSessionStore(storage: storage).read();
      expect(previous?.tenantId, isNull);
    },
  );
}
