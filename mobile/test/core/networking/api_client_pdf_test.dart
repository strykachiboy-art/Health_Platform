import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';

final Uint8List _pdf = Uint8List.fromList(
  ascii.encode('%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF'),
);

Map<String, String> _headers() => <String, String>{
  'content-type': 'application/pdf',
  'content-disposition': 'attachment; filename="invoice-41.pdf"',
  'cache-control': 'private, no-store, max-age=0',
  'x-content-type-options': 'nosniff',
  'x-clinical-release': 'none',
  'x-source-sha256': List<String>.filled(64, 'a').join(),
};

void main() {
  test(
    'binary GET uses bearer, no redirects, and bounded PDF response',
    () async {
      var calls = 0;
      final mock = MockClient((req) async {
        calls++;
        expect(req.method, 'GET');
        expect(req.url.path, '/api/v1/printing/invoice/41.pdf');
        expect(req.headers['authorization'], 'Bearer test-token');
        expect(req.headers['accept'], 'application/pdf');
        expect(req.followRedirects, isFalse);
        expect(req.headers.containsKey('idempotency-key'), isFalse);
        return http.Response.bytes(_pdf, 200, headers: _headers());
      });
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: mock,
      );
      addTearDown(() {
        api.close();
        mock.close();
      });
      final bytes = await api.getPdf(
        'api/v1/printing/invoice/41.pdf',
        bearerToken: 'test-token',
      );
      expect(bytes, orderedEquals(_pdf));
      expect(calls, 1);
    },
  );

  test(
    'never permits arbitrary origins, query, or unsupported endpoints',
    () async {
      var calls = 0;
      final mock = MockClient((req) async {
        calls++;
        throw StateError('Transport should not be reached');
      });
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: mock,
      );
      addTearDown(() {
        api.close();
        mock.close();
      });
      for (final path in <String>[
        'https://evil.example/api/v1/printing/invoice/41.pdf',
        'api/v1/printing/invoice/41.pdf?clinic_id=4',
        'api/v1/printing/final_signed/41.pdf',
        'api/v1/printing/invoice/0.pdf',
      ]) {
        await expectLater(
          api.getPdf(path, bearerToken: 'test-token'),
          throwsArgumentError,
        );
      }
      expect(calls, 0);
    },
  );

  test(
    'HTTP redirect is refused and 401 invokes existing auth cleanup',
    () async {
      var calls = 0;
      final mock = MockClient((req) async {
        calls++;
        if (calls == 1) {
          return http.Response(
            '',
            302,
            headers: {'location': 'https://another-host.example/steal'},
          );
        }
        return http.Response('{}', 401);
      });
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: mock,
      );
      var rejected = 0;
      api.onAuthenticationRejected = () => rejected++;
      addTearDown(() {
        api.close();
        mock.close();
      });
      await expectLater(
        api.getPdf('api/v1/printing/invoice/41.pdf', bearerToken: 'token'),
        throwsException,
      );
      expect(rejected, 0);
      await expectLater(
        api.getPdf('api/v1/printing/invoice/41.pdf', bearerToken: 'token'),
        throwsException,
      );
      expect(rejected, 1);
      expect(calls, 2);
    },
  );

  test(
    'rejects untrusted clinical metadata, HTML and oversize bodies',
    () async {
      var variant = 0;
      final mock = MockClient((req) async {
        variant++;
        if (variant == 1) {
          final h = _headers()..remove('x-clinical-release');
          return http.Response.bytes(_pdf, 200, headers: h);
        }
        if (variant == 2) {
          return http.Response('<html>login</html>', 200, headers: _headers());
        }
        return http.Response.bytes(
          Uint8List(3 * 1024 * 1024 + 1),
          200,
          headers: _headers(),
        );
      });
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: mock,
      );
      addTearDown(() {
        api.close();
        mock.close();
      });
      for (var i = 0; i < 3; i++) {
        await expectLater(
          api.getPdf('api/v1/printing/invoice/41.pdf', bearerToken: 'token'),
          throwsFormatException,
        );
      }
      expect(variant, 3);
    },
  );
}
