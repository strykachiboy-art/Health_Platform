import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/presentation/password_recovery_screens.dart';

const _token = 'CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC';

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
  testWidgets('forgot password is neutral and sends exactly one request', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.url.path, '/api/v1/auth/password-reset/request');
      return http.Response(jsonEncode({'success': true}), 202);
    });
    addTearDown(client.close);
    await tester.pumpWidget(
      MaterialApp(
        home: ForgotPasswordScreen(authRemoteDataSource: _source(client)),
      ),
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-forgot-email')),
      'unknown@example.test',
    );
    await tester.tap(find.byKey(const ValueKey('auth-r01-forgot-submit')));
    await tester.pumpAndSettle();
    expect(calls, 1);
    expect(
      find.textContaining('If an eligible account exists'),
      findsOneWidget,
    );
    final button = tester.widget<FilledButton>(
      find.byKey(const ValueKey('auth-r01-forgot-submit')),
    );
    expect(button.onPressed, isNull);
  });

  testWidgets('forgot password local invalid email never makes a request', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    await tester.pumpWidget(
      MaterialApp(
        home: ForgotPasswordScreen(authRemoteDataSource: _source(client)),
      ),
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-forgot-email')),
      'invalid-email',
    );
    await tester.tap(find.byKey(const ValueKey('auth-r01-forgot-submit')));
    await tester.pumpAndSettle();
    expect(calls, 0);
  });

  testWidgets('reset confirmation does not create an authenticated session', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.url.path, '/api/v1/auth/password-reset/confirm');
      final sent = jsonDecode(request.body) as Map<String, dynamic>;
      expect(sent['token'], _token);
      expect(sent['new_password'], 'NewPassword!123');
      expect(sent['confirm_password'], 'NewPassword!123');
      expect(sent.containsKey('recovery_factor'), false);
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {'reauthentication_required': true},
        }),
        200,
      );
    });
    addTearDown(client.close);
    await tester.pumpWidget(
      MaterialApp(
        home: ResetPasswordScreen(authRemoteDataSource: _source(client)),
      ),
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-link')),
      'https://account.example.test/reset-password#token=$_token',
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-password')),
      'NewPassword!123',
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-confirm')),
      'NewPassword!123',
    );
    await tester.ensureVisible(
      find.byKey(const ValueKey('auth-r01-reset-submit')),
    );
    await tester.tap(find.byKey(const ValueKey('auth-r01-reset-submit')));
    await tester.pumpAndSettle();
    expect(calls, 1);
    expect(find.textContaining('Password updated.'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(
            find.byKey(const ValueKey('auth-r01-reset-submit')),
          )
          .onPressed,
      isNull,
    );
  });

  testWidgets('additional recovery factor goes only to backend', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      final data = jsonDecode(request.body) as Map<String, dynamic>;
      expect(data['recovery_factor'], 'test-private-factor');
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {'reauthentication_required': true},
        }),
        200,
      );
    });
    addTearDown(client.close);
    await tester.pumpWidget(
      MaterialApp(
        home: ResetPasswordScreen(authRemoteDataSource: _source(client)),
      ),
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-link')),
      'https://account.example.test/reset-password#token=$_token&verification=additional',
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-password')),
      'NewPassword!123',
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-confirm')),
      'NewPassword!123',
    );
    await tester.ensureVisible(
      find.byKey(const ValueKey('auth-r01-reset-factor')),
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-factor')),
      'test-private-factor',
    );
    await tester.ensureVisible(
      find.byKey(const ValueKey('auth-r01-reset-submit')),
    );
    await tester.tap(find.byKey(const ValueKey('auth-r01-reset-submit')));
    await tester.pumpAndSettle();
    expect(calls, 1);
  });

  testWidgets('timeout/ambiguous reset is not silently retried', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode({'success': false}), 503);
    });
    addTearDown(client.close);
    await tester.pumpWidget(
      MaterialApp(
        home: ResetPasswordScreen(authRemoteDataSource: _source(client)),
      ),
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-link')),
      _token,
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-password')),
      'NewPassword!123',
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-confirm')),
      'NewPassword!123',
    );
    await tester.ensureVisible(
      find.byKey(const ValueKey('auth-r01-reset-submit')),
    );
    await tester.tap(find.byKey(const ValueKey('auth-r01-reset-submit')));
    await tester.pumpAndSettle();
    expect(calls, 1);
    expect(find.textContaining('Reset could not be confirmed'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(
            find.byKey(const ValueKey('auth-r01-reset-submit')),
          )
          .onPressed,
      isNull,
    );
  });

  testWidgets('mismatched passwords fail before server request', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    await tester.pumpWidget(
      MaterialApp(
        home: ResetPasswordScreen(authRemoteDataSource: _source(client)),
      ),
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-link')),
      _token,
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-password')),
      'NewPassword!123',
    );
    await tester.enterText(
      find.byKey(const ValueKey('auth-r01-reset-confirm')),
      'mismatch-password',
    );
    await tester.ensureVisible(
      find.byKey(const ValueKey('auth-r01-reset-submit')),
    );
    await tester.tap(find.byKey(const ValueKey('auth-r01-reset-submit')));
    await tester.pumpAndSettle();
    expect(calls, 0);
  });
}
