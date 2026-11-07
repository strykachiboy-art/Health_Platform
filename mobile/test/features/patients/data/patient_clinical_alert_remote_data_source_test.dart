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
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert.dart';
import 'package:clinic_system_pro/features/patients/data/patient_clinical_alert_remote_data_source.dart';

AuthSessionState _ready({
  String role = 'doctor',
  int? clinicId = 3,
  int userId = 42,
  String token = 's04-alert-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 's04-alert-refresh',
    userId: userId,
    role: role,
    clinicContextId: clinicId,
  ),
);

Map<String, dynamic> _alert({
  int id = 11,
  int patientId = 7,
  int clinicId = 3,
  String status = 'open',
  String severity = 'critical',
  String action = 'block',
}) => {
  'id': id,
  'clinic_id': clinicId,
  'patient_id': patientId,
  'rule_id': 20,
  'rule_version': 2,
  'severity': severity,
  'action': action,
  'status': status,
  'title': 'Clinical safety alert',
  'message': 'Verify the server-reported clinical alert.',
  'context': <String, dynamic>{},
  'source_type': 'prescription',
  'source_id': 41,
  'deduplication_key': null,
  'generated_at': '2026-11-06T09:00:00+00:00',
  'resolved_at': status == 'resolved' ? '2026-11-06T10:00:00+00:00' : null,
  'created_at': '2026-11-06T09:00:00+00:00',
  'updated_at': '2026-11-06T09:00:00+00:00',
};

Map<String, dynamic> _page({
  List<Map<String, dynamic>>? items,
  int? total,
  int page = 1,
  int perPage = 20,
}) {
  final rows = items ?? [_alert()];

  return {
    'items': rows,
    'total': total ?? rows.length,
    'page': page,
    'per_page': perPage,
  };
}

PatientClinicalAlertRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => PatientClinicalAlertRemoteDataSource(
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
  test('GET requires patient filter and reuses alert model', () async {
    var calls = 0;

    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/clinical-safety/alerts');
      expect(request.url.queryParameters, {
        'patient_id': '7',
        'page': '1',
        'per_page': '20',
      });
      expect(request.headers['authorization'], 'Bearer s04-alert-token');
      expect(request.headers.containsKey('idempotency-key'), isFalse);

      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final result = await _source(
      client,
      guard,
    ).fetchPatientAlerts(patientId: 7);

    expect(calls, 1);
    expect(result.total, 1);
    expect(result.items.single.patientId, 7);
    expect(result.items.single.clinicId, 3);
    expect(result.items.single.severity, ClinicalAlertSeverity.critical);
    expect(result.items.single.action, ClinicalAlertAction.block);
    expect(result.items.single.status, ClinicalAlertStatus.open);
    expect(result.items.single.generatedAt.isUtc, isTrue);
  });

  test('backend status filter is sent and verified', () async {
    final client = MockClient((request) async {
      expect(request.url.queryParameters, {
        'patient_id': '7',
        'page': '1',
        'per_page': '20',
        'status': 'resolved',
      });

      return http.Response(
        jsonEncode(_page(items: [_alert(status: 'resolved')])),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final result = await _source(
      client,
      guard,
    ).fetchPatientAlerts(patientId: 7, status: ClinicalAlertStatus.resolved);

    expect(result.items.single.status, ClinicalAlertStatus.resolved);
    expect(result.items.single.resolvedAt, isNotNull);
  });

  test('genuinely empty filtered page remains empty', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(items: [], total: 0)), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final page = await _source(client, guard).fetchPatientAlerts(patientId: 7);

    expect(page.items, isEmpty);
    expect(page.total, 0);
  });

  for (final role in <String>[
    'admin',
    'super_admin',
    'doctor',
    'nurse',
    'pharmacist',
    'lab_technician',
    'paramedic',
    'emt',
  ]) {
    test('authorized role $role can request scoped alerts', () async {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_page()), 200),
      );
      addTearDown(client.close);

      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      final result = await _source(
        client,
        guard,
        loader: () async => _ready(role: role),
      ).fetchPatientAlerts(patientId: 7);

      expect(result.items.single.id, 11);
    });
  }

  test('unauthorized roles make zero requests', () async {
    var calls = 0;

    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    for (final role in ['patient', 'receptionist']) {
      await expectLater(
        _source(
          client,
          guard,
          loader: () async => _ready(role: role),
        ).fetchPatientAlerts(patientId: 7),
        throwsA(isA<PatientClinicalAlertSessionMismatchException>()),
      );
    }

    expect(calls, 0);
  });

  test('missing clinic and signed-out state block GET', () async {
    var calls = 0;

    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    for (final state in <AuthSessionState>[
      _ready(clinicId: null),
      const AuthSessionState.signedOut(),
      const AuthSessionState.recoveryRequired(),
    ]) {
      await expectLater(
        _source(
          client,
          guard,
          loader: () async => state,
        ).fetchPatientAlerts(patientId: 7),
        throwsA(isA<PatientClinicalAlertSessionMismatchException>()),
      );
    }

    expect(calls, 0);
  });

  test('inactive session guard prevents GET', () async {
    var calls = 0;

    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard).fetchPatientAlerts(patientId: 7),
      throwsA(isA<PatientClinicalAlertSessionMismatchException>()),
    );

    expect(calls, 0);
  });

  test('logout during HTTP discards the alert response', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();

    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final future = _source(client, guard).fetchPatientAlerts(patientId: 7);

    await started.future;
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_page()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientClinicalAlertSessionMismatchException>()),
    );
  });

  test('clinic switch during HTTP discards stale alerts', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
    var state = _ready();

    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final future = _source(
      client,
      guard,
      loader: () async => state,
    ).fetchPatientAlerts(patientId: 7);

    await started.future;

    state = _ready(clinicId: 9, token: 'new-token');
    pending.complete(http.Response(jsonEncode(_page()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientClinicalAlertSessionMismatchException>()),
    );
  });

  test('new account during HTTP cannot inherit old alerts', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
    var state = _ready();

    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final future = _source(
      client,
      guard,
      loader: () async => state,
    ).fetchPatientAlerts(patientId: 7);

    await started.future;

    state = _ready(userId: 99);
    pending.complete(http.Response(jsonEncode(_page()), 200));

    await expectLater(
      future,
      throwsA(isA<PatientClinicalAlertSessionMismatchException>()),
    );
  });

  test('wrong patient in response fails closed', () async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(_page(items: [_alert(patientId: 99)])), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard).fetchPatientAlerts(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('wrong clinic in response fails closed', () async {
    final client = MockClient(
      (_) async =>
          http.Response(jsonEncode(_page(items: [_alert(clinicId: 99)])), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard).fetchPatientAlerts(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('duplicate alert ID is rejected', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(_page(items: [_alert(), _alert()], total: 2)),
        200,
      ),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard).fetchPatientAlerts(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('mismatched pagination metadata is rejected', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(page: 2)), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard).fetchPatientAlerts(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('item count cannot exceed reported total', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(total: 0)), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard).fetchPatientAlerts(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('status-filter mismatch is rejected', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(_page(items: [_alert(status: 'acknowledged')])),
        200,
      ),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(
        client,
        guard,
      ).fetchPatientAlerts(patientId: 7, status: ClinicalAlertStatus.open),
      throwsA(isA<FormatException>()),
    );
  });

  test('invalid alert action fails closed', () async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(_page(items: [_alert(action: 'unknown_action')])),
        200,
      ),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard).fetchPatientAlerts(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('malformed flat envelope is rejected', () async {
    final client = MockClient(
      (_) async => http.Response('{"success":true,"data":{"items":[]}}', 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard).fetchPatientAlerts(patientId: 7),
      throwsA(isA<FormatException>()),
    );
  });

  test('invalid patient and pagination never dispatch HTTP', () async {
    var calls = 0;

    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);

    await expectLater(
      source.fetchPatientAlerts(patientId: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchPatientAlerts(patientId: 7, page: 0),
      throwsArgumentError,
    );
    await expectLater(
      source.fetchPatientAlerts(patientId: 7, perPage: 501),
      throwsArgumentError,
    );

    expect(calls, 0);
  });

  test('HTTP 403 stays typed without automatic retry', () async {
    var calls = 0;

    final client = MockClient((_) async {
      calls++;
      return http.Response('{"error":"Forbidden"}', 403);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await expectLater(
      _source(client, guard).fetchPatientAlerts(patientId: 7),
      throwsA(
        isA<ApiException>().having(
          (error) => error.statusCode,
          'statusCode',
          403,
        ),
      ),
    );

    expect(calls, 1);
  });
}
