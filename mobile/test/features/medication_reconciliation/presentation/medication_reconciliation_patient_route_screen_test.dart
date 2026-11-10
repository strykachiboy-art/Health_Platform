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
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

AuthSessionState _ready(String role) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'p23g-integration-token',
    refreshToken: 'p23g-integration-refresh',
    userId: 44,
    role: role,
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _admission({int patientId = 5}) => <String, dynamic>{
  'id': 7,
  'patient_id': patientId,
  'bed_id': 9,
  'admitted_by_id': 4,
  'status': 'admitted',
};

Map<String, dynamic> _record({int patientId = 5, int clinicId = 3}) =>
    <String, dynamic>{
      'id': 11,
      'clinic_id': clinicId,
      'patient_id': patientId,
      'admission_id': 7,
      'ward_transfer_id': null,
      'event_type': 'admission',
      'event_identity': 'admission:7',
      'created_by_staff_id': 4,
      'no_medications_reported': true,
      'notes': null,
      'items': <Object>[],
      'record_status': 'draft',
      'record_version': 1,
      'finalized_by_staff_id': null,
      'finalized_at': null,
      'latest_amendment_id': null,
      'created_at': '2026-11-10T12:00:00+00:00',
      'updated_at': '2026-11-10T12:00:00+00:00',
    };

Map<String, dynamic> _page({Map<String, dynamic>? record}) => <String, dynamic>{
  'items': <Object>[record ?? _record()],
  'total': 1,
  'page': 1,
  'per_page': 50,
};

ApiClient _api(http.Client client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

void main() {
  testWidgets('invalid medication reconciliation deep link makes no request', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final api = _api(client);
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('Login')),
      sessionNavigationGuard: guard,
      patientAdmissionDataSource: PatientAdmissionRemoteDataSource(
        apiClient: api,
        loadSession: () async => _ready('doctor'),
        sessionNavigationGuard: guard,
      ),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/patients/not-a-patient/medication-reconciliation');
    await tester.pumpAndSettle();
    expect(
      find.text('Medication reconciliation navigation unavailable.'),
      findsOneWidget,
    );
    expect(calls, 0);
  });

  for (final role in <String>['doctor', 'nurse']) {
    testWidgets(
      '$role loads authoritative admission and scoped reconciliation',
      (tester) async {
        tester.view.devicePixelRatio = 1;
        tester.view.physicalSize = const Size(1100, 1900);
        addTearDown(tester.view.resetDevicePixelRatio);
        addTearDown(tester.view.resetPhysicalSize);
        final paths = <String>[];
        final client = MockClient((request) async {
          paths.add(request.url.path);
          expect(request.method, 'GET');
          expect(
            request.headers['authorization'],
            'Bearer p23g-integration-token',
          );
          if (request.url.path.endsWith('/current-admission')) {
            return http.Response(jsonEncode(_admission()), 200);
          }
          if (request.url.path ==
              '/api/v1/medication-reconciliations/admissions/7') {
            expect(request.url.queryParameters, <String, String>{
              'page': '1',
              'per_page': '50',
            });
            return http.Response(jsonEncode(_page()), 200);
          }
          return http.Response('{}', 404);
        });
        addTearDown(client.close);
        final guard = SessionNavigationGuard()..markSessionAvailable();
        addTearDown(guard.dispose);
        final api = _api(client);
        final router = createAppRouter(
          healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
          loginScreen: const Scaffold(body: Text('Login')),
          sessionNavigationGuard: guard,
          patientAdmissionDataSource: PatientAdmissionRemoteDataSource(
            apiClient: api,
            loadSession: () async => _ready(role),
            sessionNavigationGuard: guard,
          ),
        );
        addTearDown(router.dispose);
        await tester.pumpWidget(MaterialApp.router(routerConfig: router));
        router.go('/patients/5/medication-reconciliation');
        await tester.pumpAndSettle();
        expect(
          find.byKey(const ValueKey('p23g-route-workspace')),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('p23g-route-record-11')),
          findsOneWidget,
        );
        if (role == 'doctor') {
          expect(
            find.byKey(const ValueKey('p23g-route-edit-11')),
            findsOneWidget,
          );
          expect(
            find.byKey(const ValueKey('p23g-route-finalize-11')),
            findsOneWidget,
          );
        } else {
          expect(
            find.byKey(const ValueKey('p23g-route-edit-11')),
            findsNothing,
          );
          expect(
            find.byKey(const ValueKey('p23g-route-finalize-11')),
            findsNothing,
          );
        }
        expect(paths, <String>[
          '/api/v1/wards/patients/5/current-admission',
          '/api/v1/medication-reconciliations/admissions/7',
        ]);
        expect(tester.takeException(), isNull);
      },
    );
  }

  for (final action in <String>['review', 'edit', 'finalize']) {
    testWidgets('routed $action opens the existing clinical screen', (
      tester,
    ) async {
      tester.view.devicePixelRatio = 1;
      tester.view.physicalSize = const Size(1100, 1900);
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.view.resetPhysicalSize);
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        if (request.url.path.endsWith('/current-admission')) {
          return http.Response(jsonEncode(_admission()), 200);
        }
        if (request.url.path ==
            '/api/v1/medication-reconciliations/admissions/7') {
          return http.Response(jsonEncode(_page()), 200);
        }
        if (request.url.path == '/api/v1/medication-reconciliations/11') {
          return http.Response(
            jsonEncode(<String, dynamic>{'reconciliation': _record()}),
            200,
          );
        }
        return http.Response('{}', 404);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final api = _api(client);
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Text('Login')),
        sessionNavigationGuard: guard,
        patientAdmissionDataSource: PatientAdmissionRemoteDataSource(
          apiClient: api,
          loadSession: () async => _ready('doctor'),
          sessionNavigationGuard: guard,
        ),
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/patients/5/medication-reconciliation');
      await tester.pumpAndSettle();
      final key = switch (action) {
        'review' => 'p23g-route-review',
        'edit' => 'p23g-route-edit-11',
        _ => 'p23g-route-finalize-11',
      };
      final target = find.byKey(ValueKey(key));
      expect(target, findsOneWidget);
      await tester.ensureVisible(target);
      await tester.pumpAndSettle();
      await tester.tap(target);
      await tester.pumpAndSettle();
      final heading = switch (action) {
        'review' => 'Medication reconciliation review',
        'edit' => 'Medication reconciliation draft',
        _ => 'Medication reconciliation finalization',
      };
      expect(find.text(heading), findsWidgets);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
    'wrong-patient medication response is denied without clinical rows',
    (tester) async {
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/current-admission')) {
          return http.Response(jsonEncode(_admission()), 200);
        }
        return http.Response(
          jsonEncode(_page(record: _record(patientId: 9))),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final api = _api(client);
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Text('Login')),
        sessionNavigationGuard: guard,
        patientAdmissionDataSource: PatientAdmissionRemoteDataSource(
          apiClient: api,
          loadSession: () async => _ready('doctor'),
          sessionNavigationGuard: guard,
        ),
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/patients/5/medication-reconciliation');
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p23g-route-unavailable')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('p23g-route-record-11')), findsNothing);
    },
  );

  testWidgets('unauthorized admission response blocks medication requests', (
    tester,
  ) async {
    final paths = <String>[];
    final client = MockClient((request) async {
      paths.add(request.url.path);
      return http.Response('{"error":"Forbidden"}', 403);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final api = _api(client);
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('Login')),
      sessionNavigationGuard: guard,
      patientAdmissionDataSource: PatientAdmissionRemoteDataSource(
        apiClient: api,
        loadSession: () async => _ready('doctor'),
        sessionNavigationGuard: guard,
      ),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/patients/5/medication-reconciliation');
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('p23g-route-unavailable')),
      findsOneWidget,
    );
    expect(paths, <String>['/api/v1/wards/patients/5/current-admission']);
  });

  testWidgets('signed-out deep link redirects without clinical request', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSignedOut();
    addTearDown(guard.dispose);
    final api = _api(client);
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('Login')),
      sessionNavigationGuard: guard,
      patientAdmissionDataSource: PatientAdmissionRemoteDataSource(
        apiClient: api,
        loadSession: () async => _ready('doctor'),
        sessionNavigationGuard: guard,
      ),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/patients/5/medication-reconciliation');
    await tester.pumpAndSettle();
    expect(find.text('Login'), findsOneWidget);
    expect(calls, 0);
  });
}
