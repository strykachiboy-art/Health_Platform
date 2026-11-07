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
import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

AuthSessionState _ready({
  int userId = 42,
  int? clinicId = 3,
  String role = 'doctor',
  String token = 'admission-test-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'admission-refresh-test',
    userId: userId,
    role: role,
    clinicContextId: clinicId,
  ),
);

Map<String, dynamic> _admission({
  int patientId = 7,
  String status = 'admitted',
}) => {
  'id': 11,
  'patient_id': patientId,
  'bed_id': 5,
  'admitted_by_id': 14,
  'reservation_id': null,
  'status': status,
  'reason': 'Clinical observation',
  'admitted_at': '2026-11-06T09:00:00+00:00',
  'discharged_at': null,
  'created_at': '2026-11-06T08:59:00+00:00',
  'updated_at': '2026-11-06T09:00:00+00:00',
};

Map<String, dynamic> _page({
  List<Map<String, dynamic>>? items,
  int total = 1,
  int page = 1,
  int perPage = 50,
}) => {
  'items': items ?? [_admission()],
  'total': total,
  'page': page,
  'per_page': perPage,
};

PatientAdmissionRemoteDataSource _source(
  MockClient client, {
  required SessionNavigationGuard guard,
  Future<AuthSessionState> Function()? loader,
}) => PatientAdmissionRemoteDataSource(
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
  test('history GET uses raw pagination and current session token', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/wards/patients/7/admissions');
      expect(request.url.queryParameters, {'page': '1', 'per_page': '50'});
      expect(request.headers['authorization'], 'Bearer admission-test-token');
      expect(request.headers.containsKey('idempotency-key'), isFalse);
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final page = await _source(
      client,
      guard: guard,
    ).fetchAdmissionHistory(patientId: 7);

    expect(calls, 1);
    expect(page.total, 1);
    expect(page.items.single.patientId, 7);
    expect(page.items.single.status, PatientAdmissionStatus.admitted);
    expect(page.items.single.admittedAt!.isUtc, isTrue);
  });

  test('history permits a genuinely empty page', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(items: [], total: 0)), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final page = await _source(
      client,
      guard: guard,
    ).fetchAdmissionHistory(patientId: 7);

    expect(page.items, isEmpty);
    expect(page.total, 0);
  });

  test('current admission GET returns admitted record', () async {
    final client = MockClient((request) async {
      expect(request.url.path, '/api/v1/wards/patients/7/current-admission');
      expect(request.url.hasQuery, isFalse);
      return http.Response(jsonEncode(_admission()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final admission = await _source(
      client,
      guard: guard,
    ).fetchCurrentAdmission(patientId: 7);

    expect(admission, isNotNull);
    expect(admission!.id, 11);
    expect(admission.isActive, isTrue);
  });

  test('exact no-active-admission 404 becomes null', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{"message":"No active admission found"}', 404);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final admission = await _source(
      client,
      guard: guard,
    ).fetchCurrentAdmission(patientId: 7);

    expect(admission, isNull);
    expect(calls, 1);
  });

  test('unrelated 404 is never silently treated as empty', () async {
    final client = MockClient(
      (_) async => http.Response('{"error":"Patient not found"}', 404),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchCurrentAdmission(patientId: 7),
      throwsA(
        isA<ApiException>().having(
          (error) => error.statusCode,
          'statusCode',
          404,
        ),
      ),
    );
  });

  test('history 404 remains an API failure', () async {
    final client = MockClient(
      (_) async => http.Response('{"error":"Not found"}', 404),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchAdmissionHistory(patientId: 7),
      throwsA(isA<ApiException>()),
    );
  });

  test('current endpoint rejects non-admitted status', () async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(_admission(status: 'discharged')), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchCurrentAdmission(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('invalid input fails before HTTP', () async {
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
      source.fetchCurrentAdmission(patientId: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchAdmissionHistory(patientId: -1),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchAdmissionHistory(patientId: 7, page: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchAdmissionHistory(patientId: 7, perPage: 501),
      throwsArgumentError,
    );

    expect(calls, 0);
  });

  test('signed-out and recovery sessions issue no GET', () async {
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
        ).fetchAdmissionHistory(patientId: 7),
        throwsA(isA<PatientAdmissionSessionMismatchException>()),
      );
    }
    expect(calls, 0);
  });

  test('unauthorized roles and missing clinic issue no GET', () async {
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
      _ready(role: 'pharmacist'),
      _ready(role: 'super_admin'),
      _ready(clinicId: null),
    ]) {
      await expectLater(
        _source(
          client,
          guard: guard,
          loader: () async => state,
        ).fetchAdmissionHistory(patientId: 7),
        throwsA(isA<PatientAdmissionSessionMismatchException>()),
      );
    }
    expect(calls, 0);
  });

  test('guard blocks GET before session availability', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchAdmissionHistory(patientId: 7),
      throwsA(isA<PatientAdmissionSessionMismatchException>()),
    );

    expect(calls, 0);
  });

  test('logout during pending GET discards history', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
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
    ).fetchAdmissionHistory(patientId: 7);

    await started.future;
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_page()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientAdmissionSessionMismatchException>()),
    );
  });

  test('clinic switch during GET discards current admission', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
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
    ).fetchCurrentAdmission(patientId: 7);

    await started.future;
    session = _ready(clinicId: 9, token: 'rotated-token');
    pending.complete(http.Response(jsonEncode(_admission()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientAdmissionSessionMismatchException>()),
    );
  });

  test('new user during GET cannot inherit old admission', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
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
    ).fetchAdmissionHistory(patientId: 7);

    await started.future;
    session = _ready(userId: 99);
    pending.complete(http.Response(jsonEncode(_page()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientAdmissionSessionMismatchException>()),
    );
  });

  test('no-active 404 after logout cannot become empty state', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
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
    ).fetchCurrentAdmission(patientId: 7);

    await started.future;
    guard.invalidate();
    pending.complete(
      http.Response('{"message":"No active admission found"}', 404),
    );

    await expectLater(
      future,
      throwsA(isA<PatientAdmissionSessionMismatchException>()),
    );
  });

  for (final endpoint in <String>['history', 'current']) {
    test('$endpoint rejects cross-patient admission', () async {
      final row = _admission(patientId: 999);
      final body = endpoint == 'history' ? _page(items: [row]) : row;
      final client = MockClient(
        (_) async => http.Response(jsonEncode(body), 200),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      final source = _source(client, guard: guard);

      if (endpoint == 'history') {
        await expectLater(
          source.fetchAdmissionHistory(patientId: 7),
          throwsA(isA<FormatException>()),
        );
      } else {
        await expectLater(
          source.fetchCurrentAdmission(patientId: 7),
          throwsA(isA<FormatException>()),
        );
      }
    });
  }

  test('invalid pagination metadata fails closed', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(page: 2)), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchAdmissionHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('unknown admission status is rejected', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(_page(items: [_admission(status: 'unknown_status')])),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchAdmissionHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('timestamp without timezone is rejected', () async {
    final row = _admission()..['admitted_at'] = '2026-11-06T09:00:00';
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(items: [row])), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchAdmissionHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('malformed list envelope is rejected', () async {
    final client = MockClient(
      (_) async => http.Response('{"success":true,"data":{"items":[]}}', 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard: guard).fetchAdmissionHistory(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  for (final status in <int>[401, 403, 422, 500]) {
    test('HTTP $status is typed and not retried', () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response('{"error":"Rejected"}', status);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      await expectLater(
        _source(client, guard: guard).fetchAdmissionHistory(patientId: 7),
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
