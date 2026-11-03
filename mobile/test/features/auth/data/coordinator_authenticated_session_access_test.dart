import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_store.dart';
import 'package:clinic_system_pro/features/auth/data/coordinator_authenticated_session_access.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const _accessToken = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiJ9.signature';

const _refreshToken =
    'eyJhbGciOiJIUzI1NiJ9.'
    'eyJzdWIiOiI0MiIsInR5cGUiOiJyZWZyZXNoIn0.signature';

class _MemorySecureStorage implements SecureKeyValueStorage {
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

AuthTokenResponse _session() => const AuthTokenResponse(
  accessToken: _accessToken,
  refreshToken: _refreshToken,
  userId: 42,
  role: 'DOCTOR',
  clinicContextId: 3,
);

AuthSessionCoordinator _coordinator({
  required AuthSessionStore store,
  required MockClient client,
}) {
  final apiClient = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );

  return AuthSessionCoordinator(
    sessionStore: store,
    remoteDataSource: AuthRemoteDataSource(apiClient: apiClient),
  );
}

MockClient _noNetworkClient() {
  return MockClient((request) async {
    throw StateError(
      'Authenticated-session restoration must not issue HTTP: '
      '${request.method} ${request.url}',
    );
  });
}

void main() {
  group('CoordinatorAuthenticatedSessionAccess', () {
    test(
      'returns access token from ready local session without HTTP',
      () async {
        final storage = _MemorySecureStorage();
        final store = AuthSessionStore(storage: storage);

        await store.save(_session());

        final client = _noNetworkClient();
        addTearDown(client.close);

        final access = CoordinatorAuthenticatedSessionAccess(
          sessionCoordinator: _coordinator(store: store, client: client),
        );

        expect(await access.requireAccessToken(), _accessToken);
      },
    );

    test('reports signedOut when no local session exists', () async {
      final storage = _MemorySecureStorage();
      final store = AuthSessionStore(storage: storage);

      final client = _noNetworkClient();
      addTearDown(client.close);

      final access = CoordinatorAuthenticatedSessionAccess(
        sessionCoordinator: _coordinator(store: store, client: client),
      );

      await expectLater(
        access.requireAccessToken(),
        throwsA(
          isA<AuthenticatedSessionUnavailableException>().having(
            (error) => error.failure,
            'failure',
            AuthenticatedSessionAccessFailure.signedOut,
          ),
        ),
      );
    });

    test(
      'reports recoveryRequired when stored refresh is unfinished',
      () async {
        final storage = _MemorySecureStorage();
        final store = AuthSessionStore(storage: storage);

        await store.save(_session(), refreshPending: true);

        final client = _noNetworkClient();
        addTearDown(client.close);

        final access = CoordinatorAuthenticatedSessionAccess(
          sessionCoordinator: _coordinator(store: store, client: client),
        );

        await expectLater(
          access.requireAccessToken(),
          throwsA(
            isA<AuthenticatedSessionUnavailableException>().having(
              (error) => error.failure,
              'failure',
              AuthenticatedSessionAccessFailure.recoveryRequired,
            ),
          ),
        );
      },
    );

    test(
      'maps corrupt stored session to recoveryRequired without HTTP',
      () async {
        final storage = _MemorySecureStorage();

        await storage.write(
          key: AuthSessionStore.storageKey,
          value: 'not-valid-session-json',
        );

        final store = AuthSessionStore(storage: storage);

        final client = _noNetworkClient();
        addTearDown(client.close);

        final access = CoordinatorAuthenticatedSessionAccess(
          sessionCoordinator: _coordinator(store: store, client: client),
        );

        await expectLater(
          access.requireAccessToken(),
          throwsA(
            isA<AuthenticatedSessionUnavailableException>().having(
              (error) => error.failure,
              'failure',
              AuthenticatedSessionAccessFailure.recoveryRequired,
            ),
          ),
        );
      },
    );
  });
}
