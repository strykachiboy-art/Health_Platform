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

const _oldAccess = 'aaa.bbb.ccc';
const _oldRefresh = 'ddd.eee.fff';
const _newAccess = 'ggg.hhh.iii';
const _newRefresh = 'jjj.kkk.lll';
final _config = BackendConfig(
  baseUrl: 'http://127.0.0.1:5000',
  allowInsecureHttp: true,
);

class _MemorySecureStorage implements SecureKeyValueStorage {
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

const _oldSession = AuthTokenResponse(
  accessToken: _oldAccess,
  refreshToken: _oldRefresh,
  userId: 77,
  role: 'SUPER_ADMIN',
  clinicContextId: null,
);

Map<String, dynamic> _tokens(
  int? clinic, {
  int userId = 77,
  String role = 'SUPER_ADMIN',
}) => {
  'success': true,
  'data': {
    'access_token': _newAccess,
    'refresh_token': _newRefresh,
    'user_id': userId,
    'role': role,
    'clinic_context_id': clinic,
  },
};

void main() {
  test(
    'S05 gate 2: POST selects clinic and rotates both tokens atomically',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/auth/clinic-context');
        expect(request.headers['authorization'], 'Bearer $_oldAccess');
        expect(jsonDecode(request.body), {'clinic_id': 9});
        return http.Response(jsonEncode(_tokens(9)), 200);
      });
      addTearDown(client.close);
      final api = ApiClient(config: _config, client: client);
      addTearDown(api.close);
      final storage = _MemorySecureStorage();
      final store = AuthSessionStore(storage: storage);
      await store.save(_oldSession);
      final coordinator = AuthSessionCoordinator(
        sessionStore: store,
        remoteDataSource: AuthRemoteDataSource(apiClient: api),
      );
      final result = await coordinator.changeClinicContext(clinicId: 9);
      expect(result.disposition, AuthSessionDisposition.ready);
      expect(result.session?.clinicContextId, 9);
      expect((await store.readRecord())?.refreshPending, isFalse);
      expect((await store.read())?.accessToken, _newAccess);
      expect(calls, 1);
    },
  );

  test('S05 gate 2: DELETE clears clinic using existing API', () async {
    final client = MockClient((request) async {
      expect(request.method, 'DELETE');
      expect(request.url.path, '/api/v1/auth/clinic-context');
      expect(request.headers['authorization'], 'Bearer $_oldAccess');
      expect(request.body, isEmpty);
      return http.Response(jsonEncode(_tokens(null)), 200);
    });
    addTearDown(client.close);
    final api = ApiClient(config: _config, client: client);
    addTearDown(api.close);
    final storage = _MemorySecureStorage();
    final store = AuthSessionStore(storage: storage);
    await store.save(_oldSession);
    final coordinator = AuthSessionCoordinator(
      sessionStore: store,
      remoteDataSource: AuthRemoteDataSource(apiClient: api),
    );
    final result = await coordinator.changeClinicContext();
    expect(result.disposition, AuthSessionDisposition.ready);
    expect(result.session?.clinicContextId, isNull);
    expect((await store.readRecord())?.refreshPending, isFalse);
  });

  test(
    'S05 gate 2: malformed identity response cannot authorize switched clinic',
    () async {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_tokens(4, userId: 500)), 200),
      );
      addTearDown(client.close);
      final api = ApiClient(config: _config, client: client);
      addTearDown(api.close);
      final store = AuthSessionStore(storage: _MemorySecureStorage());
      await store.save(_oldSession);
      final coordinator = AuthSessionCoordinator(
        sessionStore: store,
        remoteDataSource: AuthRemoteDataSource(apiClient: api),
      );
      final result = await coordinator.changeClinicContext(clinicId: 4);
      expect(result.disposition, AuthSessionDisposition.recoveryRequired);
      expect((await store.readRecord())?.refreshPending, isTrue);
      expect(
        (await coordinator.restoreSession()).disposition,
        AuthSessionDisposition.recoveryRequired,
      );
    },
  );

  test(
    'S05 gate 2: timeout/unknown outcome never reuses old session',
    () async {
      final client = MockClient(
        (_) async => throw http.ClientException('Injected disconnect'),
      );
      addTearDown(client.close);
      final api = ApiClient(config: _config, client: client);
      addTearDown(api.close);
      final store = AuthSessionStore(storage: _MemorySecureStorage());
      await store.save(_oldSession);
      final coordinator = AuthSessionCoordinator(
        sessionStore: store,
        remoteDataSource: AuthRemoteDataSource(apiClient: api),
      );
      final result = await coordinator.changeClinicContext(clinicId: 4);
      expect(result.disposition, AuthSessionDisposition.recoveryRequired);
      expect((await store.readRecord())?.refreshPending, isTrue);
    },
  );

  test('S05 gate 2: non-super-admin fails before HTTP dispatch', () async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      throw StateError('Non-super-admin must not dispatch request.');
    });
    addTearDown(client.close);
    final api = ApiClient(config: _config, client: client);
    addTearDown(api.close);
    final store = AuthSessionStore(storage: _MemorySecureStorage());
    await store.save(
      const AuthTokenResponse(
        accessToken: _oldAccess,
        refreshToken: _oldRefresh,
        userId: 77,
        role: 'DOCTOR',
        clinicContextId: 2,
      ),
    );
    final coordinator = AuthSessionCoordinator(
      sessionStore: store,
      remoteDataSource: AuthRemoteDataSource(apiClient: api),
    );
    final result = await coordinator.changeClinicContext(clinicId: 4);
    expect(result.disposition, AuthSessionDisposition.recoveryRequired);
    expect(requests, 0);
    expect((await store.readRecord())?.refreshPending, isFalse);
  });

  test('S05 gate 2: current context GET is validated', () async {
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/auth/clinic-context');
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'clinic_id': 9,
            'clinic_name': 'North',
            'source': 'selected',
          },
        }),
        200,
      );
    });
    addTearDown(client.close);
    final api = ApiClient(config: _config, client: client);
    addTearDown(api.close);
    final source = AuthRemoteDataSource(apiClient: api);
    final value = await source.getClinicContext(accessToken: _oldAccess);
    expect(value.clinicId, 9);
    expect(value.clinicName, 'North');
  });
}
