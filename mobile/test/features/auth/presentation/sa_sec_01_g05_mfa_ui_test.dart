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

class _MemoryStore implements SecureKeyValueStorage {
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

Map<String, dynamic> _pending() => {
  'success': true,
  'data': {
    'mfa_required': true,
    'challenge_token':
        'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    'challenge_expires_in': 300,
    'method': 'authenticator_or_recovery_code',
  },
};

Map<String, dynamic> _tokens() => {
  'success': true,
  'data': {
    'access_token': 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiJ9.signature',
    'refresh_token': 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiIsInR5cGUiOiJyZWZyZXNoIn0.signature',
    'user_id': 42,
    'role': 'super_admin',
    'clinic_context_id': null,
  },
};

void main() {
  testWidgets(
    'password MFA pending never persists session; verified factor does',
    (tester) async {
      var completions = 0;
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/auth/login')) {
          return http.Response(jsonEncode(_pending()), 200);
        }
        if (request.url.path.endsWith('/auth/mfa/complete')) {
          completions++;
          expect(jsonDecode(request.body), {
            'challenge_token': 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
            'factor': '123456',
          });
          return http.Response(jsonEncode(_tokens()), 200);
        }
        return http.Response('{}', 404);
      });
      addTearDown(client.close);
      final storage = _MemoryStore();
      final remote = AuthRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
      );
      await tester.pumpWidget(
        MaterialApp(
          home: GoogleLoginScreen(
            acquireGoogleIdToken: () async => null,
            enableNativeGoogleSignIn: false,
            authRemoteDataSource: remote,
            sessionCoordinator: AuthSessionCoordinator(
              sessionStore: AuthSessionStore(storage: storage),
              remoteDataSource: remote,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const ValueKey('auth-r01-login-email')),
        'sa@example.test',
      );
      await tester.enterText(
        find.byKey(const ValueKey('auth-r01-login-password')),
        'correct-password',
      );
      await tester.tap(find.byKey(const ValueKey('auth-r01-email-sign-in')));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('g05-mfa-challenge-title')),
        findsOneWidget,
      );
      expect(storage.values, isEmpty);
      await tester.enterText(
        find.byKey(const ValueKey('g05-mfa-factor')),
        '123456',
      );
      await tester.tap(find.byKey(const ValueKey('g05-mfa-submit')));
      await tester.pumpAndSettle();
      expect(completions, 1);
      expect(
        find.byKey(const ValueKey('authenticated-session')),
        findsOneWidget,
      );
      expect(storage.values, isNotEmpty);
    },
  );

  testWidgets('failed MFA completion cannot create or persist a session', (
    tester,
  ) async {
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/auth/login')) {
        return http.Response(jsonEncode(_pending()), 200);
      }
      return http.Response(
        jsonEncode({'success': false, 'error': 'Invalid MFA challenge'}),
        422,
      );
    });
    addTearDown(client.close);
    final storage = _MemoryStore();
    final remote = AuthRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: GoogleLoginScreen(
          acquireGoogleIdToken: () async => null,
          enableNativeGoogleSignIn: false,
          authRemoteDataSource: remote,
          sessionCoordinator: AuthSessionCoordinator(
            sessionStore: AuthSessionStore(storage: storage),
            remoteDataSource: remote,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-login-email')),
      'sa@example.test',
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-login-password')),
      'correct-password',
    );
    await tester.tap(find.byKey(const ValueKey('auth-r01-email-sign-in')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('g05-mfa-factor')),
      '123456',
    );
    await tester.tap(find.byKey(const ValueKey('g05-mfa-submit')));
    await tester.pumpAndSettle();
    expect(storage.values, isEmpty);
    expect(find.byKey(const ValueKey('authenticated-session')), findsNothing);
    expect(find.byKey(const ValueKey('g05-mfa-challenge-title')), findsNothing);
  });
}
