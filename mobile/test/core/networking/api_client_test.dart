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

    test('forwards a caller-supplied PATCH idempotency key', () async {
      final mock = MockClient((request) async {
        expect(request.method, 'PATCH');
        expect(
          request.headers['idempotency-key'],
          'mobile-patch-operation-001',
        );
        expect(jsonDecode(request.body), {'sample_value': 'updated'});
        return http.Response('{"success":true,"data":{}}', 200);
      });
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);
      addTearDown(api.close);

      await api.patchJson(
        'test/idempotency-transport',
        body: const <String, dynamic>{'sample_value': 'updated'},
        idempotencyKey: 'mobile-patch-operation-001',
      );
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

    test(
      'records HTTP response receipt even when the status is unsuccessful',
      () async {
        final mock = MockClient((request) async {
          return http.Response('{"success":false}', 503);
        });
        addTearDown(mock.close);

        final api = ApiClient(config: testConfig(), client: mock);
        addTearDown(api.close);

        final eventExpectation = expectLater(
          api.communicationStatusChanges,
          emits(ApiCommunicationStatus.responseReceived),
        );

        await expectLater(
          api.getJson('health/live'),
          throwsA(
            isA<ApiException>()
                .having(
                  (error) => error.kind,
                  'kind',
                  ApiFailureKind.httpStatus,
                )
                .having((error) => error.statusCode, 'statusCode', 503),
          ),
        );

        await eventExpectation;
        expect(
          api.communicationStatus,
          ApiCommunicationStatus.responseReceived,
        );
      },
    );

    test(
      'records missing responses for connection errors and timeouts',
      () async {
        final connectionMock = MockClient((request) async {
          throw http.ClientException('Connection unavailable', request.url);
        });
        addTearDown(connectionMock.close);

        final connectionApi = ApiClient(
          config: testConfig(),
          client: connectionMock,
        );
        addTearDown(connectionApi.close);

        final connectionEvent = expectLater(
          connectionApi.communicationStatusChanges,
          emits(ApiCommunicationStatus.responseNotReceived),
        );

        await expectLater(
          connectionApi.getJson('health/live'),
          throwsA(
            isA<ApiException>().having(
              (error) => error.kind,
              'kind',
              ApiFailureKind.connection,
            ),
          ),
        );

        await connectionEvent;
        expect(
          connectionApi.communicationStatus,
          ApiCommunicationStatus.responseNotReceived,
        );

        final timeoutMock = MockClient(
          (request) => Completer<http.Response>().future,
        );
        addTearDown(timeoutMock.close);

        final timeoutApi = ApiClient(
          config: testConfig(),
          client: timeoutMock,
          requestTimeout: const Duration(milliseconds: 20),
        );
        addTearDown(timeoutApi.close);

        final timeoutEvent = expectLater(
          timeoutApi.communicationStatusChanges,
          emits(ApiCommunicationStatus.responseNotReceived),
        );

        await expectLater(
          timeoutApi.getJson('health/live'),
          throwsA(
            isA<ApiException>().having(
              (error) => error.kind,
              'kind',
              ApiFailureKind.timeout,
            ),
          ),
        );

        await timeoutEvent;
        expect(
          timeoutApi.communicationStatus,
          ApiCommunicationStatus.responseNotReceived,
        );
      },
    );

    test(
      'late older response cannot overwrite a newer request transport result',
      () async {
        final olderResponse = Completer<http.Response>();
        final newerResponse = Completer<http.Response>();
        final olderStarted = Completer<void>();
        final newerStarted = Completer<void>();

        final mock = MockClient((request) {
          if (request.url.path.endsWith('/older')) {
            if (!olderStarted.isCompleted) olderStarted.complete();
            return olderResponse.future;
          }

          if (request.url.path.endsWith('/newer')) {
            if (!newerStarted.isCompleted) newerStarted.complete();
            return newerResponse.future;
          }

          throw StateError('Unexpected request path: ${request.url.path}');
        });
        addTearDown(mock.close);

        final api = ApiClient(config: testConfig(), client: mock);
        addTearDown(api.close);

        final events = <ApiCommunicationStatus>[];
        final subscription = api.communicationStatusChanges.listen(events.add);
        addTearDown(subscription.cancel);

        final olderRequest = api.getJson('older');
        await olderStarted.future;

        final newerRequest = api.getJson('newer');
        await newerStarted.future;

        newerResponse.completeError(
          http.ClientException(
            'Newer request failed',
            Uri.parse('http://127.0.0.1:5000/newer'),
          ),
        );

        await expectLater(
          newerRequest,
          throwsA(
            isA<ApiException>().having(
              (error) => error.kind,
              'kind',
              ApiFailureKind.connection,
            ),
          ),
        );

        expect(
          api.communicationStatus,
          ApiCommunicationStatus.responseNotReceived,
        );

        olderResponse.complete(http.Response('{"success":true}', 200));

        await expectLater(olderRequest, completion(equals({'success': true})));

        expect(
          api.communicationStatus,
          ApiCommunicationStatus.responseNotReceived,
        );
        expect(events, [ApiCommunicationStatus.responseNotReceived]);
      },
    );

    test(
      'older response is ignored while the newest request is still pending',
      () async {
        final olderResponse = Completer<http.Response>();
        final newerResponse = Completer<http.Response>();
        final olderStarted = Completer<void>();
        final newerStarted = Completer<void>();

        final mock = MockClient((request) {
          if (request.url.path.endsWith('/older')) {
            if (!olderStarted.isCompleted) olderStarted.complete();
            return olderResponse.future;
          }

          if (request.url.path.endsWith('/newer')) {
            if (!newerStarted.isCompleted) newerStarted.complete();
            return newerResponse.future;
          }

          throw StateError('Unexpected request path: ${request.url.path}');
        });
        addTearDown(mock.close);

        final api = ApiClient(config: testConfig(), client: mock);
        addTearDown(api.close);

        final events = <ApiCommunicationStatus>[];
        final subscription = api.communicationStatusChanges.listen(events.add);
        addTearDown(subscription.cancel);

        final olderRequest = api.getJson('older');
        await olderStarted.future;

        final newerRequest = api.getJson('newer');
        await newerStarted.future;

        // The older success arrives first, while the newer request is pending.
        olderResponse.complete(http.Response('{"success":true}', 200));
        await expectLater(olderRequest, completion(equals({'success': true})));

        final statusWhileNewerPending = api.communicationStatus;
        final eventsWhileNewerPending = List<ApiCommunicationStatus>.of(events);

        // Finish all requests before assertions so the test cannot leave
        // an unresolved mock request behind if the regression is present.
        newerResponse.completeError(
          http.ClientException(
            'Newer request failed',
            Uri.parse('http://127.0.0.1:5000/newer'),
          ),
        );

        await expectLater(
          newerRequest,
          throwsA(
            isA<ApiException>().having(
              (error) => error.kind,
              'kind',
              ApiFailureKind.connection,
            ),
          ),
        );

        expect(statusWhileNewerPending, ApiCommunicationStatus.notChecked);
        expect(eventsWhileNewerPending, isEmpty);
        expect(
          api.communicationStatus,
          ApiCommunicationStatus.responseNotReceived,
        );
        expect(events, [ApiCommunicationStatus.responseNotReceived]);
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

    test(
      'PATCH sends authenticated JSON and returns a decoded object',
      () async {
        final mock = MockClient((request) async {
          expect(request.method, 'PATCH');
          expect(request.url.path, '/api/v1/patients/7');
          expect(request.headers['authorization'], 'Bearer test-access-token');
          expect(request.headers['content-type'], 'application/json');
          expect(jsonDecode(request.body), {'first_name': 'Grace'});
          return http.Response('{"success":true,"data":{"id":7}}', 200);
        });
        addTearDown(mock.close);

        final api = ApiClient(config: testConfig(), client: mock);
        final result = await api.patchJson(
          'api/v1/patients/7',
          body: {'first_name': 'Grace'},
          bearerToken: 'test-access-token',
        );

        expect(result, {
          'success': true,
          'data': {'id': 7},
        });
      },
    );

    test('parses a stable server code from an HTTP error', () async {
      final mock = MockClient(
        (request) async => http.Response(
          '{"success":false,"error":"Schedule conflict","code":"APPOINTMENT_PATIENT_SCHEDULE_CONFLICT"}',
          409,
        ),
      );
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);
      addTearDown(api.close);

      await expectLater(
        api.getJson('api/v1/appointments'),
        throwsA(
          isA<ApiException>().having(
            (error) => error.serverCode,
            'serverCode',
            'APPOINTMENT_PATIENT_SCHEDULE_CONFLICT',
          ),
        ),
      );
    });

    test('ignores malformed server conflict codes', () async {
      final mock = MockClient(
        (request) async => http.Response(
          '{"success":false,"error":"Conflict","code":"appointment-conflict"}',
          409,
        ),
      );
      addTearDown(mock.close);

      final api = ApiClient(config: testConfig(), client: mock);
      addTearDown(api.close);

      await expectLater(
        api.getJson('api/v1/appointments'),
        throwsA(
          isA<ApiException>().having(
            (error) => error.serverCode,
            'serverCode',
            isNull,
          ),
        ),
      );
    });

    test(
      'API exception diagnostics exclude potentially sensitive details',
      () async {
        const accessTokenMarker = 'ACCESS_TOKEN_SENTINEL_DO_NOT_LOG';
        const patientDataMarker = 'PATIENT_DATA_SENTINEL_DO_NOT_LOG';
        const rawResponseMarker = 'RAW_RESPONSE_SENTINEL_DO_NOT_LOG';
        const uriQueryMarker = 'URI_QUERY_SENTINEL_DO_NOT_LOG';

        final mock = MockClient(
          (request) async => http.Response(
            jsonEncode({
              'message':
                  '$accessTokenMarker $patientDataMarker $rawResponseMarker',
              'details': [
                {
                  'loc': ['body', 'first_name'],
                  'msg':
                      'Rejected $accessTokenMarker $patientDataMarker $rawResponseMarker',
                },
              ],
            }),
            422,
          ),
        );
        addTearDown(mock.close);

        final api = ApiClient(config: testConfig(), client: mock);
        addTearDown(api.close);

        ApiException? captured;
        try {
          await api.patchJson(
            'api/v1/patients/7?marker=$uriQueryMarker',
            body: <String, dynamic>{'first_name': patientDataMarker},
          );
          fail('Expected the mock backend to return HTTP 422.');
        } on ApiException catch (error) {
          captured = error;
        }

        expect(captured, isNotNull);
        final error = captured!;

        // Preserve parsed details for controlled UI feedback.
        expect(error.statusCode, 422);
        expect(error.serverMessage, contains(accessTokenMarker));
        expect(
          error.validationErrors['first_name'],
          contains(patientDataMarker),
        );
        expect(error.uri.query, contains(uriQueryMarker));

        // Diagnostic formatting must not expose them.
        final diagnostic = error.toString();
        expect(diagnostic, contains('422'));
        for (final marker in <String>[
          accessTokenMarker,
          patientDataMarker,
          rawResponseMarker,
          uriQueryMarker,
        ]) {
          expect(
            diagnostic,
            isNot(contains(marker)),
            reason: 'Diagnostic output exposed a sensitive sentinel.',
          );
        }
      },
    );
    test(
      'HTTP validation metadata is parsed without retaining raw body',
      () async {
        final mock = MockClient(
          (request) async => http.Response(
            jsonEncode({
              'message': 'Validation failed',
              'details': [
                {
                  'loc': ['body', 'first_name'],
                  'msg': 'String should have at least 1 character',
                },
                {
                  'loc': ['body', 'email'],
                  'msg': 'Invalid email address',
                },
              ],
            }),
            422,
          ),
        );
        addTearDown(mock.close);

        final api = ApiClient(config: testConfig(), client: mock);
        await expectLater(
          api.patchJson('api/v1/patients/7', body: {'first_name': ''}),
          throwsA(
            isA<ApiException>()
                .having((error) => error.statusCode, 'statusCode', 422)
                .having(
                  (error) => error.serverMessage,
                  'serverMessage',
                  'Validation failed',
                )
                .having(
                  (error) => error.validationErrors['first_name'],
                  'first name error',
                  'String should have at least 1 character',
                )
                .having(
                  (error) => error.validationErrors['email'],
                  'email error',
                  'Invalid email address',
                ),
          ),
        );
      },
    );
  });
}
