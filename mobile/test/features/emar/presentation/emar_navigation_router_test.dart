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

AuthSessionState _nurse() => const AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'router-emar-token',
    refreshToken: 'router-emar-refresh',
    userId: 44,
    role: 'nurse',
    clinicContextId: 3,
  ),
);

void main() {
  testWidgets('invalid direct eMAR route cannot trigger a network lookup', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/patients', '/emar'],
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
      patientAdmissionDataSource: PatientAdmissionRemoteDataSource(
        apiClient: api,
        loadSession: () async => _nurse(),
        sessionNavigationGuard: guard,
      ),
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/emar/patient/not-an-id');
    await tester.pumpAndSettle();
    expect(find.text('eMAR navigation unavailable.'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets(
    'registered route loads scoped admission before medication list',
    (tester) async {
      tester.view.devicePixelRatio = 1;
      tester.view.physicalSize = const Size(1100, 1900);
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.view.resetPhysicalSize);
      final paths = <String>[];
      final client = MockClient((request) async {
        paths.add(request.url.path);
        expect(request.method, 'GET');
        if (request.url.path.endsWith('/current-admission')) {
          return http.Response(
            jsonEncode({
              'id': 7,
              'patient_id': 5,
              'bed_id': 9,
              'admitted_by_id': 4,
              'status': 'admitted',
            }),
            200,
          );
        }
        if (request.url.path == '/api/v1/emar/admissions/7') {
          return http.Response(
            jsonEncode({
              'items': <Object>[],
              'total': 0,
              'page': 1,
              'per_page': 20,
            }),
            200,
          );
        }
        return http.Response('{}', 404);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const <String>['/patients', '/emar'],
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
        patientAdmissionDataSource: PatientAdmissionRemoteDataSource(
          apiClient: api,
          loadSession: () async => _nurse(),
          sessionNavigationGuard: guard,
        ),
        sessionNavigationGuard: guard,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/emar/patient/5');
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23f-s02-workspace')), findsOneWidget);
      expect(paths, [
        '/api/v1/wards/patients/5/current-admission',
        '/api/v1/emar/admissions/7',
      ]);
    },
  );
}
