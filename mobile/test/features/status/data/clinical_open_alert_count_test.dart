import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';

void main() {
  test(
    'P22 S04.2 gate 1 uses backend-filtered open total, not first-page length',
    () async {
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/clinical-safety/alerts');
        expect(request.url.queryParameters['status'], 'open');
        expect(request.url.queryParameters['per_page'], '1');
        expect(request.headers['Authorization'], 'Bearer test-token');
        return http.Response(
          jsonEncode({'items': [], 'total': 12, 'page': 1, 'per_page': 1}),
          200,
        );
      });
      addTearDown(client.close);
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      );
      addTearDown(api.close);
      final source = ClinicalSafetyRemoteDataSource(
        apiClient: api,
        loadAccessToken: () async => 'test-token',
      );
      expect(await source.fetchOpenAlertCount(), 12);
    },
  );

  test('P22 S04.2 gate 1 rejects malformed or unfiltered totals', () async {
    final client = MockClient((request) async {
      return http.Response(
        jsonEncode({'items': [], 'total': -1, 'page': 1, 'per_page': 1}),
        200,
      );
    });
    addTearDown(client.close);
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    addTearDown(api.close);
    final source = ClinicalSafetyRemoteDataSource(
      apiClient: api,
      loadAccessToken: () async => 'test-token',
    );
    await expectLater(source.fetchOpenAlertCount(), throwsFormatException);
  });
}
