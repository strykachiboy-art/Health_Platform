import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';

const _config = BackendConfig(
  baseUrl: 'http://127.0.0.1:5000',
  allowInsecureHttp: true,
);

void main() {
  group('ApiClient additionalHeaders negative contract', () {
    for (final reserved in <String>[
      'Accept',
      'Authorization',
      'Content-Type',
      'Idempotency-Key',
    ]) {
      test('rejects reserved header $reserved before HTTP', () async {
        var requests = 0;

        final client = MockClient((request) async {
          requests++;

          return http.Response('{"success":true}', 200);
        });
        addTearDown(client.close);

        final api = ApiClient(config: _config, client: client);
        addTearDown(api.close);

        await expectLater(
          api.getJson(
            'api/v1/patients/7',
            bearerToken: 'test-token',
            additionalHeaders: <String, String>{reserved: 'injected'},
          ),
          throwsArgumentError,
        );

        expect(requests, 0);
      });
    }

    test('rejects blank additional header values before HTTP', () async {
      var requests = 0;

      final client = MockClient((request) async {
        requests++;

        return http.Response('{"success":true}', 200);
      });
      addTearDown(client.close);

      final api = ApiClient(config: _config, client: client);
      addTearDown(api.close);

      await expectLater(
        api.getJson(
          'api/v1/patients/7',
          additionalHeaders: const <String, String>{'X-Test-Header': '   '},
        ),
        throwsArgumentError,
      );

      expect(requests, 0);
    });

    test('rejects CRLF additional header values before HTTP', () async {
      var requests = 0;

      final client = MockClient((request) async {
        requests++;

        return http.Response('{"success":true}', 200);
      });
      addTearDown(client.close);

      final api = ApiClient(config: _config, client: client);
      addTearDown(api.close);

      await expectLater(
        api.getJson(
          'api/v1/patients/7',
          additionalHeaders: const <String, String>{
            'X-Test-Header': 'value\r\nInjected: true',
          },
        ),
        throwsArgumentError,
      );

      expect(requests, 0);
    });

    test('rejects malformed additional header names before HTTP', () async {
      var requests = 0;

      final client = MockClient((request) async {
        requests++;

        return http.Response('{"success":true}', 200);
      });
      addTearDown(client.close);

      final api = ApiClient(config: _config, client: client);
      addTearDown(api.close);

      await expectLater(
        api.getJson(
          'api/v1/patients/7',
          additionalHeaders: const <String, String>{'Bad Header': 'value'},
        ),
        throwsArgumentError,
      );

      expect(requests, 0);
    });
  });
}
