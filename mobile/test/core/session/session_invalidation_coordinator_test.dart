import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/session/session_invalidation_coordinator.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_scheduler.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_worker.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_store.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const _session = AuthTokenResponse(
  accessToken: 'a.b.c',
  refreshToken: 'd.e.f',
  userId: 7,
  role: 'RECEPTIONIST',
  clinicContextId: 3,
);

class _MemorySecureStorage implements SecureKeyValueStorage {
  final Map<String, String> values = <String, String>{};
  bool failDelete = false;

  @override
  Future<String?> read({required String key}) async => values[key];

  @override
  Future<void> write({required String key, required String value}) async {
    values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    if (failDelete) throw StateError('Injected secure-storage delete failure.');
    values.remove(key);
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
    Future<void>.delayed(const Duration(milliseconds: 20));

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('authenticated HTTP 401 invalidates navigation, waits for active replay, and retains queue keys', () async {
    final storage = _MemorySecureStorage();
    storage.values['fixture.queue-encryption-key'] = 'retained-key';
    final sessionStore = AuthSessionStore(storage: storage);
    await sessionStore.save(_session);

    final httpClient = MockClient((request) async {
      return http.Response('{"success":false}', 401);
    });
    final apiClient = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: httpClient,
    );
    final sessionCoordinator = AuthSessionCoordinator(
      sessionStore: sessionStore,
      remoteDataSource: AuthRemoteDataSource(apiClient: apiClient),
    );

    final connectivity = _FakeConnectivity(DeviceNetworkStatus.connected);
    final replayStarted = Completer<void>();
    final releaseReplay = Completer<void>();
    var replayCalls = 0;
    final scheduler = OperationReplayScheduler(
      connectivitySource: connectivity,
      runReplay: () async {
        replayCalls++;
        if (replayCalls == 1) {
          replayStarted.complete();
          await releaseReplay.future;
        }
        return OperationReplayRunResult(
          items: const <OperationReplayItemResult>[],
        );
      },
    );
    final guard = SessionNavigationGuard()..markSessionAvailable();
    final invalidator = SessionInvalidationCoordinator(
      sessionCoordinator: sessionCoordinator,
      sessionNavigationGuard: guard,
      replayScheduler: scheduler,
    );
    Future<void>? invalidationFuture;
    apiClient.onAuthenticationRejected = () {
      invalidationFuture ??= invalidator
          .handleDefinitiveAuthenticationRejection();
      unawaited(invalidationFuture!);
    };

    try {
      await scheduler.start();
      await replayStarted.future;

      await expectLater(
        apiClient.getJson('api/v1/patients', bearerToken: _session.accessToken),
        throwsA(
          isA<ApiException>().having(
            (error) => error.statusCode,
            'statusCode',
            401,
          ),
        ),
      );

      expect(guard.status, SessionNavigationStatus.invalidated);
      expect(guard.redirect('/patients/register'), '/login');
      expect(storage.values[AuthSessionStore.storageKey], isNotNull);

      connectivity.emit(DeviceNetworkStatus.connected);
      await _settle();
      expect(replayCalls, 1);
      expect(storage.values[AuthSessionStore.storageKey], isNotNull);

      releaseReplay.complete();
      await invalidationFuture!;

      expect(storage.values[AuthSessionStore.storageKey], isNull);
      expect(storage.values['fixture.queue-encryption-key'], 'retained-key');
      expect(guard.status, SessionNavigationStatus.signedOut);

      connectivity.emit(DeviceNetworkStatus.connected);
      await _settle();
      expect(
        replayCalls,
        1,
        reason: 'Replay must remain paused after invalidation.',
      );
    } finally {
      if (!releaseReplay.isCompleted) releaseReplay.complete();
      final pendingInvalidation = invalidationFuture;
      if (pendingInvalidation != null) await pendingInvalidation;
      await scheduler.dispose();
      await connectivity.close();
      guard.dispose();
      apiClient.close();
      httpClient.close();
    }
  });

  test('secure-storage deletion failure keeps access invalidated and replay paused', () async {
    final storage = _MemorySecureStorage();
    await AuthSessionStore(storage: storage).save(_session);
    final httpClient = MockClient((_) async => http.Response('{}', 200));
    final apiClient = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: httpClient,
    );
    final sessionCoordinator = AuthSessionCoordinator(
      sessionStore: AuthSessionStore(storage: storage),
      remoteDataSource: AuthRemoteDataSource(apiClient: apiClient),
    );
    final connectivity = _FakeConnectivity(DeviceNetworkStatus.disconnected);
    var replayCalls = 0;
    final scheduler = OperationReplayScheduler(
      connectivitySource: connectivity,
      runReplay: () async {
        replayCalls++;
        return OperationReplayRunResult(
          items: const <OperationReplayItemResult>[],
        );
      },
    );
    final guard = SessionNavigationGuard()..markSessionAvailable();
    final invalidator = SessionInvalidationCoordinator(
      sessionCoordinator: sessionCoordinator,
      sessionNavigationGuard: guard,
      replayScheduler: scheduler,
    );

    try {
      await scheduler.start();
      storage.failDelete = true;
      await invalidator.handleDefinitiveAuthenticationRejection();

      expect(guard.status, SessionNavigationStatus.invalidated);
      expect(guard.redirect('/patients/42'), '/login');
      expect(storage.values[AuthSessionStore.storageKey], isNotNull);

      connectivity.emit(DeviceNetworkStatus.connected);
      await _settle();
      expect(replayCalls, 0);
    } finally {
      await scheduler.dispose();
      await connectivity.close();
      guard.dispose();
      apiClient.close();
      httpClient.close();
    }
  });
}
