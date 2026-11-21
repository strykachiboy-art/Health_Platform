import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/administration/data/wd05_report_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _ready({
  int clinic = 7,
  String role = 'admin',
  String token = 'test-only-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'test-refresh',
    userId: 37,
    role: role,
    clinicContextId: clinic,
    tenantId: '11111111-1111-1111-1111-111111111111',
  ),
);

Map<String, dynamic> _record({int clinic = 7, int id = 18}) => {
  'id': id,
  'clinic_id': clinic,
  'report_type': 'billing',
  'report_format': 'csv',
  'generated_by_id': 37,
  'filters': null,
  'file_url': 'https://other.example.test/NEVER_DOWNLOAD',
  'created_at': '2026-11-20T22:00:00+00:00',
  'updated_at': '2026-11-20T22:00:00+00:00',
};

http.Response _response({int clinic = 7, int page = 1, int total = 1}) =>
    http.Response(
      jsonEncode({
        'success': true,
        'data': {
          'items': [_record(clinic: clinic)],
          'total': total,
          'page': page,
          'per_page': 20,
        },
      }),
      200,
    );

Wd05ReportReadRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard,
  Future<AuthSessionState> Function() session,
) => Wd05ReportReadRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: session,
  sessionNavigationGuard: guard,
);

void main() {
  test(
    'WD05 report reader requests only filtered authenticated GET metadata',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/reports');
        expect(request.headers['authorization'], 'Bearer test-only-token');
        expect(request.url.queryParameters['report_type'], 'billing');
        expect(request.url.queryParameters['report_format'], 'csv');
        expect(request.url.queryParameters['date_from'], '2026-01-01');
        expect(request.url.queryParameters['date_to'], '2026-11-20');
        expect(request.url.queryParameters.containsKey('clinic_id'), isFalse);
        expect(request.url.queryParameters.containsKey('tenant_id'), isFalse);
        return _response();
      });
      addTearDown(client.close);
      final page = await _source(client, guard, () async => _ready())
          .listReports(
            reportType: 'billing',
            reportFormat: 'csv',
            dateFrom: '2026-01-01',
            dateTo: '2026-11-20',
          );
      expect(calls, 1);
      expect(page.items.single.clinicId, 7);
      expect(page.items.single.createdUtc.isUtc, isTrue);
      expect(page.items.single.type, 'billing');
      expect(page.hasNext, isFalse);
    },
  );

  test(
    'WD05 fails closed for super admin, patient or missing selected clinic',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return _response();
      });
      addTearDown(client.close);
      for (final state in <AuthSessionState>[
        _ready(role: 'super_admin'),
        _ready(role: 'patient'),
        _ready(clinic: 0),
      ]) {
        await expectLater(
          _source(client, guard, () async => state).listReports(),
          throwsA(isA<Wd05ReportSessionUnavailable>()),
        );
      }
      guard.invalidate();
      await expectLater(
        _source(client, guard, () async => _ready()).listReports(),
        throwsA(isA<Wd05ReportSessionUnavailable>()),
      );
      expect(calls, 0);
    },
  );

  test(
    'WD05 rejects cross-clinic, missing-clinic and duplicate report rows',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var body = {
        'success': true,
        'data': {
          'items': [_record(clinic: 8)],
          'page': 1,
          'per_page': 20,
          'total': 1,
        },
      };
      final client = MockClient(
        (request) async => http.Response(jsonEncode(body), 200),
      );
      addTearDown(client.close);
      final source = _source(client, guard, () async => _ready());
      await expectLater(source.listReports(), throwsA(isA<FormatException>()));
      body = {
        'success': true,
        'data': {
          'items': [_record(clinic: 7)..remove('clinic_id')],
          'page': 1,
          'per_page': 20,
          'total': 1,
        },
      };
      await expectLater(source.listReports(), throwsA(isA<FormatException>()));
      body = {
        'success': true,
        'data': {
          'items': [_record(), _record()],
          'page': 1,
          'per_page': 20,
          'total': 2,
        },
      };
      await expectLater(source.listReports(), throwsA(isA<FormatException>()));
    },
  );

  test(
    'WD05 session/tenant switch during HTTP response discards the row',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final response = Completer<http.Response>();
      final requested = Completer<void>();
      final client = MockClient((request) {
        requested.complete();
        return response.future;
      });
      addTearDown(client.close);
      var session = _ready();
      final source = _source(client, guard, () async => session);
      final pending = source.listReports();
      await requested.future;
      session = _ready(clinic: 8);
      response.complete(_response());
      await expectLater(pending, throwsA(isA<Wd05ReportSessionUnavailable>()));
    },
  );

  test('WD05 bad dates and unsupported types never cause HTTP', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return _response();
    });
    addTearDown(client.close);
    final source = _source(client, guard, () async => _ready());
    for (final range in <List<String>>[
      ['2026-02-30', '2026-03-01'],
      ['2026-10-01', '2026-01-01'],
      ['2026/01/01', '2026-11-20'],
    ]) {
      await expectLater(
        source.listReports(dateFrom: range[0], dateTo: range[1]),
        throwsA(isA<ArgumentError>()),
      );
    }
    await expectLater(
      source.listReports(reportType: 'fantasy'),
      throwsA(isA<ArgumentError>()),
    );
    await expectLater(
      source.listReports(reportFormat: 'html'),
      throwsA(isA<ArgumentError>()),
    );
    expect(calls, 0);
  });
}
