import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_mfa_challenge.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

Map<String, dynamic> _pending() => {
  'success': true,
  'data': {
    'mfa_required': true,
    'challenge_token':
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    'challenge_expires_in': 300,
    'method': 'authenticator_or_recovery_code',
  },
};

Map<String, dynamic> _tokens() => {
  'success': true,
  'data': {
    'access_token': 'signed-token',
    'refresh_token': 'refresh-token',
    'user_id': 2,
    'role': 'super_admin',
    'clinic_context_id': null,
  },
};

AuthRemoteDataSource _source(MockClient mock) => AuthRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: mock,
  ),
);

void main() {
  test('pending challenge cannot become a token-bearing session', () {
    expect(
      () => AuthTokenResponse.fromEnvelope(_pending()),
      throwsA(isA<AuthMfaChallenge>()),
    );
    final leaked = _pending();
    (leaked['data'] as Map<String, dynamic>)['access_token'] = 'forged';
    expect(
      () => AuthTokenResponse.fromEnvelope(leaked),
      throwsA(isA<FormatException>()),
    );
  });

  test('MFA challenge exception never prints its secret', () {
    final exception = AuthMfaChallenge(
      challengeToken:
          'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      expiresInSeconds: 300,
    );
    expect(
      exception.toString(),
      isNot(
        contains(
          'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        ),
      ),
    );
  });

  test('password login receives in-memory MFA challenge, no tokens', () async {
    final client = MockClient(
      (request) async => http.Response(jsonEncode(_pending()), 200),
    );
    addTearDown(client.close);
    await expectLater(
      _source(client).login(email: 'sa@example.test', password: 'password'),
      throwsA(isA<AuthMfaChallenge>()),
    );
  });

  test('challenge verification posts one factor and parses JWT only afterward', () async {
    final client = MockClient((request) async {
      expect(request.url.path, '/api/v1/auth/mfa/complete');
      expect(jsonDecode(request.body), {
        'challenge_token':
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        'factor': '123456',
      });
      expect(request.headers.containsKey('authorization'), isFalse);
      return http.Response(jsonEncode(_tokens()), 200);
    });
    addTearDown(client.close);
    final result = await _source(client).completeMfa(
      challengeToken:
          'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      factor: '123456',
    );
    expect(result.role, 'super_admin');
    expect(result.accessToken, 'signed-token');
  });

  test(
    'enrollment GET and begin use the existing bearer, no offline queue',
    () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(request.headers['authorization'], 'Bearer signed-token');
        if (calls == 1) {
          expect(request.method, 'GET');
          expect(request.url.path, '/api/v1/auth/mfa/enrollment/status');
          return http.Response(
            jsonEncode({
              'success': true,
              'data': {'enrollment_enabled': true, 'enrolled': false},
            }),
            200,
          );
        }
        expect(request.url.path, '/api/v1/auth/mfa/enrollment/begin');
        expect(jsonDecode(request.body), {'current_password': 'old-password'});
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'setup_secret':
                  'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
              'expires_in': 600,
            },
          }),
          200,
        );
      });
      addTearDown(client.close);
      final source = _source(client);
      final status = await source.getMfaEnrollmentStatus(
        accessToken: 'signed-token',
      );
      expect(status.enrollmentEnabled, isTrue);
      expect(status.enrolled, isFalse);
      final secret = await source.beginMfaEnrollment(
        accessToken: 'signed-token',
        currentPassword: 'old-password',
      );
      expect(secret, 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA');
      expect(calls, 2);
    },
  );

  test(
    'single-use recovery codes are returned only after confirmed enrollment',
    () async {
      final codes = List<String>.generate(
        10,
        (i) => "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA${i.toString().padLeft(2, '0')}",
      );
      // Recovery codes are base32 only; use valid distinct suffixes.
      final valid = List<String>.generate(
        10,
        (i) => "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA${'BCDEFGHIJK'[i]}",
      );
      expect(codes.length, 10);
      final client = MockClient((request) async {
        expect(request.url.path, '/api/v1/auth/mfa/enrollment/confirm');
        expect(request.headers['authorization'], 'Bearer signed-token');
        expect(jsonDecode(request.body), {'code': '123456'});
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'recovery_codes': valid,
              'reauthentication_required': true,
            },
          }),
          200,
        );
      });
      addTearDown(client.close);
      final got = await _source(client)
          .confirmMfaEnrollment(accessToken: 'signed-token', code: '123456');
      expect(got, valid);
      expect(() => got.add('INVALID'), throwsUnsupportedError);
    },
  );
}
