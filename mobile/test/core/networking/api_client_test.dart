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
  group('ApiClient', () {
    test('GET returns a decoded JSON object', () async {
      final mock = MockClient((request) async {
        expect(request.method, 'GET');
        expect(request.url, Uri.parse('http://127.0.0.1:5000/health/live'));
        expect(request.headers['accept'], 'application/json');

        return http.Response('{"success":true,"status":"ok"}', 200);
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);

      final result = await api.getJson('health/live');

      expect(result, {'success': true, 'status': 'ok'});
    });

    test(
      'PATCH sends authenticated JSON and decodes an object response',
      () async {
        final mock = MockClient((request) async {
          expect(request.method, 'PATCH');
          expect(
            request.url,
            Uri.parse('http://127.0.0.1:5000/api/v1/patients/7/status'),
          );
          expect(request.headers['accept'], 'application/json');
          expect(request.headers['content-type'], 'application/json');
          expect(request.headers['authorization'], 'Bearer test-access-token');
          expect(jsonDecode(request.body), {'is_active': false});
          return http.Response(
            '{"success":true,"data":{"is_active":false}}',
            200,
          );
        });
        addTearDown(mock.close);

        final api = ApiClient(config: testConfig(), client: mock);
        final result = await api.patchJson(
          'api/v1/patients/7/status',
          body: const <String, dynamic>{'is_active': false},
          bearerToken: 'test-access-token',
        );

        expect(result, {
          'success': true,
          'data': {'is_active': false},
        });
      },
    );

    test('non-success status becomes a typed failure', () async {
      final mock = MockClient((request) async {
        return http.Response('{"success":false}', 503);
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);

      await expectLater(
        api.getJson('health/live'),
        throwsA(
          isA<ApiException>()
              .having((error) => error.kind, 'kind', ApiFailureKind.httpStatus)
              .having((error) => error.statusCode, 'statusCode', 503),
        ),
      );
    });

    test('malformed JSON becomes an invalid-response failure', () async {
      final mock = MockClient((request) async {
        return http.Response('not-json', 200);
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);

      await expectLater(
        api.getJson('health/live'),
        throwsA(
          isA<ApiException>().having(
            (error) => error.kind,
            'kind',
            ApiFailureKind.invalidResponse,
          ),
        ),
      );
    });

    test('JSON arrays are rejected when an object is expected', () async {
      final mock = MockClient((request) async {
        return http.Response('[1,2,3]', 200);
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);

      await expectLater(
        api.getJson('health/live'),
        throwsA(
          isA<ApiException>().having(
            (error) => error.kind,
            'kind',
            ApiFailureKind.invalidResponse,
          ),
        ),
      );
    });

    test('request timeout becomes a typed failure', () async {
      final mock = MockClient((request) => Completer<http.Response>().future);
      addTearDown(mock.close);

      final api = ApiClient(
        config: testConfig(),
        client: mock,
        requestTimeout: const Duration(milliseconds: 20),
      );

      await expectLater(
        api.getJson('health/live'),
        throwsA(
          isA<ApiException>().having(
            (error) => error.kind,
            'kind',
            ApiFailureKind.timeout,
          ),
        ),
      );
    });

    test('connection errors become a typed failure', () async {
      final mock = MockClient((request) async {
        throw http.ClientException('Connection unavailable', request.url);
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);

      await expectLater(
        api.getJson('health/live'),
        throwsA(
          isA<ApiException>().having(
            (error) => error.kind,
            'kind',
            ApiFailureKind.connection,
          ),
        ),
      );
    });
  });
}
