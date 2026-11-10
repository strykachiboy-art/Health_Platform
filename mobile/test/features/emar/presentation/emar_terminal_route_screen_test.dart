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
import 'package:clinic_system_pro/features/emar/presentation/emar_patient_route_screen.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

AuthSessionState ready({String role = 'nurse'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'terminal-route-token',
    refreshToken: 'terminal-route-refresh',
    userId: 71,
    role: role,
    clinicContextId: 3,
  ),
);

Map<String, dynamic> admission() => {
  'id': 7,
  'patient_id': 5,
  'bed_id': 11,
  'admitted_by_id': 13,
  'status': 'admitted',
};

Map<String, dynamic> row() => {
  'id': 21,
  'clinic_id': 3,
  'patient_id': 5,
  'admission_id': 7,
  'prescription_id': 17,
  'prescription_item_id': 19,
  'status': 'due',
  'record_status': 'draft',
  'record_version': 1,
  'source_order_version': 2,
  'dose_value': '1.25',
  'dose_unit': 'mg',
  'route_code': 'oral',
  'scheduled_at': '2026-11-09T11:00:00Z',
};

Widget app(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'nurse',
}) {
  final source = PatientAdmissionRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: () async => ready(role: role),
    sessionNavigationGuard: guard,
  );
  return MaterialApp(
    home: EmarPatientRouteScreen(admissions: source, patientId: 5),
  );
}

void wide(WidgetTester tester) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(1100, 2100);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

void main() {
  testWidgets('nurse terminal route revalidates current admission and dose', (
    tester,
  ) async {
    wide(tester);
    var admissions = 0;
    var details = 0;
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method != 'GET') {
        writes++;
        return http.Response('{}', 500);
      }
      if (request.url.path.endsWith('/current-admission')) {
        admissions++;
        return http.Response(jsonEncode(admission()), 200);
      }
      if (request.url.path.endsWith('/admissions/7')) {
        return http.Response(
          jsonEncode({
            'items': [row()],
            'total': 1,
            'page': 1,
            'per_page': 20,
          }),
          200,
        );
      }
      if (request.url.path == '/api/v1/emar/21') {
        details++;
        return http.Response(jsonEncode({'administration': row()}), 200);
      }
      return http.Response('{}', 404);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23f-s02-row-21')));
    await tester.pumpAndSettle();
    final button = find.byKey(const ValueKey('p23f-s05-terminal-entry'));
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pumpAndSettle();
    expect(find.text('Medication outcome documentation'), findsOneWidget);
    expect(find.byKey(const ValueKey('p23f-s05-reason')), findsOneWidget);
    expect(writes, 0);
    expect(admissions, 2);
    expect(details, greaterThanOrEqualTo(2));
  });

  testWidgets('doctor sees history but cannot enter terminal mutation', (
    tester,
  ) async {
    wide(tester);
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method != 'GET') writes++;
      if (request.url.path.endsWith('/current-admission')) {
        return http.Response(jsonEncode(admission()), 200);
      }
      if (request.url.path.endsWith('/admissions/7')) {
        return http.Response(
          jsonEncode({
            'items': [row()],
            'total': 1,
            'page': 1,
            'per_page': 20,
          }),
          200,
        );
      }
      return http.Response(jsonEncode({'administration': row()}), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard, role: 'doctor'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23f-s02-row-21')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23f-s05-terminal-entry')), findsNothing);
    expect(find.byKey(const ValueKey('p23f-s03c-verify-entry')), findsNothing);
    expect(writes, 0);
  });
}
