import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';

BackendConfig testConfig() {
  return const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  );
}

void main() {
  group('ApiClient POST JSON', () {
    test('sends the login request contract and decodes the response', () async {
      final mock = MockClient((request) async {
        expect(request.method, 'POST');
        expect(
          request.url,
          Uri.parse('http://127.0.0.1:5000/api/v1/auth/login'),
        );
        expect(request.headers['accept'], 'application/json');
        expect(request.headers['content-type'], contains('application/json'));
        expect(request.headers.containsKey('authorization'), isFalse);
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        expect(jsonDecode(request.body), {
          'email': 'clinician@example.test',
          'password': 'example-password',
        });

        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'access_token': 'access-example',
              'refresh_token': 'refresh-example',
              'user_id': 42,
              'role': 'DOCTOR',
              'clinic_context_id': null,
            },
          }),
          200,
          headers: {'content-type': 'application/json'},
        );
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);

      final result = await api.postJson(
        'api/v1/auth/login',
        body: {
          'email': 'clinician@example.test',
          'password': 'example-password',
        },
      );

      expect(result['success'], isTrue);
      expect(result['data'], isA<Map<String, dynamic>>());
      expect(result['data']['user_id'], 42);
      expect(result['data']['role'], 'DOCTOR');
    });

    test('forwards a caller-supplied POST idempotency key after trimming', () async {
      final mock = MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.headers['idempotency-key'], 'mobile-operation-001');
        expect(jsonDecode(request.body), {'sample_value': 'created'});
        return http.Response('{"success":true,"data":{}}', 200);
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);
      addTearDown(api.close);

      await api.postJson(
        'test/idempotency-transport',
        body: const <String, dynamic>{'sample_value': 'created'},
        idempotencyKey: '  mobile-operation-001  ',
      );
    });

    test('rejects blank and oversized POST idempotency keys before sending', () async {
      final mock = MockClient((request) async {
        fail('Invalid idempotency keys must not reach the HTTP client.');
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);
      addTearDown(api.close);

      await expectLater(
        api.postJson(
          'test/idempotency-transport',
          body: const <String, dynamic>{},
          idempotencyKey: '   ',
        ),
        throwsArgumentError,
      );

      await expectLater(
        api.postJson(
          'test/idempotency-transport',
          body: const <String, dynamic>{},
          idempotencyKey: List<String>.filled(256, 'x').join(),
        ),
        throwsArgumentError,
      );
    });

    test('adds bearer authorization when a token is supplied', () async {
      final mock = MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.headers['authorization'], 'Bearer refresh-example');
        expect(jsonDecode(request.body), <String, dynamic>{});

        return http.Response('{"success":true,"data":{}}', 200);
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);

      expect(
        await api.postJson(
          'api/v1/auth/refresh',
          body: const <String, dynamic>{},
          bearerToken: 'refresh-example',
        ),
        {'success': true, 'data': <String, dynamic>{}},
      );
    });

    test('turns non-success HTTP status into a typed failure', () async {
      final mock = MockClient((request) async {
        return http.Response(
          '{"success":false,"error":"Invalid email or password"}',
          401,
        );
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);

      await expectLater(
        api.postJson(
          'api/v1/auth/login',
          body: {
            'email': 'clinician@example.test',
            'password': 'wrong-password',
          },
        ),
        throwsA(
          isA<ApiException>()
              .having((error) => error.kind, 'kind', ApiFailureKind.httpStatus)
              .having((error) => error.statusCode, 'statusCode', 401),
        ),
      );
    });

    test('rejects malformed JSON from a successful response', () async {
      final mock = MockClient((request) async {
        return http.Response('not-json', 200);
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);

      await expectLater(
        api.postJson(
          'api/v1/auth/login',
          body: {'email': 'clinician@example.test', 'password': 'password'},
        ),
        throwsA(
          isA<ApiException>().having(
            (error) => error.kind,
            'kind',
            ApiFailureKind.invalidResponse,
          ),
        ),
      );
    });

    test('rejects an empty bearer token before making a request', () async {
      final mock = MockClient((request) async {
        fail('The HTTP request should not be sent.');
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);

      expect(
        () => api.postJson(
          'api/v1/auth/refresh',
          body: const <String, dynamic>{},
          bearerToken: '  ',
        ),
        throwsArgumentError,
      );
    });

    test('maps POST timeout to a typed failure', () async {
      final mock = MockClient((request) => Completer<http.Response>().future);
      addTearDown(mock.close);

      final api = ApiClient(
        config: testConfig(),
        client: mock,
        requestTimeout: const Duration(milliseconds: 20),
      );

      await expectLater(
        api.postJson('api/v1/auth/login', body: const <String, dynamic>{}),
        throwsA(
          isA<ApiException>().having(
            (error) => error.kind,
            'kind',
            ApiFailureKind.timeout,
          ),
        ),
      );
    });
  });
}
