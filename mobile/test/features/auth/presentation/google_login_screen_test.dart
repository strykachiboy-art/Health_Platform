import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_store.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/auth/presentation/google_login_screen.dart';

const _idToken = 'google-id-token';
const _accessToken = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiJ9.signature';
const _refreshToken =
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiIsInR5cGUiOiJyZWZyZXNoIn0.signature';

class _MemorySecureStorage implements SecureKeyValueStorage {
  final Map<String, String> values = <String, String>{};
  Object? readFailure;

  @override
  Future<String?> read({required String key}) async {
    if (readFailure != null) {
      throw readFailure!;
    }
    return values[key];
  }

  @override
  Future<void> write({required String key, required String value}) async {
    values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    values.remove(key);
  }
}

class _GateWriteSecureStorage extends _MemorySecureStorage {
  final Completer<void> writeStarted = Completer<void>();
  final Completer<void> releaseWrite = Completer<void>();
  bool blockNextWrite = false;

  @override
  Future<void> write({required String key, required String value}) async {
    if (blockNextWrite) {
      blockNextWrite = false;
      writeStarted.complete();
      await releaseWrite.future;
    }
    await super.write(key: key, value: value);
  }
}

AuthTokenResponse _session() => const AuthTokenResponse(
  accessToken: _accessToken,
  refreshToken: _refreshToken,
  userId: 42,
  role: 'DOCTOR',
  clinicContextId: null,
);

Map<String, dynamic> _successEnvelope() => {
  'success': true,
  'data': {
    'access_token': _accessToken,
    'refresh_token': _refreshToken,
    'user_id': 42,
    'role': 'DOCTOR',
    'clinic_context_id': null,
  },
};

void main() {
  group('GoogleLoginScreen', () {
    testWidgets('exchanges Google ID token and securely persists session', (
      tester,
    ) async {
      final storage = _MemorySecureStorage();
      var requests = 0;
      var sessionChangedCalls = 0;

      final client = MockClient((request) async {
        requests++;
        expect(request.method, 'POST');
        expect(
          request.url,
          Uri.parse('http://127.0.0.1:5000/api/v1/auth/google/mobile'),
        );
        expect(request.headers.containsKey('authorization'), isFalse);
        expect(jsonDecode(request.body), {'id_token': _idToken});
        return http.Response(jsonEncode(_successEnvelope()), 200);
      });
      addTearDown(client.close);

      final remote = AuthRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
      );
      final coordinator = AuthSessionCoordinator(
        sessionStore: AuthSessionStore(storage: storage),
        remoteDataSource: remote,
      );

      await tester.pumpWidget(
        MaterialApp(
          home: GoogleLoginScreen(
            acquireGoogleIdToken: () async => _idToken,
            authRemoteDataSource: remote,
            sessionCoordinator: coordinator,
            onSessionChanged: () => sessionChangedCalls++,
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const ValueKey('google-sign-in-button')));
      await tester.pumpAndSettle();

      expect(requests, 1);
      expect(sessionChangedCalls, 1);
      expect(
        find.byKey(const ValueKey('authenticated-session')),
        findsOneWidget,
      );
      expect(find.text('Role: DOCTOR'), findsOneWidget);
      expect(find.text('Signed in securely.'), findsOneWidget);

      final stored = await AuthSessionStore(storage: storage).read();
      expect(stored?.accessToken, _accessToken);
      expect(stored?.refreshToken, _refreshToken);
    });

    testWidgets(
      'invalidation during session persistence cannot reopen protected navigation',
      (tester) async {
        final storage = _GateWriteSecureStorage()..blockNextWrite = true;
        final guard = SessionNavigationGuard()..markSignedOut();
        addTearDown(guard.dispose);

        final client = MockClient((request) async {
          expect(request.url.path, '/api/v1/auth/google/mobile');
          return http.Response(jsonEncode(_successEnvelope()), 200);
        });
        addTearDown(client.close);

        final remote = AuthRemoteDataSource(
          apiClient: ApiClient(
            config: const BackendConfig(
              baseUrl: 'http://127.0.0.1:5000',
              allowInsecureHttp: true,
            ),
            client: client,
          ),
        );
        final coordinator = AuthSessionCoordinator(
          sessionStore: AuthSessionStore(storage: storage),
          remoteDataSource: remote,
        );

        await tester.pumpWidget(
          MaterialApp(
            home: GoogleLoginScreen(
              acquireGoogleIdToken: () async => _idToken,
              authRemoteDataSource: remote,
              sessionCoordinator: coordinator,
              sessionNavigationGuard: guard,
            ),
          ),
        );
        await tester.pumpAndSettle();
        await tester.tap(find.byKey(const ValueKey('google-sign-in-button')));
        await tester.pump();
        await storage.writeStarted.future;

        guard.invalidate();
        final invalidation = coordinator
            .invalidateAfterDefinitiveAuthenticationRejection();
        storage.releaseWrite.complete();
        await invalidation;
        await tester.pumpAndSettle();

        expect(guard.status, SessionNavigationStatus.invalidated);
        expect(await AuthSessionStore(storage: storage).read(), isNull);
        expect(
          find.byKey(const ValueKey('session-recovery-required')),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('authenticated-session')),
          findsNothing,
        );
        expect(
          find.byKey(const ValueKey('google-sign-in-button')),
          findsNothing,
        );
      },
    );

    testWidgets('cancellation makes no backend request and stores no session', (
      tester,
    ) async {
      final storage = _MemorySecureStorage();
      var requests = 0;

      final client = MockClient((_) async {
        requests++;
        return http.Response(jsonEncode(_successEnvelope()), 200);
      });
      addTearDown(client.close);

      final remote = AuthRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
      );
      final coordinator = AuthSessionCoordinator(
        sessionStore: AuthSessionStore(storage: storage),
        remoteDataSource: remote,
      );

      await tester.pumpWidget(
        MaterialApp(
          home: GoogleLoginScreen(
            acquireGoogleIdToken: () async => null,
            authRemoteDataSource: remote,
            sessionCoordinator: coordinator,
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const ValueKey('google-sign-in-button')));
      await tester.pumpAndSettle();

      expect(requests, 0);
      expect(await AuthSessionStore(storage: storage).read(), isNull);
      expect(
        find.byKey(const ValueKey('google-sign-in-button')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('auth-error-message')), findsNothing);
      expect(find.text('Sign-in cancelled.'), findsOneWidget);
    });

    testWidgets('backend rejection shows error without persisting session', (
      tester,
    ) async {
      final storage = _MemorySecureStorage();

      final client = MockClient((_) async {
        return http.Response(
          jsonEncode({'success': false, 'error': 'Rejected'}),
          401,
        );
      });
      addTearDown(client.close);

      final remote = AuthRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
      );
      final coordinator = AuthSessionCoordinator(
        sessionStore: AuthSessionStore(storage: storage),
        remoteDataSource: remote,
      );

      await tester.pumpWidget(
        MaterialApp(
          home: GoogleLoginScreen(
            acquireGoogleIdToken: () async => _idToken,
            authRemoteDataSource: remote,
            sessionCoordinator: coordinator,
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const ValueKey('google-sign-in-button')));
      await tester.pumpAndSettle();

      expect(find.byKey(const ValueKey('auth-error-message')), findsOneWidget);
      expect(find.text('You are signed in'), findsNothing);
      expect(await AuthSessionStore(storage: storage).read(), isNull);
    });

    testWidgets('restored session can sign out locally before remote logout', (
      tester,
    ) async {
      final storage = _MemorySecureStorage();
      final store = AuthSessionStore(storage: storage);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await store.save(_session());

      final pauseGate = Completer<void>();
      var sessionChangedCalls = 0;
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        expect(request.url.path, '/api/v1/auth/logout');
        expect(request.headers['authorization'], 'Bearer $_accessToken');
        expect(jsonDecode(request.body), {'refresh_token': _refreshToken});
        expect(
          storage.values.containsKey(AuthSessionStore.storageKey),
          isFalse,
          reason: 'Local credentials must be removed before network I/O.',
        );

        return http.Response(jsonEncode({'success': true}), 200);
      });
      addTearDown(client.close);

      final remote = AuthRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
      );
      final coordinator = AuthSessionCoordinator(
        sessionStore: store,
        remoteDataSource: remote,
      );

      await tester.pumpWidget(
        MaterialApp(
          home: GoogleLoginScreen(
            acquireGoogleIdToken: () async => null,
            authRemoteDataSource: remote,
            sessionCoordinator: coordinator,
            sessionNavigationGuard: guard,
            onSessionEnding: () async {
              await pauseGate.future;
            },
            onSessionChanged: () => sessionChangedCalls++,
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.byKey(const ValueKey('sign-out-button')), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('sign-out-button')));
      await tester.pump();
      await tester.idle();
      expect(guard.status, SessionNavigationStatus.invalidated);
      expect(guard.redirect('/patients/42'), '/login');
      expect(
        requests,
        0,
        reason: 'Remote logout must wait for replay quiescence.',
      );
      pauseGate.complete();
      await tester.pumpAndSettle();

      expect(requests, 1);
      expect(await store.read(), isNull);
      expect(
        sessionChangedCalls,
        0,
        reason: 'Logout must leave replay paused until the next successful sign-in.',
      );
      expect(
        find.byKey(const ValueKey('google-sign-in-button')),
        findsOneWidget,
      );
      expect(
        find.text('Signed out. The server confirmed logout.'),
        findsOneWidget,
      );
    });

    testWidgets(
      'secure-storage read failure requires recovery without deleting session',
      (tester) async {
        final storage = _MemorySecureStorage();
        final store = AuthSessionStore(storage: storage);
        await store.save(_session());

        final savedRecord = storage.values[AuthSessionStore.storageKey];
        storage.readFailure = StateError(
          'Injected secure-storage read failure.',
        );

        var requests = 0;
        final client = MockClient((_) async {
          requests++;
          return http.Response(jsonEncode({'success': true}), 200);
        });
        addTearDown(client.close);

        final remote = AuthRemoteDataSource(
          apiClient: ApiClient(
            config: const BackendConfig(
              baseUrl: 'http://127.0.0.1:5000',
              allowInsecureHttp: true,
            ),
            client: client,
          ),
        );
        final coordinator = AuthSessionCoordinator(
          sessionStore: store,
          remoteDataSource: remote,
        );

        await tester.pumpWidget(
          MaterialApp(
            home: GoogleLoginScreen(
              acquireGoogleIdToken: () async => _idToken,
              authRemoteDataSource: remote,
              sessionCoordinator: coordinator,
            ),
          ),
        );
        await tester.pumpAndSettle();

        expect(
          find.byKey(const ValueKey('session-recovery-required')),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('auth-error-message')),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('google-sign-in-button')),
          findsNothing,
        );
        expect(
          find.byKey(const ValueKey('clear-local-session-button')),
          findsOneWidget,
        );
        expect(requests, 0);
        expect(storage.values[AuthSessionStore.storageKey], savedRecord);
      },
    );
    testWidgets('pending refresh requires local recovery before sign-in', (
      tester,
    ) async {
      final storage = _MemorySecureStorage();
      final store = AuthSessionStore(storage: storage);
      await store.save(_session(), refreshPending: true);

      var requests = 0;
      final client = MockClient((_) async {
        requests++;
        return http.Response(jsonEncode({'success': true}), 200);
      });
      addTearDown(client.close);

      final remote = AuthRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
      );
      final coordinator = AuthSessionCoordinator(
        sessionStore: store,
        remoteDataSource: remote,
      );

      await tester.pumpWidget(
        MaterialApp(
          home: GoogleLoginScreen(
            acquireGoogleIdToken: () async => _idToken,
            authRemoteDataSource: remote,
            sessionCoordinator: coordinator,
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('session-recovery-required')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('google-sign-in-button')), findsNothing);

      await tester.tap(
        find.byKey(const ValueKey('clear-local-session-button')),
      );
      await tester.pumpAndSettle();

      expect(requests, 0);
      expect(await store.read(), isNull);
      expect(
        find.byKey(const ValueKey('google-sign-in-button')),
        findsOneWidget,
      );
    });

    testWidgets(
      'invalidation hides a restored session until secure local state is cleared',
      (tester) async {
        final storage = _MemorySecureStorage();
        final store = AuthSessionStore(storage: storage);
        await store.save(_session());
        final guard = SessionNavigationGuard();
        addTearDown(guard.dispose);

        final client = MockClient((_) async {
          fail('Restoration and invalidation must not make a network request.');
        });
        addTearDown(client.close);

        final remote = AuthRemoteDataSource(
          apiClient: ApiClient(
            config: const BackendConfig(
              baseUrl: 'http://127.0.0.1:5000',
              allowInsecureHttp: true,
            ),
            client: client,
          ),
        );
        final coordinator = AuthSessionCoordinator(
          sessionStore: store,
          remoteDataSource: remote,
        );

        await tester.pumpWidget(
          MaterialApp(
            home: GoogleLoginScreen(
              acquireGoogleIdToken: () async => _idToken,
              authRemoteDataSource: remote,
              sessionCoordinator: coordinator,
              sessionNavigationGuard: guard,
            ),
          ),
        );
        await tester.pumpAndSettle();

        expect(guard.status, SessionNavigationStatus.sessionAvailable);
        expect(
          find.byKey(const ValueKey('authenticated-session')),
          findsOneWidget,
        );

        guard.invalidate();
        await tester.pumpAndSettle();

        expect(
          find.byKey(const ValueKey('session-recovery-required')),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('authenticated-session')),
          findsNothing,
        );
        expect(
          find.byKey(const ValueKey('clear-local-session-button')),
          findsOneWidget,
        );

        await store.clear();
        guard.markSignedOut();
        await tester.pumpAndSettle();

        expect(await store.read(), isNull);
        expect(guard.status, SessionNavigationStatus.signedOut);
        expect(
          find.byKey(const ValueKey('google-sign-in-button')),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('authenticated-session')),
          findsNothing,
        );
      },
    );
  });
}
