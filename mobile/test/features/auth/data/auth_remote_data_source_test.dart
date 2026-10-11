import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';

const testEmail = 'clinician@example.test';
const testPassword = 'example-password';

BackendConfig testConfig() {
  return const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  );
}

Map<String, dynamic> successfulLoginEnvelope() => {
  'success': true,
  'data': {
    'access_token': 'access-example',
    'refresh_token': 'refresh-example',
    'user_id': 42,
    'role': 'DOCTOR',
    'clinic_context_id': null,
  },
};

void main() {
  group('AuthRemoteDataSource', () {
    test('sends credentials to login and parses the token response', () async {
      final mock = MockClient((request) async {
        expect(request.method, 'POST');
        expect(
          request.url,
          Uri.parse('http://127.0.0.1:5000/api/v1/auth/login'),
        );
        expect(request.headers['accept'], 'application/json');
        expect(request.headers['content-type'], contains('application/json'));
        expect(request.headers.containsKey('authorization'), isFalse);
        expect(jsonDecode(request.body), {
          'email': testEmail,
          'password': testPassword,
        });

        return http.Response(
          jsonEncode(successfulLoginEnvelope()),
          200,
          headers: {'content-type': 'application/json'},
        );
      });
      addTearDown(mock.close);

      final source = AuthRemoteDataSource(
        apiClient: ApiClient(config: testConfig(), client: mock),
      );

      final result = await source.login(
        email: testEmail,
        password: testPassword,
      );

      expect(result.accessToken, 'access-example');
      expect(result.refreshToken, 'refresh-example');
      expect(result.userId, 42);
      expect(result.role, 'DOCTOR');
      expect(result.clinicContextId, isNull);
    });

    test(
      'sends a Google ID token to mobile auth and parses the response',
      () async {
        const idToken = 'google-id-token';
        final mock = MockClient((request) async {
          expect(request.method, 'POST');
          expect(
            request.url,
            Uri.parse('http://127.0.0.1:5000/api/v1/auth/google/mobile'),
          );
          expect(request.headers['accept'], 'application/json');
          expect(request.headers['content-type'], contains('application/json'));
          expect(request.headers.containsKey('authorization'), isFalse);
          expect(jsonDecode(request.body), {'id_token': idToken});

          return http.Response(
            jsonEncode(successfulLoginEnvelope()),
            200,
            headers: {'content-type': 'application/json'},
          );
        });
        addTearDown(mock.close);

        final source = AuthRemoteDataSource(
          apiClient: ApiClient(config: testConfig(), client: mock),
        );

        final result = await source.loginWithGoogleIdToken(idToken: idToken);

        expect(result.accessToken, 'access-example');
        expect(result.refreshToken, 'refresh-example');
        expect(result.userId, 42);
        expect(result.role, 'DOCTOR');
      },
    );

    test('rejects malformed Google ID tokens before network I/O', () async {
      var requests = 0;
      final mock = MockClient((request) async {
        requests++;
        return http.Response(jsonEncode(successfulLoginEnvelope()), 200);
      });
      addTearDown(mock.close);

      final source = AuthRemoteDataSource(
        apiClient: ApiClient(config: testConfig(), client: mock),
      );
      final invalidTokens = <String>[
        '',
        ' leading-space',
        'trailing-space ',
        List<String>.filled(8193, 'x').join(),
      ];

      for (final token in invalidTokens) {
        await expectLater(
          source.loginWithGoogleIdToken(idToken: token),
          throwsArgumentError,
        );
      }

      expect(requests, 0);
    });

    test('propagates typed HTTP failures', () async {
      final mock = MockClient((request) async {
        return http.Response(
          '{"success":false,"error":"Invalid credentials"}',
          401,
        );
      });
      addTearDown(mock.close);

      final source = AuthRemoteDataSource(
        apiClient: ApiClient(config: testConfig(), client: mock),
      );

      await expectLater(
        source.login(email: testEmail, password: 'wrong-password'),
        throwsA(
          isA<ApiException>()
              .having((error) => error.kind, 'kind', ApiFailureKind.httpStatus)
              .having((error) => error.statusCode, 'statusCode', 401),
        ),
      );
    });

    test(
      'rejects a successful HTTP response with invalid token data',
      () async {
        final mock = MockClient((request) async {
          return http.Response(
            jsonEncode({
              'success': true,
              'data': {
                'access_token': 'access-example',
                'user_id': 42,
                'role': 'DOCTOR',
              },
            }),
            200,
          );
        });
        addTearDown(mock.close);

        final source = AuthRemoteDataSource(
          apiClient: ApiClient(config: testConfig(), client: mock),
        );

        await expectLater(
          source.login(email: testEmail, password: testPassword),
          throwsA(isA<FormatException>()),
        );
      },
    );

    test('rejects a malformed JSON response', () async {
      final mock = MockClient((request) async {
        return http.Response('not-json', 200);
      });
      addTearDown(mock.close);

      final source = AuthRemoteDataSource(
        apiClient: ApiClient(config: testConfig(), client: mock),
      );

      await expectLater(
        source.login(email: testEmail, password: testPassword),
        throwsA(
          isA<ApiException>().having(
            (error) => error.kind,
            'kind',
            ApiFailureKind.invalidResponse,
          ),
        ),
      );
    });
  });
}
