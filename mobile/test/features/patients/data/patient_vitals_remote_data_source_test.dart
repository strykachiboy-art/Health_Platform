import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/patient_vitals_remote_data_source.dart';

AuthSessionState _ready({
  int userId = 42,
  int? clinicId = 3,
  String role = 'doctor',
  String token = 'vitals-test-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'vitals-test-refresh',
    userId: userId,
    role: role,
    clinicContextId: clinicId,
  ),
);

Map<String, dynamic> _vitals({int patientId = 7}) => {
  'id': 11,
  'patient_id': patientId,
  'consultation_id': null,
  'recorded_by_id': 14,
  'temperature': '36.70',
  'blood_pressure_systolic': 120,
  'blood_pressure_diastolic': 80,
  'heart_rate': 72,
  'respiratory_rate': 16,
  'oxygen_saturation': '98.00',
  'weight': '70.50',
  'height': '175.00',
  'recorded_at': '2026-11-06T09:00:00+00:00',
};

Map<String, dynamic> _page({
  List<Map<String, dynamic>>? items,
  int total = 1,
  int page = 1,
  int perPage = 50,
}) => {
  'success': true,
  'data': {
    'items': items ?? [_vitals()],
    'total': total,
    'page': page,
    'per_page': perPage,
  },
};

PatientVitalsRemoteDataSource _source(
  MockClient client, {
  required SessionNavigationGuard guard,
  Future<AuthSessionState> Function()? loader,
}) => PatientVitalsRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: loader ?? () async => _ready(),
  sessionNavigationGuard: guard,
);

void main() {
  test('vitals history GET uses authenticated paginated contract', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/patients/7/vitals');
      expect(request.url.queryParameters, {'page': '1', 'per_page': '50'});
      expect(request.headers['authorization'], 'Bearer vitals-test-token');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final page = await _source(
      client,
      guard: guard,
    ).fetchVitalsHistory(patientId: 7);

    expect(calls, 1);
    expect(page.total, 1);
    expect(page.items.single.patientId, 7);
    expect(page.items.single.temperature, '36.70');
    expect(page.items.single.oxygenSaturation, '98.00');
    expect(page.items.single.weight, '70.50');
    expect(page.items.single.height, '175.00');
    expect(page.items.single.heartRate, 72);
    expect(page.items.single.recordedAt.isUtc, isTrue);
  });

  test('latest vitals GET accepts a server-owned record', () async {
    final client = MockClient((request) async {
      expect(request.url.path, '/api/v1/patients/7/vitals/latest');
      expect(request.url.hasQuery, isFalse);
      return http.Response(
        jsonEncode({'success': true, 'data': _vitals()}),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final latest = await _source(
      client,
      guard: guard,
    ).fetchLatestVitals(patientId: 7);

    expect(latest, isNotNull);
    expect(latest!.id, 11);
    expect(latest.bloodPressureSystolic, 120);
  });

  test('latest success with explicit null is a valid empty state', () async {
    final client = MockClient(
      (_) async => http.Response('{"success":true,"data":null}', 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final latest = await _source(
      client,
      guard: guard,
    ).fetchLatestVitals(patientId: 7);

    expect(latest, isNull);
  });

  test('history supports a genuinely empty page', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(items: [], total: 0)), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final page = await _source(
      client,
      guard: guard,
    ).fetchVitalsHistory(patientId: 7);

    expect(page.items, isEmpty);
    expect(page.total, 0);
  });

  test('invalid patient and pagination fail before HTTP', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard: guard);

    await expectLater(
      source.fetchVitalsHistory(patientId: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchLatestVitals(patientId: -1),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchVitalsHistory(patientId: 7, page: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchVitalsHistory(patientId: 7, perPage: 501),
      throwsArgumentError,
    );
    expect(calls, 0);
  });

  test('signed-out and recovery states cannot issue GET', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    for (final state in <AuthSessionState>[
      const AuthSessionState.signedOut(),
      const AuthSessionState.recoveryRequired(),
    ]) {
      await expectLater(
        _source(
          client,
          guard: guard,
          loader: () async => state,
        ).fetchVitalsHistory(patientId: 7),
        throwsA(isA<PatientVitalsSessionMismatchException>()),
      );
    }
    expect(calls, 0);
  });

  test('patient, receptionist and missing clinic cannot issue GET', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    for (final state in [
      _ready(role: 'patient'),
      _ready(role: 'receptionist'),
      _ready(role: 'super_admin'),
      _ready(clinicId: null),
    ]) {
      await expectLater(
        _source(
          client,
          guard: guard,
          loader: () async => state,
        ).fetchVitalsHistory(patientId: 7),
        throwsA(isA<PatientVitalsSessionMismatchException>()),
      );
    }
    expect(calls, 0);
  });

  test('inactive navigation guard prevents GET', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchLatestVitals(patientId: 7),
      throwsA(isA<PatientVitalsSessionMismatchException>()),
    );
    expect(calls, 0);
  });

  test('logout while history GET is pending discards data', () async {
    final pending = Completer<http.Response>();
    final started = Completer<void>();

    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final future = _source(
      client,
      guard: guard,
    ).fetchVitalsHistory(patientId: 7);

    await started.future;
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_page()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientVitalsSessionMismatchException>()),
    );
  });

  test('clinic switch during latest GET discards result', () async {
    final pending = Completer<http.Response>();
    final started = Completer<void>();
    var session = _ready();

    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final future = _source(
      client,
      guard: guard,
      loader: () async => session,
    ).fetchLatestVitals(patientId: 7);

    await started.future;
    session = _ready(clinicId: 9, token: 'changed-token');
    pending.complete(
      http.Response(jsonEncode({'success': true, 'data': _vitals()}), 200),
    );

    await expectLater(
      future,
      throwsA(isA<PatientVitalsSessionMismatchException>()),
    );
  });

  test('new user during GET cannot inherit old vitals', () async {
    final pending = Completer<http.Response>();
    final started = Completer<void>();
    var session = _ready();

    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final future = _source(
      client,
      guard: guard,
      loader: () async => session,
    ).fetchVitalsHistory(patientId: 7);

    await started.future;
    session = _ready(userId: 99);
    pending.complete(http.Response(jsonEncode(_page()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientVitalsSessionMismatchException>()),
    );
  });

  for (final endpoint in <String>['history', 'latest']) {
    test('$endpoint rejects wrong patient ID in response', () async {
      final row = _vitals(patientId: 999);
      final response = endpoint == 'history'
          ? _page(items: [row])
          : {'success': true, 'data': row};

      final client = MockClient(
        (_) async => http.Response(jsonEncode(response), 200),
      );
      addTearDown(client.close);

      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard: guard);

      if (endpoint == 'history') {
        await expectLater(
          source.fetchVitalsHistory(patientId: 7),
          throwsA(isA<FormatException>()),
        );
      } else {
        await expectLater(
          source.fetchLatestVitals(patientId: 7),
          throwsA(isA<FormatException>()),
        );
      }
    });
  }

  test('unknown pagination metadata fails closed', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(page: 2)), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchVitalsHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('numeric JSON decimal is rejected rather than rounded', () async {
    final row = _vitals()..['temperature'] = 36.70;
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(items: [row])), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchVitalsHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('timestamp without timezone is rejected', () async {
    final row = _vitals()..['recorded_at'] = '2026-11-06T09:00:00';

    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(items: [row])), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchVitalsHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('missing latest data field is not an empty state', () async {
    final client = MockClient(
      (_) async => http.Response('{"success":true}', 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchLatestVitals(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('incorrect history envelope is rejected', () async {
    final client = MockClient(
      (_) async => http.Response('{"success":true,"data":[]}', 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchVitalsHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  for (final status in <int>[401, 403, 404, 422, 500]) {
    test('HTTP $status stays typed without automatic retry', () async {
      var calls = 0;

      final client = MockClient((_) async {
        calls++;
        return http.Response('{"error":"Request rejected"}', status);
      });
      addTearDown(client.close);

      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      await expectLater(
        _source(client, guard: guard).fetchVitalsHistory(patientId: 7),
        throwsA(
          isA<ApiException>().having(
            (error) => error.statusCode,
            'statusCode',
            status,
          ),
        ),
      );
      expect(calls, 1);
    });
  }
}
