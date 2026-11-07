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
import 'package:clinic_system_pro/features/patients/data/patient_allergy_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_detail_screen.dart';

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

Map<String, dynamic> _allergyPage() => {
  'success': true,
  'data': {
    'items': [
      {
        'id': 11,
        'clinic_id': 3,
        'patient_id': 7,
        'allergen_name': 'Penicillin',
        'allergen_code': null,
        'allergen_type': 'drug',
        'reaction': 'Anaphylaxis',
        'severity': 'life_threatening',
        'clinical_status': 'active',
        'verification_status': 'confirmed',
        'onset_at': null,
        'recorded_at': '2026-11-06T09:00:00+00:00',
        'recorded_by_id': 12,
        'notes': null,
        'created_at': '2026-11-06T09:00:00+00:00',
        'updated_at': '2026-11-06T09:00:00+00:00',
      },
    ],
    'total': 1,
    'page': 1,
    'per_page': 20,
  },
};

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
      loadAccessToken: () async => 'integration-token',
    );

PatientAllergyRemoteDataSource _allergySource(
  ApiClient api,
  SessionNavigationGuard guard,
) => PatientAllergyRemoteDataSource(
  apiClient: api,
  sessionNavigationGuard: guard,
  loadSession: () async => AuthSessionState.ready(
    const AuthTokenResponse(
      accessToken: 'integration-token',
      refreshToken: 'integration-refresh',
      userId: 42,
      clinicContextId: 3,
      role: 'doctor',
    ),
  ),
);

Widget _screen(ApiClient api, SessionNavigationGuard guard) => MaterialApp(
  home: PatientDetailScreen(
    dataSource: _patientSource(api),
    patientId: 7,
    allergyDataSource: _allergySource(api, guard),
  ),
);

Future<void> _showAllergyCard(WidgetTester tester) async {
  await tester.scrollUntilVisible(
    find.byKey(const ValueKey('patient-structured-allergies-card')),
    200,
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('identity and structured allergies remain distinct', (
    tester,
  ) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      paths.add(request.url.path);
      expect(request.method, 'GET');
      expect(request.headers['authorization'], 'Bearer integration-token');

      if (request.url.path == '/api/v1/patients/7') {
        return http.Response(
          jsonEncode({'success': true, 'data': _patient()}),
          200,
        );
      }

      if (request.url.path == '/api/v1/patients/7/allergies') {
        return http.Response(jsonEncode(_allergyPage()), 200);
      }

      throw StateError('Unexpected request');
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_screen(_api(client), guard));
    await tester.pumpAndSettle();
    expect(find.text('Ada Lovelace'), findsOneWidget);
    await _showAllergyCard(tester);
    expect(find.text('Penicillin'), findsOneWidget);
    expect(find.textContaining('Life-threatening'), findsOneWidget);
    expect(find.textContaining('Confirmed'), findsOneWidget);
    expect(paths, ['/api/v1/patients/7', '/api/v1/patients/7/allergies']);

    await tester.scrollUntilVisible(find.text('Peanuts'), -200);
    expect(find.text('Peanuts'), findsOneWidget);
    expect(find.text('Legacy allergy notes (not verified)'), findsOneWidget);
  });

  testWidgets('patient identity denial never loads allergies', (tester) async {
    var allergyRequests = 0;

    final client = MockClient((request) async {
      if (request.url.path.endsWith('/allergies')) {
        allergyRequests++;
        return http.Response(jsonEncode(_allergyPage()), 200);
      }
      return http.Response('{"error":"Forbidden"}', 403);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_screen(_api(client), guard));
    await tester.pumpAndSettle();

    expect(allergyRequests, 0);
    expect(find.text('Ada Lovelace'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-structured-allergies-card')),
      findsNothing,
    );
  });

  testWidgets('mismatched patient identity blocks allergy loading', (
    tester,
  ) async {
    var allergyRequests = 0;

    final client = MockClient((request) async {
      if (request.url.path.endsWith('/allergies')) {
        allergyRequests++;
        return http.Response(jsonEncode(_allergyPage()), 200);
      }
      return http.Response(
        jsonEncode({'success': true, 'data': _patient(id: 8)}),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_screen(_api(client), guard));
    await tester.pumpAndSettle();

    expect(allergyRequests, 0);
    expect(find.text('Ada Lovelace'), findsNothing);
    expect(find.textContaining('malformed patient data'), findsOneWidget);
  });

  testWidgets('allergy 403 cannot hide identity or show PHI', (tester) async {
    final client = MockClient((request) async {
      if (request.url.path == '/api/v1/patients/7') {
        return http.Response(
          jsonEncode({'success': true, 'data': _patient()}),
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
    expect(find.text('Ada Lovelace'), findsOneWidget);
    await _showAllergyCard(tester);
    expect(
      find.byKey(const ValueKey('patient-allergies-error')),
      findsOneWidget,
    );
    expect(find.text('Penicillin'), findsNothing);
  });

  testWidgets('failed patient refresh hides structured allergy panel', (
    tester,
  ) async {
    var patientGets = 0;

    final client = MockClient((request) async {
      if (request.url.path == '/api/v1/patients/7') {
        patientGets++;
        if (patientGets == 2) {
          return http.Response('{"error":"Unavailable"}', 503);
        }
        return http.Response(
          jsonEncode({'success': true, 'data': _patient()}),
          200,
        );
      }

      return http.Response(jsonEncode(_allergyPage()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_screen(_api(client), guard));
    await tester.pumpAndSettle();
    await _showAllergyCard(tester);

    expect(find.text('Penicillin'), findsOneWidget);

    await tester.tap(
      find.byKey(const ValueKey('patient-detail-refresh-button')),
    );
    await tester.pumpAndSettle();

    expect(patientGets, 2);

    // The stale notice is above the structured-allergy section.
    // Return to the top before asserting its visibility.
    await tester.drag(find.byType(ListView), const Offset(0, 3000));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-detail-stale-data-notice')),
      findsOneWidget,
    );
    expect(
      find.byKey(
        const ValueKey('patient-structured-allergies-card'),
        skipOffstage: false,
      ),
      findsNothing,
    );
    expect(find.text('Penicillin', skipOffstage: false), findsNothing);
  });

  testWidgets('router passes the allergy dependency to patient detail', (
    tester,
  ) async {
    final paths = <String>[];

    final client = MockClient((request) async {
      paths.add(request.url.path);
      if (request.url.path == '/api/v1/patients/7') {
        return http.Response(
          jsonEncode({'success': true, 'data': _patient()}),
          200,
        );
      }
      if (request.url.path == '/api/v1/patients/7/allergies') {
        return http.Response(jsonEncode(_allergyPage()), 200);
      }
      throw StateError('Unexpected route request');
    });
    addTearDown(client.close);

    final api = _api(client);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('Login')),
      patientDataSource: _patientSource(api),
      patientAllergyDataSource: _allergySource(api, guard),
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);

    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/patients/7');
    await tester.pumpAndSettle();
    await _showAllergyCard(tester);

    expect(paths, ['/api/v1/patients/7', '/api/v1/patients/7/allergies']);
    expect(find.text('Penicillin'), findsOneWidget);
  });
}
