import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';

const _token = 'BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB';

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
  test(
    'forgot password sends only email, without auth or idempotency key',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/auth/password-reset/request');
        expect(request.headers.containsKey('authorization'), false);
        expect(request.headers.containsKey('idempotency-key'), false);
        expect(jsonDecode(request.body), {'email': 'someone@example.test'});
        return http.Response(
          jsonEncode({
            'success': true,
            'message': 'If an eligible account exists, email will be sent.',
          }),
          202,
        );
      });
      addTearDown(client.close);
      await _source(client).requestPasswordReset(email: 'someone@example.test');
      expect(calls, 1);
    },
  );

  test(
    'reset confirmation uses backend contract and never authenticates',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/auth/password-reset/confirm');
        expect(request.headers.containsKey('authorization'), false);
        expect(request.headers.containsKey('idempotency-key'), false);
        expect(jsonDecode(request.body), {
          'token': _token,
          'new_password': 'secure-new-password-123',
          'confirm_password': 'secure-new-password-123',
          'recovery_factor': 'test-recovery-factor',
        });
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {'reauthentication_required': true},
          }),
          200,
        );
      });
      addTearDown(client.close);
      await _source(client).confirmPasswordReset(
        token: _token,
        newPassword: 'secure-new-password-123',
        confirmPassword: 'secure-new-password-123',
        recoveryFactor: 'test-recovery-factor',
      );
      expect(calls, 1);
    },
  );

  test('reset without additional factor omits optional field', () async {
    final client = MockClient((request) async {
      final data = jsonDecode(request.body) as Map<String, dynamic>;
      expect(data.containsKey('recovery_factor'), false);
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {'reauthentication_required': true},
        }),
        200,
      );
    });
    addTearDown(client.close);
    await _source(client).confirmPasswordReset(
      token: _token,
      newPassword: 'secure-new-password-123',
      confirmPassword: 'secure-new-password-123',
    );
  });

  test('malformed credentials never reach the network', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final source = _source(client);
    await expectLater(
      source.requestPasswordReset(email: 'not-an-email'),
      throwsArgumentError,
    );
    await expectLater(
      source.confirmPasswordReset(
        token: 'not-valid',
        newPassword: 'new-password-123',
        confirmPassword: 'new-password-123',
      ),
      throwsArgumentError,
    );
    expect(calls, 0);
  });

  test('backend HTTP 400 is propagated without local retry', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(
        jsonEncode({'success': false, 'error': 'Invalid or expired token'}),
        400,
      );
    });
    addTearDown(client.close);
    await expectLater(
      _source(client).confirmPasswordReset(
        token: _token,
        newPassword: 'new-password-123',
        confirmPassword: 'new-password-123',
      ),
      throwsA(
        isA<ApiException>().having((e) => e.statusCode, 'statusCode', 400),
      ),
    );
    expect(calls, 1);
  });

  test('HTTP 200 missing explicit reauthentication is rejected', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode({'success': true, 'data': <String, dynamic>{}}),
        200,
      ),
    );
    addTearDown(client.close);
    await expectLater(
      _source(client).confirmPasswordReset(
        token: _token,
        newPassword: 'new-password-123',
        confirmPassword: 'new-password-123',
      ),
      throwsFormatException,
    );
  });
}
