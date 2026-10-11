import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_store.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const _accessToken =
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiJ9.old-access-signature';
const _refreshToken =
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiIsInR5cGUiOiJyZWZyZXNoIn0.old-refresh-signature';
const _newAccessToken =
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiJ9.new-access-signature';
const _newRefreshToken =
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiIsInR5cGUiOiJyZWZyZXNoIn0.new-refresh-signature';

BackendConfig _testConfig() => const BackendConfig(
  baseUrl: 'http://127.0.0.1:5000',
  allowInsecureHttp: true,
);

AuthTokenResponse _oldSession() => const AuthTokenResponse(
  accessToken: _accessToken,
  refreshToken: _refreshToken,
  userId: 42,
  role: 'DOCTOR',
  clinicContextId: null,
);

Map<String, dynamic> _replacementEnvelope() => {
  'success': true,
  'data': {
    'access_token': _newAccessToken,
    'refresh_token': _newRefreshToken,
    'user_id': 42,
    'role': 'DOCTOR',
    'clinic_context_id': null,
  },
};

class _MemorySecureStorage implements SecureKeyValueStorage {
  final Map<String, String> values = <String, String>{};
  int writeCount = 0;
  int? failWriteNumber;

  @override
  Future<String?> read({required String key}) async => values[key];

  @override
  Future<void> write({required String key, required String value}) async {
    writeCount++;
    if (writeCount == failWriteNumber) {
      throw StateError('Injected secure-storage write failure.');
    }
    values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    values.remove(key);
  }
}

AuthSessionStore _store(_MemorySecureStorage storage) =>
    AuthSessionStore(storage: storage);

AuthSessionCoordinator _coordinator({
  required _MemorySecureStorage storage,
  required Future<http.Response> Function(http.Request request) handler,
  Duration requestTimeout = const Duration(seconds: 10),
}) {
  final client = MockClient(handler);
  addTearDown(client.close);

  return AuthSessionCoordinator(
    sessionStore: _store(storage),
    remoteDataSource: AuthRemoteDataSource(
      apiClient: ApiClient(
        config: _testConfig(),
        client: client,
        requestTimeout: requestTimeout,
      ),
    ),
  );
}

bool _pendingMarker(_MemorySecureStorage storage) {
  final raw = storage.values[AuthSessionStore.storageKey];
  if (raw == null) {
    return false;
  }
  final record = jsonDecode(raw) as Map<String, dynamic>;
  return record['refresh_pending'] as bool;
}

void main() {
  group('AuthSessionCoordinator', () {
    test('restores a saved session as ready without network I/O', () async {
      final storage = _MemorySecureStorage();
      await _store(storage).save(_oldSession());

      var requests = 0;
      final coordinator = _coordinator(
        storage: storage,
        handler: (_) async {
          requests++;
          fail('Restore must not perform network I/O.');
        },
      );

      final state = await coordinator.restoreSession();

      expect(state.disposition, AuthSessionDisposition.ready);
      expect(state.session?.refreshToken, _refreshToken);
      expect(requests, 0);
    });

    test(
      'restores a pending record as recovery required without retrying',
      () async {
        final storage = _MemorySecureStorage();
        await _store(storage).save(_oldSession(), refreshPending: true);

        var requests = 0;
        final coordinator = _coordinator(
          storage: storage,
          handler: (_) async {
            requests++;
            fail('A pending refresh must never retry the old token.');
          },
        );

        final state = await coordinator.restoreSession();
        final refreshState = await coordinator.refreshSession();

        expect(state.disposition, AuthSessionDisposition.recoveryRequired);
        expect(state.session, isNull);
        expect(
          refreshState.disposition,
          AuthSessionDisposition.recoveryRequired,
        );
        expect(requests, 0);
        expect(_pendingMarker(storage), isTrue);
      },
    );

    test(
      'writes the pending marker before refresh and atomically saves the pair',
      () async {
        final storage = _MemorySecureStorage();
        await _store(storage).save(_oldSession());

        var requests = 0;
        final coordinator = _coordinator(
          storage: storage,
          handler: (request) async {
            requests++;
            expect(request.url.path, '/api/v1/auth/refresh');
            expect(request.headers['authorization'], 'Bearer $_refreshToken');
            expect(_pendingMarker(storage), isTrue);
            return http.Response(jsonEncode(_replacementEnvelope()), 200);
          },
        );

        final state = await coordinator.refreshSession();
        final saved = await _store(storage).readRecord();

        expect(state.disposition, AuthSessionDisposition.ready);
        expect(state.session?.accessToken, _newAccessToken);
        expect(state.session?.refreshToken, _newRefreshToken);
        expect(saved?.refreshPending, isFalse);
        expect(saved?.session.accessToken, _newAccessToken);
        expect(saved?.session.refreshToken, _newRefreshToken);
        expect(requests, 1);
      },
    );

    test(
      'does not send refresh if persisting the pending marker fails',
      () async {
        final storage = _MemorySecureStorage();
        await _store(storage).save(_oldSession());
        storage.failWriteNumber = storage.writeCount + 1;

        var requests = 0;
        final coordinator = _coordinator(
          storage: storage,
          handler: (_) async {
            requests++;
            return http.Response(jsonEncode(_replacementEnvelope()), 200);
          },
        );

        await expectLater(
          coordinator.refreshSession(),
          throwsA(isA<StateError>()),
        );

        expect(requests, 0);
        expect(_pendingMarker(storage), isFalse);
      },
    );

    test('confirmed 401 rejection clears the local session', () async {
      final storage = _MemorySecureStorage();
      await _store(storage).save(_oldSession());

      final coordinator = _coordinator(
        storage: storage,
        handler: (_) async => http.Response(
          jsonEncode({'success': false, 'error': 'Token has been revoked'}),
          401,
        ),
      );

      final state = await coordinator.refreshSession();

      expect(state.disposition, AuthSessionDisposition.signedOut);
      expect(storage.values.containsKey(AuthSessionStore.storageKey), isFalse);
    });

    test(
      'confirmed invalid-token 422 rejection clears the local session',
      () async {
        final storage = _MemorySecureStorage();
        await _store(storage).save(_oldSession());

        final coordinator = _coordinator(
          storage: storage,
          handler: (_) async =>
              http.Response(jsonEncode({'msg': 'Invalid token'}), 422),
        );

        final state = await coordinator.refreshSession();

        expect(state.disposition, AuthSessionDisposition.signedOut);
        expect(
          storage.values.containsKey(AuthSessionStore.storageKey),
          isFalse,
        );
      },
    );

    test(
      'connection failure leaves pending marker and requires recovery',
      () async {
        final storage = _MemorySecureStorage();
        await _store(storage).save(_oldSession());

        var requests = 0;
        final coordinator = _coordinator(
          storage: storage,
          handler: (_) async {
            requests++;
            throw http.ClientException(
              'Connection lost after request started.',
            );
          },
        );

        final state = await coordinator.refreshSession();
        final restored = await coordinator.restoreSession();

        expect(state.disposition, AuthSessionDisposition.recoveryRequired);
        expect(state.cause, isA<ApiException>());
        expect((state.cause! as ApiException).kind, ApiFailureKind.connection);
        expect(restored.disposition, AuthSessionDisposition.recoveryRequired);
        expect(_pendingMarker(storage), isTrue);
        expect(requests, 1);
      },
    );

    test('timeout leaves pending marker and requires recovery', () async {
      final storage = _MemorySecureStorage();
      await _store(storage).save(_oldSession());

      final requestStarted = Completer<void>();
      var requests = 0;
      final coordinator = _coordinator(
        storage: storage,
        requestTimeout: const Duration(milliseconds: 10),
        handler: (_) {
          requests++;
          if (!requestStarted.isCompleted) {
            requestStarted.complete();
          }
          return Completer<http.Response>().future;
        },
      );

      final state = await coordinator.refreshSession();

      await requestStarted.future;
      expect(state.disposition, AuthSessionDisposition.recoveryRequired);
      expect(state.cause, isA<ApiException>());
      expect((state.cause! as ApiException).kind, ApiFailureKind.timeout);
      expect(_pendingMarker(storage), isTrue);
      expect(requests, 1);
    });

    test(
      'server error stays uncertain and does not clear the pending marker',
      () async {
        final storage = _MemorySecureStorage();
        await _store(storage).save(_oldSession());

        final coordinator = _coordinator(
          storage: storage,
          handler: (_) async => http.Response(
            jsonEncode({'success': false, 'error': 'Temporary server error'}),
            503,
          ),
        );

        final state = await coordinator.refreshSession();

        expect(state.disposition, AuthSessionDisposition.recoveryRequired);
        expect(state.cause, isA<ApiException>());
        expect(_pendingMarker(storage), isTrue);
      },
    );

    test(
      'malformed successful response is uncertain and remains pending',
      () async {
        final storage = _MemorySecureStorage();
        await _store(storage).save(_oldSession());

        final coordinator = _coordinator(
          storage: storage,
          handler: (_) async => http.Response(
            jsonEncode({'success': true, 'data': <String, dynamic>{}}),
            200,
          ),
        );

        final state = await coordinator.refreshSession();

        expect(state.disposition, AuthSessionDisposition.recoveryRequired);
        expect(state.cause, isA<FormatException>());
        expect(_pendingMarker(storage), isTrue);
      },
    );

    test('replacement-session write failure leaves recovery marker', () async {
      final storage = _MemorySecureStorage();
      await _store(storage).save(_oldSession());
      // Write 1 saved the old session; write 2 marks refresh pending; write 3
      // attempts to save the replacement pair and clear the marker.
      storage.failWriteNumber = storage.writeCount + 2;

      final coordinator = _coordinator(
        storage: storage,
        handler: (_) async =>
            http.Response(jsonEncode(_replacementEnvelope()), 200),
      );

      final state = await coordinator.refreshSession();
      final stored = await _store(storage).readRecord();

      expect(state.disposition, AuthSessionDisposition.recoveryRequired);
      expect(state.cause, isA<StateError>());
      expect(stored?.refreshPending, isTrue);
      expect(stored?.session.refreshToken, _refreshToken);
    });

    test('concurrent refresh callers share one rotation request', () async {
      final storage = _MemorySecureStorage();
      await _store(storage).save(_oldSession());

      final requestStarted = Completer<void>();
      final responseCompleter = Completer<http.Response>();
      var requests = 0;

      final coordinator = _coordinator(
        storage: storage,
        handler: (_) {
          requests++;
          if (!requestStarted.isCompleted) {
            requestStarted.complete();
          }
          return responseCompleter.future;
        },
      );

      final first = coordinator.refreshSession();
      final second = coordinator.refreshSession();

      await requestStarted.future;
      responseCompleter.complete(
        http.Response(jsonEncode(_replacementEnvelope()), 200),
      );

      final states = await Future.wait([first, second]);

      expect(requests, 1);
      expect(states[0].disposition, AuthSessionDisposition.ready);
      expect(states[1].disposition, AuthSessionDisposition.ready);
      expect(
        (await _store(storage).readRecord())?.session.refreshToken,
        _newRefreshToken,
      );
    });
  });
}
