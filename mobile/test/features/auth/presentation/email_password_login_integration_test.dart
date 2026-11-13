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
import 'package:clinic_system_pro/features/auth/presentation/google_login_screen.dart';

class _Storage implements SecureKeyValueStorage {
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

AuthRemoteDataSource _source(MockClient client) => AuthRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
);

void main() {
  testWidgets('password sign-in shares existing secure session coordinator', (
    tester,
  ) async {
    var calls = 0;
    var changed = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.url.path, '/api/v1/auth/login');
      expect(request.method, 'POST');
      expect(jsonDecode(request.body), {
        'email': 'doctor@example.test',
        'password': 'example-secret',
      });
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'access_token': 'test.access.signature',
            'refresh_token': 'test.refresh.signature',
            'user_id': 42,
            'role': 'DOCTOR',
            'clinic_context_id': null,
          },
        }),
        200,
      );
    });
    addTearDown(client.close);
    final store = AuthSessionStore(storage: _Storage());
    final remote = _source(client);
    await tester.pumpWidget(
      MaterialApp(
        home: GoogleLoginScreen(
          acquireGoogleIdToken: () async => null,
          authRemoteDataSource: remote,
          sessionCoordinator: AuthSessionCoordinator(
            sessionStore: store,
            remoteDataSource: remote,
          ),
          onSessionChanged: () => changed++,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('google-sign-in-button')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('auth-r01-email-toggle')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(
      find.byKey(const ValueKey('auth-r01-login-email')),
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-login-email')),
      'doctor@example.test',
    );
    await tester.ensureVisible(
      find.byKey(const ValueKey('auth-r01-login-password')),
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-login-password')),
      'example-secret',
    );
    await tester.ensureVisible(
      find.byKey(const ValueKey('auth-r01-email-sign-in')),
    );
    await tester.tap(find.byKey(const ValueKey('auth-r01-email-sign-in')));
    await tester.pumpAndSettle();
    expect(calls, 1);
    expect(changed, 1);
    expect(find.byKey(const ValueKey('authenticated-session')), findsOneWidget);
    expect((await store.read())?.accessToken, 'test.access.signature');
  });

  testWidgets('backend password rejection never persists session', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode({'success': false}), 401);
    });
    addTearDown(client.close);
    final store = AuthSessionStore(storage: _Storage());
    final remote = _source(client);
    await tester.pumpWidget(
      MaterialApp(
        home: GoogleLoginScreen(
          acquireGoogleIdToken: () async => null,
          authRemoteDataSource: remote,
          sessionCoordinator: AuthSessionCoordinator(
            sessionStore: store,
            remoteDataSource: remote,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('auth-r01-email-toggle')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(
      find.byKey(const ValueKey('auth-r01-login-email')),
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-login-email')),
      'doctor@example.test',
    );
    await tester.ensureVisible(
      find.byKey(const ValueKey('auth-r01-login-password')),
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-login-password')),
      'invalid-secret',
    );
    await tester.ensureVisible(
      find.byKey(const ValueKey('auth-r01-email-sign-in')),
    );
    await tester.tap(find.byKey(const ValueKey('auth-r01-email-sign-in')));
    await tester.pumpAndSettle();
    expect(calls, 1);
    expect(await store.read(), isNull);
    expect(find.byKey(const ValueKey('authenticated-session')), findsNothing);
    expect(find.byKey(const ValueKey('auth-error-message')), findsOneWidget);
  });
}
