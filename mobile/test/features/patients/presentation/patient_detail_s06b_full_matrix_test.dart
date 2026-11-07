import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_clinical_alert_remote_data_source.dart';

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

Future<void> _show(WidgetTester tester, String key) async {
  await tester.scrollUntilVisible(
    find.byKey(ValueKey(key)),
    200,
    maxScrolls: 60,
  );
  await tester.pumpAndSettle();
}

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

Map<String, dynamic> _alert() => {
  'id': 21,
  'clinic_id': 3,
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

http.Response _fullFixture(http.Request request) {
  final path = request.url.path;
  final query = request.url.queryParameters;

  if (path == '/api/v1/prescriptions/patients/7') {
    if (query['page'] != '1' || query['per_page'] != '20') {
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
    if (query['patient_id'] != '7' ||
        query['page'] != '1' ||
        query['per_page'] != '20') {
      throw StateError('Unscoped alert query');
    }

    return http.Response(
      jsonEncode({
        'items': [_alert()],
        'total': 1,
        'page': 1,
        'per_page': 20,
      }),
      200,
    );
  }

  return _fixture(request);
}

PatientPrescriptionRemoteDataSource _prescriptions(
  ApiClient api,
  SessionNavigationGuard guard,
) => PatientPrescriptionRemoteDataSource(
  apiClient: api,
  loadSession: () async => _ready('doctor'),
  sessionNavigationGuard: guard,
);

PatientClinicalAlertRemoteDataSource _alerts(
  ApiClient api,
  SessionNavigationGuard guard,
) => PatientClinicalAlertRemoteDataSource(
  apiClient: api,
  loadSession: () async => _ready('doctor'),
  sessionNavigationGuard: guard,
);

Widget _fullScreen(
  ApiClient api,
  SessionNavigationGuard guard, {
  EmergencyAccessContext? emergency,
}) => MaterialApp(
  home: PatientDetailScreen(
    dataSource: _patientSource(api),
    patientId: 7,
    emergencyAccessContext: emergency,
    allergyDataSource: _allergies(api, guard, 'doctor'),
    vitalsDataSource: _vitalsSource(api, guard, 'doctor'),
    admissionDataSource: _admissionSource(api, guard, 'doctor'),
    consultationDataSource: _consultationSource(api, guard, 'doctor'),
    prescriptionDataSource: _prescriptions(api, guard),
    clinicalAlertDataSource: _alerts(api, guard),
  ),
);

EmergencyAccessGrant _grant() =>
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

Future<void> _visitSixPanels(WidgetTester tester) async {
  for (final key in <String>[
    'patient-structured-allergies-card',
    'patient-vitals-card',
    'patient-admissions-card',
    'patient-consultations-card',
    'patient-prescriptions-card',
    'patient-clinical-alerts-card',
  ]) {
    await _show(tester, key);
    expect(find.byKey(ValueKey(key)), findsOneWidget);
  }
}

void main() {
  testWidgets('verified patient loads all six clinical panels together', (
    tester,
  ) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.headers['authorization'], 'Bearer s03d-token');
      paths.add(request.url.path);
      return _fullFixture(request);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_fullScreen(_api(client), guard));
    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace'), findsOneWidget);

    // Visit top-to-bottom: Flutter's lazy list is not scrolled
    // backwards by the shared positive-delta _show helper.
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
    expect(find.text('Clinical record: Amended'), findsOneWidget);

    await _show(tester, 'patient-prescriptions-card');
    expect(find.text('Dosage: 500 mg'), findsOneWidget);

    await _show(tester, 'patient-clinical-alerts-card');
    expect(find.text('Severity: Critical'), findsOneWidget);
    expect(find.text('Action: Block'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('patient-alerts-no-clearance-warning')),
      findsOneWidget,
    );

    // All six panels were visited in forward scroll order above.
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
        '/api/v1/prescriptions/patients/7',
        '/api/v1/clinical-safety/alerts',
      }),
    );

    expect(tester.takeException(), isNull);
  });

  testWidgets('revoked emergency grant removes all six panels', (tester) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      expect(request.method, 'GET');
      paths.add(request.url.path);

      if (request.url.path == '/api/v1/patients/7') {
        expect(request.headers['x-emergency-access-id'], '17');
      }

      return _fullFixture(request);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final emergency = EmergencyAccessContext(
      nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
    )..activate(_grant());
    addTearDown(emergency.dispose);

    await tester.pumpWidget(
      _fullScreen(_api(client), guard, emergency: emergency),
    );
    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace'), findsOneWidget);

    await _visitSixPanels(tester);

    expect(paths, contains('/api/v1/clinical-safety/alerts'));
    expect(paths, contains('/api/v1/consultations/patient/7'));

    emergency.clear();
    await tester.pumpAndSettle();

    expect(find.text('Ada Lovelace', skipOffstage: false), findsNothing);

    for (final key in <String>[
      'patient-structured-allergies-card',
      'patient-vitals-card',
      'patient-admissions-card',
      'patient-consultations-card',
      'patient-prescriptions-card',
      'patient-clinical-alerts-card',
    ]) {
      expect(find.byKey(ValueKey(key), skipOffstage: false), findsNothing);
    }

    expect(
      find.text('Server-reported clinical risk.', skipOffstage: false),
      findsNothing,
    );
    expect(find.text('Dosage: 500 mg', skipOffstage: false), findsNothing);
    expect(find.textContaining('Emergency access changed'), findsOneWidget);
  });

  testWidgets('patient identity mismatch blocks every clinical GET', (
    tester,
  ) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      paths.add(request.url.path);
      expect(request.method, 'GET');

      if (request.url.path != '/api/v1/patients/7') {
        throw StateError('Clinical GET before patient verification');
      }

      return http.Response(
        jsonEncode({'success': true, 'data': _patient(id: 8)}),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_fullScreen(_api(client), guard));
    await tester.pumpAndSettle();

    expect(paths, <String>['/api/v1/patients/7']);
    expect(find.text('Ada Lovelace'), findsNothing);

    for (final key in <String>[
      'patient-structured-allergies-card',
      'patient-vitals-card',
      'patient-admissions-card',
      'patient-consultations-card',
      'patient-prescriptions-card',
      'patient-clinical-alerts-card',
    ]) {
      expect(find.byKey(ValueKey(key)), findsNothing);
    }
  });

  testWidgets('consultation failure does not mask prescription or alert', (
    tester,
  ) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      expect(request.method, 'GET');
      paths.add(request.url.path);

      if (request.url.path == '/api/v1/consultations/patient/7') {
        return http.Response('{"error":"Unavailable"}', 503);
      }

      return _fullFixture(request);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_fullScreen(_api(client), guard));
    await tester.pumpAndSettle();

    await _show(tester, 'patient-consultations-card');

    expect(
      find.byKey(const ValueKey('patient-consultations-error')),
      findsOneWidget,
    );
    expect(find.text('Clinical record: Amended'), findsNothing);

    await _show(tester, 'patient-prescriptions-card');
    expect(find.text('Dosage: 500 mg'), findsOneWidget);

    await _show(tester, 'patient-clinical-alerts-card');
    expect(find.text('Severity: Critical'), findsOneWidget);
    expect(find.text('Action: Block'), findsOneWidget);

    expect(paths, contains('/api/v1/consultations/patient/7'));
    expect(paths, contains('/api/v1/prescriptions/patients/7'));
    expect(paths, contains('/api/v1/clinical-safety/alerts'));
  });
}
