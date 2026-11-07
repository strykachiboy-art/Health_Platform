import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_allergy_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_vitals_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_detail_screen.dart';

AuthSessionState _ready(String role) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's03d-token',
    refreshToken: 's03d-refresh',
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

Map<String, dynamic> _vitals() => {
  'id': 31,
  'patient_id': 7,
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

Map<String, dynamic> _admission() => {
  'id': 41,
  'patient_id': 7,
  'bed_id': 5,
  'admitted_by_id': 14,
  'reservation_id': null,
  'status': 'admitted',
  'reason': 'Clinical observation',
  'admitted_at': '2026-11-06T09:00:00+00:00',
  'discharged_at': null,
  'created_at': '2026-11-06T08:59:00+00:00',
  'updated_at': '2026-11-06T09:00:00+00:00',
};

Map<String, dynamic> _consultation() => {
  'id': 51,
  'clinic_id': 3,
  'patient_id': 7,
  'staff_id': 8,
  'appointment_id': null,
  'icd10_code': null,
  'consultation_type': 'general',
  'status': 'completed',
  'record_status': 'amended',
  'record_version': 2,
  'finalized_by_staff_id': 8,
  'finalized_at': '2026-11-06T10:00:00',
  'latest_amendment_id': 25,
  'chief_complaint': 'Fever',
  'symptoms': 'Fatigue',
  'diagnosis': 'Clinical finding',
  'treatment_plan': null,
  'notes': null,
  'voice_note_url': null,
  'transcribed_text': null,
  'template_id': null,
  'started_at': '2026-11-06T09:00:00',
  'ended_at': '2026-11-06T10:00:00',
  'created_at': '2026-11-06T09:00:00+00:00',
  'updated_at': '2026-11-06T10:00:00+00:00',
};

http.Response _fixture(http.Request request) {
  final path = request.url.path;

  if (path == '/api/v1/patients/7') {
    return http.Response(
      jsonEncode({'success': true, 'data': _patient()}),
      200,
    );
  }

  if (path == '/api/v1/patients/7/allergies') {
    return http.Response(
      jsonEncode({
        'success': true,
        'data': {'items': [], 'total': 0, 'page': 1, 'per_page': 20},
      }),
      200,
    );
  }

  if (path == '/api/v1/patients/7/vitals/latest') {
    return http.Response(jsonEncode({'success': true, 'data': _vitals()}), 200);
  }

  if (path == '/api/v1/patients/7/vitals') {
    return http.Response(
      jsonEncode({
        'success': true,
        'data': {
          'items': [_vitals()],
          'total': 1,
          'page': 1,
          'per_page': 20,
        },
      }),
      200,
    );
  }

  if (path == '/api/v1/wards/patients/7/current-admission') {
    return http.Response(jsonEncode(_admission()), 200);
  }

  if (path == '/api/v1/wards/patients/7/admissions') {
    return http.Response(
      jsonEncode({
        'items': [_admission()],
        'total': 1,
        'page': 1,
        'per_page': 20,
      }),
      200,
    );
  }

  if (path == '/api/v1/consultations/patient/7') {
    return http.Response(
      jsonEncode({
        'success': true,
        'data': [_consultation()],
        'pagination': {
          'total': 1,
          'page': 1,
          'per_page': 20,
          'pages': 1,
          'has_next': false,
          'has_prev': false,
          'next_page': null,
          'prev_page': null,
        },
      }),
      200,
    );
  }

  throw StateError('Unexpected clinical request: $path');
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
      loadAccessToken: () async => 's03d-token',
    );

PatientAllergyRemoteDataSource _allergies(
  ApiClient api,
  SessionNavigationGuard guard,
  String role,
) => PatientAllergyRemoteDataSource(
  apiClient: api,
  loadSession: () async => _ready(role),
  sessionNavigationGuard: guard,
);

PatientVitalsRemoteDataSource _vitalsSource(
  ApiClient api,
  SessionNavigationGuard guard,
  String role,
) => PatientVitalsRemoteDataSource(
  apiClient: api,
  loadSession: () async => _ready(role),
  sessionNavigationGuard: guard,
);

PatientAdmissionRemoteDataSource _admissionSource(
  ApiClient api,
  SessionNavigationGuard guard,
  String role,
) => PatientAdmissionRemoteDataSource(
  apiClient: api,
  loadSession: () async => _ready(role),
  sessionNavigationGuard: guard,
);

PatientConsultationRemoteDataSource _consultationSource(
  ApiClient api,
  SessionNavigationGuard guard,
  String role,
) => PatientConsultationRemoteDataSource(
  apiClient: api,
  loadSession: () async => _ready(role),
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
    allergyDataSource: _allergies(api, guard, role),
    vitalsDataSource: _vitalsSource(api, guard, role),
    admissionDataSource: _admissionSource(api, guard, role),
    consultationDataSource: _consultationSource(api, guard, role),
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

void main() {
  testWidgets('verified patient exposes all three clinical history panels', (
    tester,
  ) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.headers['authorization'], 'Bearer s03d-token');
      paths.add(request.url.path);
      return _fixture(request);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_screen(_api(client), guard));
    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace'), findsOneWidget);

    await _show(tester, 'patient-structured-allergies-card');
    expect(
      find.byKey(const ValueKey('patient-structured-allergies-card')),
      findsOneWidget,
    );

    await _show(tester, 'patient-vitals-card');
    expect(find.text('Temperature: 36.70 °C'), findsNWidgets(2));

    await _show(tester, 'patient-admissions-card');
    expect(find.text('Status: Admitted'), findsNWidgets(2));

    await _show(tester, 'patient-consultations-card');
    expect(find.text('Workflow: Completed'), findsOneWidget);
    expect(find.text('Clinical record: Amended'), findsOneWidget);
    expect(find.text('Record version: 2'), findsOneWidget);

    expect(paths.first, '/api/v1/patients/7');
    expect(
      paths.toSet(),
      containsAll(<String>{
        '/api/v1/patients/7/allergies',
        '/api/v1/patients/7/vitals/latest',
        '/api/v1/patients/7/vitals',
        '/api/v1/wards/patients/7/current-admission',
        '/api/v1/wards/patients/7/admissions',
        '/api/v1/consultations/patient/7',
      }),
    );
    expect(tester.takeException(), isNull);
  });

  for (final mismatch in <bool>[false, true]) {
    testWidgets(
      mismatch
          ? 'wrong patient ID blocks all clinical history requests'
          : 'patient 403 blocks all clinical history requests',
      (tester) async {
        final paths = <String>[];

        final client = MockClient((request) async {
          paths.add(request.url.path);
          if (request.url.path != '/api/v1/patients/7') {
            throw StateError('Clinical request before patient verification');
          }

          if (mismatch) {
            return http.Response(
              jsonEncode({'success': true, 'data': _patient(id: 8)}),
              200,
            );
          }
          return http.Response('{"error":"Forbidden"}', 403);
        });
        addTearDown(client.close);

        final guard = SessionNavigationGuard()..markSessionAvailable();
        addTearDown(guard.dispose);

        await tester.pumpWidget(_screen(_api(client), guard));
        await tester.pumpAndSettle();

        expect(paths, ['/api/v1/patients/7']);
        expect(find.text('Ada Lovelace'), findsNothing);
        expect(
          find.byKey(
            const ValueKey('patient-vitals-card'),
            skipOffstage: false,
          ),
          findsNothing,
        );
        expect(
          find.byKey(
            const ValueKey('patient-admissions-card'),
            skipOffstage: false,
          ),
          findsNothing,
        );
        expect(
          find.byKey(
            const ValueKey('patient-consultations-card'),
            skipOffstage: false,
          ),
          findsNothing,
        );
      },
    );
  }

  testWidgets('stale patient refresh removes all three clinical panels', (
    tester,
  ) async {
    var patientGets = 0;

    final client = MockClient((request) async {
      if (request.url.path == '/api/v1/patients/7') {
        patientGets++;
        if (patientGets == 2) {
          return http.Response('{"error":"Unavailable"}', 503);
        }
      }
      return _fixture(request);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_screen(_api(client), guard));
    await tester.pumpAndSettle();

    await _show(tester, 'patient-vitals-card');
    expect(find.text('Temperature: 36.70 °C'), findsNWidgets(2));

    await tester.tap(
      find.byKey(const ValueKey('patient-detail-refresh-button')),
    );
    await tester.pumpAndSettle();

    expect(patientGets, 2);

    await tester.drag(find.byType(ListView), const Offset(0, 3000));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-detail-stale-data-notice')),
      findsOneWidget,
    );

    for (final key in <String>[
      'patient-vitals-card',
      'patient-admissions-card',
      'patient-consultations-card',
      'patient-structured-allergies-card',
    ]) {
      expect(find.byKey(ValueKey(key), skipOffstage: false), findsNothing);
    }

    expect(
      find.text('Temperature: 36.70 °C', skipOffstage: false),
      findsNothing,
    );
  });

  testWidgets('unauthorized patient role cannot fetch clinical history', (
    tester,
  ) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      paths.add(request.url.path);
      return _fixture(request);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    // Patient details are mocked as allowed to isolate the role
    // gate in each of the three clinical-history data adapters.
    await tester.pumpWidget(_screen(_api(client), guard, role: 'patient'));
    await tester.pumpAndSettle();

    await _show(tester, 'patient-vitals-card');
    await _show(tester, 'patient-admissions-card');
    await _show(tester, 'patient-consultations-card');

    expect(paths, ['/api/v1/patients/7']);
    expect(
      find.byKey(const ValueKey('patient-consultations-error')),
      findsOneWidget,
    );
  });

  testWidgets('router injects history dependencies and logout removes PHI', (
    tester,
  ) async {
    final client = MockClient((request) async => _fixture(request));
    addTearDown(client.close);

    final api = _api(client);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Center(child: Text('Login'))),
      patientDataSource: _patientSource(api),
      patientAllergyDataSource: _allergies(api, guard, 'doctor'),
      patientVitalsDataSource: _vitalsSource(api, guard, 'doctor'),
      patientAdmissionDataSource: _admissionSource(api, guard, 'doctor'),
      patientConsultationDataSource: _consultationSource(api, guard, 'doctor'),
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);

    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/patients/7');
    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace'), findsOneWidget);

    await _show(tester, 'patient-consultations-card');
    expect(find.text('Clinical record: Amended'), findsOneWidget);

    guard.invalidate();
    await tester.pumpAndSettle();

    expect(find.text('Login'), findsOneWidget);
    expect(
      find.text('Clinical record: Amended', skipOffstage: false),
      findsNothing,
    );
    expect(find.text('Ada Lovelace', skipOffstage: false), findsNothing);
  });
}
