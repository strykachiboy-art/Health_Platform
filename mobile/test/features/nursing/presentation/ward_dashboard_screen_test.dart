import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/nursing/data/ward_dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/nursing/presentation/ward_dashboard_screen.dart';

AuthSessionState ready() => AuthSessionState.ready(
  const AuthTokenResponse(
    accessToken: 'ward-test',
    refreshToken: 'ward-refresh',
    userId: 11,
    role: 'nurse',
    clinicContextId: 3,
  ),
);

Map<String, dynamic> ward({int id = 9, String name = 'General Ward'}) => {
  'id': id,
  'clinic_id': 3,
  'name': name,
  'ward_type': 'general',
  'capacity': 2,
};

Map<String, dynamic> wardList({List<Map<String, dynamic>>? items}) => {
  'items': items ?? [ward()],
  'total': (items ?? [ward()]).length,
  'page': 1,
  'per_page': 20,
};

Map<String, dynamic> bedList({int wardId = 9}) => {
  'items': [
    {'id': 1, 'ward_id': wardId, 'bed_number': 'A-1', 'status': 'available'},
    {'id': 2, 'ward_id': wardId, 'bed_number': 'A-2', 'status': 'reserved'},
  ],
  'total': 2,
  'page': 1,
  'per_page': 30,
};

Map<String, dynamic> occupancy({
  int wardId = 9,
  String name = 'General Ward',
}) => {
  'ward_id': wardId,
  'clinic_id': 3,
  'ward_name': name,
  'capacity': 2,
  'total_beds': 2,
  'occupied': 0,
  'available': 1,
  'reserved': 1,
  'maintenance': 0,
};

Widget app(MockClient client, SessionNavigationGuard guard) => MaterialApp(
  home: WardDashboardScreen(
    dataSource: WardDashboardRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
      loadSession: () async => ready(),
      sessionNavigationGuard: guard,
    ),
  ),
);

void wide(WidgetTester tester) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(1200, 2200);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

void main() {
  testWidgets(
    'ward dashboard renders backend occupancy and bed status, GET only',
    (tester) async {
      wide(tester);
      var gets = 0;
      var writes = 0;
      final client = MockClient((request) async {
        if (request.method != 'GET') {
          writes++;
          return http.Response('{}', 500);
        }
        gets++;
        if (request.url.path == '/api/v1/wards') {
          return http.Response(jsonEncode(wardList()), 200);
        }
        if (request.url.path.endsWith('/occupancy')) {
          return http.Response(jsonEncode(occupancy()), 200);
        }
        if (request.url.path.endsWith('/beds')) {
          return http.Response(jsonEncode(bedList()), 200);
        }
        return http.Response('{}', 404);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(app(client, guard));
      await tester.pumpAndSettle();
      expect(find.text('General Ward'), findsOneWidget);
      expect(find.text('Available: 1'), findsOneWidget);
      expect(find.text('Reserved: 1'), findsOneWidget);
      expect(find.text('Bed A-1'), findsOneWidget);
      expect(find.text('Bed A-2'), findsOneWidget);
      expect(gets, 3);
      expect(writes, 0);
    },
  );

  testWidgets('sign-out removes ward and bed data immediately', (tester) async {
    wide(tester);
    final client = MockClient((request) async {
      if (request.url.path == '/api/v1/wards') {
        return http.Response(jsonEncode(wardList()), 200);
      }
      if (request.url.path.endsWith('/occupancy')) {
        return http.Response(jsonEncode(occupancy()), 200);
      }
      return http.Response(jsonEncode(bedList()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await tester.pumpAndSettle();
    expect(find.text('Available: 1'), findsOneWidget);
    guard.markSignedOut();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23e-b-unavailable')), findsOneWidget);
    expect(find.text('Available: 1'), findsNothing);
    expect(find.text('Bed A-1'), findsNothing);
  });

  testWidgets('backend failure is not disguised as an empty ward directory', (
    tester,
  ) async {
    wide(tester);
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode({'success': false, 'error': 'private backend error'}),
        503,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23e-b-ward-error')), findsOneWidget);
    expect(find.textContaining('private backend error'), findsNothing);
    expect(find.text('No wards returned for this clinic.'), findsNothing);
  });

  testWidgets('stale result after invalidation cannot render', (tester) async {
    wide(tester);
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await tester.pump();
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(wardList()), 200));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23e-b-unavailable')), findsOneWidget);
    expect(find.text('General Ward'), findsNothing);
  });

  testWidgets(
    'registered /wards route reuses the patient-admission session client',
    (tester) async {
      wide(tester);
      final client = MockClient((request) async {
        if (request.url.path == '/api/v1/wards') {
          return http.Response(jsonEncode(wardList()), 200);
        }
        if (request.url.path.endsWith('/occupancy')) {
          return http.Response(jsonEncode(occupancy()), 200);
        }
        if (request.url.path.endsWith('/beds')) {
          return http.Response(jsonEncode(bedList()), 200);
        }
        return http.Response('{}', 404);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const <String>['/wards', '/patients'],
      )..markSessionAvailable();
      addTearDown(guard.dispose);
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Text('Login')),
        loadHomeSession: () async => ready(),
        patientAdmissionDataSource: PatientAdmissionRemoteDataSource(
          apiClient: api,
          loadSession: () async => ready(),
          sessionNavigationGuard: guard,
        ),
        sessionNavigationGuard: guard,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/wards');
      await tester.pumpAndSettle();
      expect(find.text('Ward dashboard'), findsOneWidget);
      expect(find.text('Available: 1'), findsOneWidget);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('Available: 1'), findsNothing);
    },
  );

  testWidgets(
    'empty ward directory is explicit, not an inferred clinical state',
    (tester) async {
      wide(tester);
      final client = MockClient(
        (_) async => http.Response(
          jsonEncode(wardList(items: <Map<String, dynamic>>[])),
          200,
        ),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(app(client, guard));
      await tester.pumpAndSettle();
      expect(find.text('No wards returned for this clinic.'), findsOneWidget);
    },
  );
}
