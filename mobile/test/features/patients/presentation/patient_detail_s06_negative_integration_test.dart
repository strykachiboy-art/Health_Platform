import 'dart:async';

import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_clinical_alert_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_detail_screen.dart';

AuthSessionState _session(String role) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's04d-token',
    refreshToken: 's04d-refresh',
    userId: 42,
    clinicContextId: 3,
    role: role,
  ),
);

Map<String, dynamic> _patient({int id = 7}) => {
  'id': id,
  'clinic_id': 3,
  'patient_number': 'PT-0007',
  'first_name': 'Ada',
  'last_name': 'Lovelace',
  'date_of_birth': '1815-12-10',
  'gender': 'FEMALE',
  'blood_type': 'O_POSITIVE',
  'phone': '+2348000000000',
  'email': 'ada@example.test',
  'address': 'Test address',
  'allergies': 'Peanuts',
  'chronic_conditions': 'Asthma',
  'emirates_id': null,
  'umrn': 'UMRN-7',
  'is_active': true,
  'created_at': '2026-01-01T10:00:00+00:00',
  'updated_at': '2026-01-02T10:00:00+00:00',
};

Map<String, dynamic> _prescription() => {
  'id': 11,
  'clinic_id': 3,
  'patient_id': 7,
  'consultation_id': null,
  'prescribed_by_id': 8,
  'status': 'active',
  'notes': null,
  'issued_at': '2026-11-06T09:00:00',
  'expires_at': null,
  'items': [
    {
      'id': 111,
      'prescription_id': 11,
      'drug_id': 15,
      'dosage': '500 mg',
      'frequency': 'twice daily',
      'duration': '5 days',
      'quantity': 10,
      'instructions': 'Use as prescribed',
    },
  ],
};

Map<String, dynamic> _alert({int clinicId = 3}) => {
  'id': 21,
  'clinic_id': clinicId,
  'patient_id': 7,
  'rule_id': 20,
  'rule_version': 2,
  'severity': 'critical',
  'action': 'block',
  'status': 'open',
  'title': 'Clinical safety alert',
  'message': 'Server-reported clinical risk.',
  'context': <String, dynamic>{},
  'source_type': 'prescription',
  'source_id': 11,
  'deduplication_key': null,
  'generated_at': '2026-11-06T09:00:00+00:00',
  'resolved_at': null,
  'created_at': '2026-11-06T09:00:00+00:00',
  'updated_at': '2026-11-06T09:00:00+00:00',
};

http.Response _fixture(http.Request request, {int alertClinicId = 3}) {
  final path = request.url.path;

  if (path == '/api/v1/patients/7') {
    return http.Response(
      jsonEncode({'success': true, 'data': _patient()}),
      200,
    );
  }

  if (path == '/api/v1/prescriptions/patients/7') {
    if (request.url.queryParameters['page'] != '1' ||
        request.url.queryParameters['per_page'] != '20') {
      throw StateError('Unexpected prescription pagination');
    }

    return http.Response(
      jsonEncode({
        'success': true,
        'data': {
          'items': [_prescription()],
          'total': 1,
          'page': 1,
          'per_page': 20,
        },
      }),
      200,
    );
  }

  if (path == '/api/v1/clinical-safety/alerts') {
    if (request.url.queryParameters['patient_id'] != '7' ||
        request.url.queryParameters['page'] != '1' ||
        request.url.queryParameters['per_page'] != '20') {
      throw StateError('Unscoped clinical alert query');
    }

    return http.Response(
      jsonEncode({
        'items': [_alert(clinicId: alertClinicId)],
        'total': 1,
        'page': 1,
        'per_page': 20,
      }),
      200,
    );
  }

  throw StateError('Unexpected route: $path');
}

ApiClient _api(MockClient client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

PatientRemoteDataSource _patientSource(ApiClient api) =>
    PatientRemoteDataSource(
      apiClient: api,
      loadAccessToken: () async => 's04d-token',
    );

PatientPrescriptionRemoteDataSource _prescriptions(
  ApiClient api,
  SessionNavigationGuard guard,
  String role,
) => PatientPrescriptionRemoteDataSource(
  apiClient: api,
  loadSession: () async => _session(role),
  sessionNavigationGuard: guard,
);

PatientClinicalAlertRemoteDataSource _alerts(
  ApiClient api,
  SessionNavigationGuard guard,
  String role,
) => PatientClinicalAlertRemoteDataSource(
  apiClient: api,
  loadSession: () async => _session(role),
  sessionNavigationGuard: guard,
);

Widget _screen(
  ApiClient api,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => MaterialApp(
  home: PatientDetailScreen(
    dataSource: _patientSource(api),
    patientId: 7,
    prescriptionDataSource: _prescriptions(api, guard, role),
    clinicalAlertDataSource: _alerts(api, guard, role),
  ),
);

Future<void> _show(WidgetTester tester, String key) async {
  await tester.scrollUntilVisible(
    find.byKey(ValueKey(key)),
    200,
    maxScrolls: 60,
  );
  await tester.pumpAndSettle();
}

EmergencyAccessGrant _s06Grant() =>
    EmergencyAccessGrant.fromJson(<String, dynamic>{
      'id': 17,
      'clinic_id': 3,
      'patient_id': 7,
      'requester_user_id': 42,
      'requester_role': 'DOCTOR',
      'reason': 'Emergency treatment',
      'purpose': 'Immediate clinical review',
      'scope': <String>['patient:read'],
      'status': 'active',
      'requested_at': '2026-11-03T12:00:00+00:00',
      'granted_at': '2026-11-03T12:05:00+00:00',
      'expires_at': '2026-11-03T13:00:00+00:00',
      'revoked_at': null,
      'reviewed_at': '2026-11-03T12:05:00+00:00',
      'reviewed_by_user_id': 99,
      'review_notes': 'Approved',
    });

EmergencyAccessContext _s06Context() =>
    EmergencyAccessContext(nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30));

Widget _emergencyScreen(
  ApiClient api,
  SessionNavigationGuard guard,
  EmergencyAccessContext emergency,
) => MaterialApp(
  home: PatientDetailScreen(
    dataSource: _patientSource(api),
    patientId: 7,
    emergencyAccessContext: emergency,
    prescriptionDataSource: _prescriptions(api, guard, 'doctor'),
    clinicalAlertDataSource: _alerts(api, guard, 'doctor'),
  ),
);

void main() {
  testWidgets('emergency revocation removes patient and both clinical panels', (
    tester,
  ) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      expect(request.method, 'GET');
      paths.add(request.url.path);

      if (request.url.path == '/api/v1/patients/7') {
        expect(request.headers['x-emergency-access-id'], '17');
      }

      return _fixture(request);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final emergency = _s06Context()..activate(_s06Grant());
    addTearDown(emergency.dispose);

    await tester.pumpWidget(_emergencyScreen(_api(client), guard, emergency));
    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace'), findsOneWidget);

    await _show(tester, 'patient-prescriptions-card');
    expect(find.text('Dosage: 500 mg'), findsOneWidget);

    await _show(tester, 'patient-clinical-alerts-card');
    expect(find.text('Server-reported clinical risk.'), findsOneWidget);

    emergency.clear();
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-detail-name'), skipOffstage: false),
      findsNothing,
    );
    expect(
      find.byKey(
        const ValueKey('patient-prescriptions-card'),
        skipOffstage: false,
      ),
      findsNothing,
    );
    expect(
      find.byKey(
        const ValueKey('patient-clinical-alerts-card'),
        skipOffstage: false,
      ),
      findsNothing,
    );
    expect(
      find.text('Server-reported clinical risk.', skipOffstage: false),
      findsNothing,
    );
    expect(find.text('Dosage: 500 mg', skipOffstage: false), findsNothing);

    expect(paths, contains('/api/v1/patients/7'));
    expect(paths, contains('/api/v1/prescriptions/patients/7'));
    expect(paths, contains('/api/v1/clinical-safety/alerts'));
  });

  testWidgets('emergency patient 403 clears grant and blocks clinical GETs', (
    tester,
  ) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      paths.add(request.url.path);
      expect(request.headers['x-emergency-access-id'], '17');

      if (request.url.path != '/api/v1/patients/7') {
        throw StateError('Clinical request before patient authorization');
      }

      return http.Response('{"error":"Forbidden"}', 403);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final emergency = _s06Context()..activate(_s06Grant());
    addTearDown(emergency.dispose);

    await tester.pumpWidget(_emergencyScreen(_api(client), guard, emergency));
    await tester.pumpAndSettle();

    expect(paths, <String>['/api/v1/patients/7']);
    expect(emergency.activeEmergencyAccessId, isNull);
    expect(
      find.byKey(const ValueKey('patient-detail-name'), skipOffstage: false),
      findsNothing,
    );
    expect(
      find.byKey(
        const ValueKey('patient-prescriptions-card'),
        skipOffstage: false,
      ),
      findsNothing,
    );
    expect(
      find.byKey(
        const ValueKey('patient-clinical-alerts-card'),
        skipOffstage: false,
      ),
      findsNothing,
    );
  });

  testWidgets(
    'prescription 403 does not invent clearance or hide valid alerts',
    (tester) async {
      final paths = <String>[];

      final client = MockClient((request) async {
        expect(request.method, 'GET');
        paths.add(request.url.path);

        if (request.url.path == '/api/v1/prescriptions/patients/7') {
          return http.Response('{"error":"Forbidden"}', 403);
        }

        return _fixture(request);
      });
      addTearDown(client.close);

      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      await tester.pumpWidget(_screen(_api(client), guard));
      await tester.pumpAndSettle();

      await _show(tester, 'patient-prescriptions-card');
      expect(
        find.byKey(const ValueKey('patient-prescriptions-error')),
        findsOneWidget,
      );
      expect(find.text('Dosage: 500 mg'), findsNothing);

      await _show(tester, 'patient-clinical-alerts-card');
      expect(find.text('Server-reported clinical risk.'), findsOneWidget);
      expect(find.text('Severity: Critical'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('patient-alerts-no-clearance-warning')),
        findsOneWidget,
      );

      expect(paths, contains('/api/v1/prescriptions/patients/7'));
      expect(paths, contains('/api/v1/clinical-safety/alerts'));
    },
  );

  testWidgets('wrong-patient alert fails closed without hiding prescriptions', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.method, 'GET');

      if (request.url.path == '/api/v1/clinical-safety/alerts') {
        final wrongPatient = _alert()..['patient_id'] = 8;

        return http.Response(
          jsonEncode({
            'items': [wrongPatient],
            'total': 1,
            'page': 1,
            'per_page': 20,
          }),
          200,
        );
      }

      return _fixture(request);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_screen(_api(client), guard));
    await tester.pumpAndSettle();

    await _show(tester, 'patient-prescriptions-card');
    expect(find.text('Dosage: 500 mg'), findsOneWidget);

    await _show(tester, 'patient-clinical-alerts-card');
    expect(find.byKey(const ValueKey('patient-alerts-error')), findsOneWidget);
    expect(find.text('Server-reported clinical risk.'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-alerts-no-clearance-warning')),
      findsOneWidget,
    );
  });

  testWidgets('unavailable patient blocks both clinical endpoints entirely', (
    tester,
  ) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      paths.add(request.url.path);

      if (request.url.path != '/api/v1/patients/7') {
        throw StateError('Clinical GET before patient verification');
      }

      return http.Response('{"error":"Unavailable"}', 503);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_screen(_api(client), guard));
    await tester.pumpAndSettle();

    expect(paths, <String>['/api/v1/patients/7']);
    expect(
      find.byKey(const ValueKey('patient-detail-name'), skipOffstage: false),
      findsNothing,
    );
    expect(
      find.byKey(
        const ValueKey('patient-prescriptions-card'),
        skipOffstage: false,
      ),
      findsNothing,
    );
    expect(
      find.byKey(
        const ValueKey('patient-clinical-alerts-card'),
        skipOffstage: false,
      ),
      findsNothing,
    );
  });

  testWidgets(
    'late alert refresh after session invalidation cannot restore PHI',
    (tester) async {
      var alertGets = 0;
      var holdRefreshResponse = false;
      http.Request? pendingRequest;
      final pending = Completer<http.Response>();

      final client = MockClient((request) {
        expect(request.method, 'GET');

        if (request.url.path == '/api/v1/clinical-safety/alerts') {
          alertGets++;

          if (holdRefreshResponse) {
            holdRefreshResponse = false;
            pendingRequest = request;
            return pending.future;
          }
        }

        return Future<http.Response>.value(_fixture(request));
      });
      addTearDown(client.close);

      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);

      await tester.pumpWidget(_screen(_api(client), guard));
      await tester.pumpAndSettle();

      await _show(tester, 'patient-clinical-alerts-card');
      expect(find.text('Server-reported clinical risk.'), findsOneWidget);

      final initialAlertGets = alertGets;
      holdRefreshResponse = true;

      await tester.tap(find.byKey(const ValueKey('patient-alerts-refresh')));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 10));

      expect(alertGets, initialAlertGets + 1);
      expect(pendingRequest, isNotNull);

      guard.invalidate();
      await tester.pump();

      pending.complete(_fixture(pendingRequest!));
      await tester.pumpAndSettle();

      expect(
        find.text('Server-reported clinical risk.', skipOffstage: false),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('patient-alerts-error'), skipOffstage: false),
        findsOneWidget,
      );
    },
  );
}
