import 'dart:async';
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

AuthSessionState _ready({String role = 'nurse', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'emar-route-token',
        refreshToken: 'emar-route-refresh',
        userId: 71,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> _admission({int patientId = 5, int id = 7}) => {
  'id': id,
  'patient_id': patientId,
  'bed_id': 11,
  'admitted_by_id': 13,
  'status': 'admitted',
};

Map<String, dynamic> _row({String status = 'due', int patientId = 5}) => {
  'id': 21,
  'clinic_id': 3,
  'patient_id': patientId,
  'admission_id': 7,
  'prescription_id': 17,
  'prescription_item_id': 19,
  'status': status,
  'record_status': 'draft',
  'record_version': 1,
  'dose_value': '1.25',
  'dose_unit': 'mg',
  'route_code': 'oral',
  'scheduled_at': '2026-11-09T11:00:00Z',
  'source_order_version': 1,
};

Map<String, dynamic> _list() => {
  'items': [_row()],
  'total': 1,
  'page': 1,
  'per_page': 20,
};

PatientAdmissionRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? load,
}) => PatientAdmissionRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: load ?? () async => _ready(),
  sessionNavigationGuard: guard,
);

Widget _app(PatientAdmissionRemoteDataSource source) =>
    MaterialApp(home: EmarPatientRouteScreen(admissions: source, patientId: 5));

void _wide(WidgetTester tester) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(1100, 2100);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

void main() {
  testWidgets('nurse route obtains current admission before eMAR schedule', (
    tester,
  ) async {
    _wide(tester);
    final paths = <String>[];
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      paths.add(request.url.path);
      expect(request.headers['authorization'], 'Bearer emar-route-token');
      if (request.url.path.endsWith('/current-admission')) {
        return http.Response(jsonEncode(_admission()), 200);
      }
      return http.Response(jsonEncode(_list()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(paths, [
      '/api/v1/wards/patients/5/current-admission',
      '/api/v1/emar/admissions/7',
    ]);
    expect(find.byKey(const ValueKey('p23f-s02-row-21')), findsOneWidget);
    expect(find.textContaining('Patient #5'), findsWidgets);
    expect(find.textContaining('Administer now'), findsNothing);
  });

  testWidgets('doctor sees read-only schedule, never verification entry', (
    tester,
  ) async {
    _wide(tester);
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      if (request.url.path.endsWith('/current-admission')) {
        return http.Response(jsonEncode(_admission()), 200);
      }
      if (request.url.path.endsWith('/admissions/7')) {
        return http.Response(jsonEncode(_list()), 200);
      }
      if (request.url.path == '/api/v1/emar/21') {
        return http.Response(jsonEncode({'administration': _row()}), 200);
      }
      return http.Response('{}', 404);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _app(_source(client, guard, load: () async => _ready(role: 'doctor'))),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23f-s02-row-21')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23f-s02-detail')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23f-s03c-verify-entry')), findsNothing);
  });

  testWidgets('patient role is rejected before any HTTP request', (
    tester,
  ) async {
    _wide(tester);
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _app(_source(client, guard, load: () async => _ready(role: 'patient'))),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('p23f-s03c-no-admission')),
      findsOneWidget,
    );
    expect(requests, 0);
  });

  testWidgets('no active admission never fabricates an eMAR schedule', (
    tester,
  ) async {
    _wide(tester);
    var reads = 0;
    final client = MockClient((request) async {
      reads++;
      expect(request.url.path, '/api/v1/wards/patients/5/current-admission');
      return http.Response(
        jsonEncode({'error': 'No active admission found'}),
        404,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(
      find.text('No active admission returned for this patient.'),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('p23f-s02-workspace')), findsNothing);
    expect(reads, 1);
  });

  testWidgets(
    'nurse enters separate verify UI from fresh pending detail, no POST',
    (tester) async {
      _wide(tester);
      var writes = 0;
      var reads = 0;
      final client = MockClient((request) async {
        if (request.method != 'GET') {
          writes++;
          return http.Response('{}', 500);
        }
        reads++;
        if (request.url.path.endsWith('/current-admission')) {
          return http.Response(jsonEncode(_admission()), 200);
        }
        if (request.url.path == '/api/v1/emar/admissions/7') {
          return http.Response(jsonEncode(_list()), 200);
        }
        if (request.url.path == '/api/v1/emar/21') {
          return http.Response(jsonEncode({'administration': _row()}), 200);
        }
        return http.Response('{}', 404);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(_app(_source(client, guard)));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('p23f-s02-row-21')));
      await tester.pumpAndSettle();
      final button = find.byKey(const ValueKey('p23f-s03c-verify-entry'));
      await tester.ensureVisible(button);
      await tester.tap(button);
      await tester.pumpAndSettle();
      expect(find.text('Medication barcode verification'), findsOneWidget);
      expect(find.textContaining('Medication NOT'), findsNothing);
      expect(
        find.byKey(const ValueKey('p23f-s03b-scan-patient')),
        findsOneWidget,
      );
      expect(writes, 0);
      expect(reads, greaterThanOrEqualTo(5));
    },
  );

  testWidgets('logout invalidates a pending admission response without PHI', (
    tester,
  ) async {
    _wide(tester);
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(_source(client, guard)));
    await tester.pump();
    guard.markSignedOut();
    pending.complete(http.Response(jsonEncode(_admission()), 200));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23f-s03c-unavailable')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23f-s02-workspace')), findsNothing);
  });
}
