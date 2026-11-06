import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_scheduler.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_store.dart';
import 'package:clinic_system_pro/features/auth/data/authenticated_sign_out_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const _session = AuthTokenResponse(
  accessToken: 'aaa.bbb.ccc',
  refreshToken: 'ddd.eee.fff',
  userId: 42,
  role: 'DOCTOR',
  clinicContextId: 3,
);

class _Storage implements SecureKeyValueStorage {
  final values = <String, String>{};
  bool failDelete = false;

  @override
  Future<String?> read({required String key}) async => values[key];

  @override
  Future<void> write({required String key, required String value}) async {
    values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    if (failDelete) {
      throw StateError('Injected secure storage failure.');
    }
    values.remove(key);
  }
}

class _Disconnected implements DeviceConnectivitySource {
  @override
  Future<DeviceNetworkStatus> checkStatus() async =>
      DeviceNetworkStatus.disconnected;

  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test(
    'S05 gate 3: sign-out invalidates navigation before remote logout',
    () async {
      final storage = _Storage();
      final store = AuthSessionStore(storage: storage);
      await store.save(_session);
      final started = Completer<void>();
      final completeRemote = Completer<http.Response>();
      final mock = MockClient((request) {
        expect(request.url.path, '/api/v1/auth/logout');
        expect(request.headers['authorization'], 'Bearer aaa.bbb.ccc');
        expect(jsonDecode(request.body), <String, Object>{
          'refresh_token': 'ddd.eee.fff',
        });
        started.complete();
        return completeRemote.future;
      });
      addTearDown(mock.close);
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: mock,
      );
      addTearDown(api.close);
      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const <String>[
          '/home',
          '/account',
          '/patients',
        ],
      )..markSessionAvailable();
      addTearDown(guard.dispose);
      final scheduler = OperationReplayScheduler(
        connectivitySource: _Disconnected(),
        runReplay: () async => throw StateError('No replay expected.'),
      );
      addTearDown(scheduler.dispose);
      final coordinator = AuthSessionCoordinator(
        sessionStore: store,
        remoteDataSource: AuthRemoteDataSource(apiClient: api),
      );
      final signOut = AuthenticatedSignOutCoordinator(
        sessionCoordinator: coordinator,
        sessionNavigationGuard: guard,
        replayScheduler: scheduler,
      );
      final future = signOut.signOut();
      expect(guard.canAccessProtectedRoutes, isFalse);
      expect(guard.redirect('/patients/2'), '/login');
      expect(guard.redirect('/account'), '/login');
      await started.future;
      expect(await store.readRecord(), isNull);
      expect(guard.status, SessionNavigationStatus.invalidated);
      completeRemote.complete(http.Response('{"success":true}', 200));
      final result = await future;
      expect(result.remoteStatus, AuthLogoutRemoteStatus.confirmed);
      expect(guard.status, SessionNavigationStatus.signedOut);
      expect(await store.read(), isNull);
    },
  );

  test(
    'S05 gate 3: remote 503 remains uncertain but local logout succeeds',
    () async {
      final storage = _Storage();
      final store = AuthSessionStore(storage: storage);
      await store.save(_session);
      final mock = MockClient(
        (_) async => http.Response('{"success":false}', 503),
      );
      addTearDown(mock.close);
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: mock,
      );
      addTearDown(api.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final scheduler = OperationReplayScheduler(
        connectivitySource: _Disconnected(),
        runReplay: () async => throw StateError('No replay expected.'),
      );
      addTearDown(scheduler.dispose);
      final signOut = AuthenticatedSignOutCoordinator(
        sessionCoordinator: AuthSessionCoordinator(
          sessionStore: store,
          remoteDataSource: AuthRemoteDataSource(apiClient: api),
        ),
        sessionNavigationGuard: guard,
        replayScheduler: scheduler,
      );
      final result = await signOut.signOut();
      expect(result.remoteStatus, AuthLogoutRemoteStatus.uncertain);
      expect(guard.status, SessionNavigationStatus.signedOut);
      expect(await store.read(), isNull);
    },
  );

  test(
    'S05 gate 3: failed secure storage removal keeps routes locked',
    () async {
      final storage = _Storage()..failDelete = true;
      final store = AuthSessionStore(storage: storage);
      await store.save(_session);
      final mock = MockClient((_) async => fail('No remote request expected.'));
      addTearDown(mock.close);
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: mock,
      );
      addTearDown(api.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final scheduler = OperationReplayScheduler(
        connectivitySource: _Disconnected(),
        runReplay: () async => throw StateError('No replay expected.'),
      );
      addTearDown(scheduler.dispose);
      final signOut = AuthenticatedSignOutCoordinator(
        sessionCoordinator: AuthSessionCoordinator(
          sessionStore: store,
          remoteDataSource: AuthRemoteDataSource(apiClient: api),
        ),
        sessionNavigationGuard: guard,
        replayScheduler: scheduler,
      );
      await expectLater(signOut.signOut(), throwsA(isA<StateError>()));
      expect(guard.status, SessionNavigationStatus.invalidated);
      expect(guard.canAccessProtectedRoutes, isFalse);
      expect(await store.read(), isNotNull);
    },
  );
}
