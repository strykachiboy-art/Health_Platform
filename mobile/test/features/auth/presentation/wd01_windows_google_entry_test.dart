import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_store.dart';
import 'package:clinic_system_pro/features/auth/data/google_desktop_identity.dart';
import 'package:clinic_system_pro/features/auth/presentation/google_login_screen.dart';

class _MemorySecrets implements SecureKeyValueStorage {
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

const _desktopIdentity = DesktopGoogleIdentity(
  idToken: 'fake-google-id-token',
  nonce: 'test-only-one-time-nonce',
);

Map<String, dynamic> _backendTokens() => {
  'success': true,
  'data': {
    'access_token':
        'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiJ9.wd01_desktop_google_access',
    'refresh_token':
        'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiJ9.wd01_desktop_google_refresh',
    'user_id': 42,
    'role': 'PATIENT',
    'clinic_context_id': null,
  },
};

AuthRemoteDataSource _remote(http.Client client) => AuthRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
);

GoogleLoginScreen _screen({
  required AuthRemoteDataSource remote,
  required _MemorySecrets storage,
  required Future<DesktopGoogleIdentity?> Function() desktopLogin,
}) => GoogleLoginScreen(
  enableNativeGoogleSignIn: false,
  enableDesktopGoogleSignIn: true,
  acquireGoogleIdToken: () async => throw StateError('Mobile provider called'),
  acquireDesktopGoogleIdToken: desktopLogin,
  authRemoteDataSource: remote,
  sessionCoordinator: AuthSessionCoordinator(
    sessionStore: AuthSessionStore(storage: storage),
    remoteDataSource: remote,
  ),
);

void main() {
  group('WD01 Windows Google desktop OAuth handoff', () {
    testWidgets(
      'Google button and password form coexist without eager network requests',
      (tester) async {
        final storage = _MemorySecrets();
        final client = MockClient(
          (_) async => throw StateError('No request expected'),
        );
        addTearDown(client.close);
        await tester.pumpWidget(
          MaterialApp(
            home: _screen(
              remote: _remote(client),
              storage: storage,
              desktopLogin: () async => null,
            ),
          ),
        );
        await tester.pumpAndSettle();
        expect(
          find.byKey(const ValueKey('google-sign-in-button')),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('auth-r01-login-email')),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('auth-r01-login-password')),
          findsOneWidget,
        );
        expect(storage.values, isEmpty);
      },
    );

    testWidgets(
      'desktop token and nonce go to Flask; only Flask JWT is persisted',
      (tester) async {
        final storage = _MemorySecrets();
        var requests = 0;
        final client = MockClient((request) async {
          requests++;
          expect(request.method, 'POST');
          expect(request.url.path, '/api/v1/auth/google/desktop');
          expect(request.headers.containsKey('authorization'), false);
          expect(jsonDecode(request.body), {
            'id_token': _desktopIdentity.idToken,
            'nonce': _desktopIdentity.nonce,
          });
          return http.Response(jsonEncode(_backendTokens()), 200);
        });
        addTearDown(client.close);
        final remote = _remote(client);
        await tester.pumpWidget(
          MaterialApp(
            home: _screen(
              remote: remote,
              storage: storage,
              desktopLogin: () async => _desktopIdentity,
            ),
          ),
        );
        await tester.pumpAndSettle();
        await tester.tap(find.byKey(const ValueKey('google-sign-in-button')));
        await tester.pumpAndSettle();
        expect(requests, 1);
        final tokens = await AuthSessionStore(storage: storage).read();
        expect(tokens?.role, 'PATIENT');
        expect(tokens?.accessToken, _backendTokens()['data']['access_token']);
        expect(
          storage.values.values.single,
          isNot(contains('fake-google-id-token')),
        );
        expect(
          storage.values.values.single,
          isNot(contains('test-only-one-time-nonce')),
        );
        expect(
          find.byKey(const ValueKey('authenticated-session')),
          findsOneWidget,
        );
      },
    );

    testWidgets('desktop code and PKCE go to Flask only; CSP JWT persists', (
      tester,
    ) async {
      final storage = _MemorySecrets();
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/auth/google/desktop/code');
        expect(jsonDecode(request.body), {
          'code': 'one-time-code',
          'code_verifier': 'VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV',
          'redirect_uri': 'http://127.0.0.1:23456',
          'nonce': 'NNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNN',
        });
        expect(request.body, isNot(contains('client_secret')));
        return http.Response(jsonEncode(_backendTokens()), 200);
      });
      addTearDown(client.close);
      final remote = _remote(client);
      await tester.pumpWidget(
        MaterialApp(
          home: _screen(
            remote: remote,
            storage: storage,
            desktopLogin: () async => const DesktopGoogleIdentity(
              authorizationCode: 'one-time-code',
              codeVerifier: 'VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV',
              redirectUri: 'http://127.0.0.1:23456',
              nonce: 'NNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNN',
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('google-sign-in-button')));
      await tester.pumpAndSettle();
      expect(requests, 1);
      final stored = await AuthSessionStore(storage: storage).read();
      expect(stored?.role, 'PATIENT');
      expect(storage.values.values.single, isNot(contains('one-time-code')));
      expect(
        find.byKey(const ValueKey('authenticated-session')),
        findsOneWidget,
      );
    });

    testWidgets('Google cancellation never calls backend or saves a session', (
      tester,
    ) async {
      final storage = _MemorySecrets();
      final client = MockClient(
        (_) async => throw StateError('Backend request forbidden'),
      );
      addTearDown(client.close);
      await tester.pumpWidget(
        MaterialApp(
          home: _screen(
            remote: _remote(client),
            storage: storage,
            desktopLogin: () async => null,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('google-sign-in-button')));
      await tester.pumpAndSettle();
      expect(storage.values, isEmpty);
      expect(find.byKey(const ValueKey('authenticated-session')), findsNothing);
      expect(find.text('Sign-in cancelled.'), findsOneWidget);
    });

    testWidgets('Flask rejection cannot open authenticated desktop session', (
      tester,
    ) async {
      final storage = _MemorySecrets();
      final client = MockClient(
        (request) async => http.Response(
          jsonEncode({'success': false, 'error': 'Invalid Google ID token'}),
          422,
        ),
      );
      addTearDown(client.close);
      await tester.pumpWidget(
        MaterialApp(
          home: _screen(
            remote: _remote(client),
            storage: storage,
            desktopLogin: () async => _desktopIdentity,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('google-sign-in-button')));
      await tester.pumpAndSettle();
      expect(storage.values, isEmpty);
      expect(find.byKey(const ValueKey('authenticated-session')), findsNothing);
      expect(find.byKey(const ValueKey('auth-error-message')), findsOneWidget);
    });
  });
}
