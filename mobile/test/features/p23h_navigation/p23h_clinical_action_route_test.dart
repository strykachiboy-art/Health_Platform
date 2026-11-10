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
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';

void main() {
  testWidgets(
    'direct clinical mutation link cannot instantiate a grant or POST',
    (tester) async {
      final requests = <String>[];
      final client = MockClient((request) async {
        requests.add('${request.method} ${request.url.path}');
        return http.Response(jsonEncode({'success': false}), 403);
      });
      addTearDown(client.close);
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      );
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final reader = PatientPrescriptionRemoteDataSource(
        apiClient: api,
        loadSession: () async => const AuthSessionState.ready(
          AuthTokenResponse(
            accessToken: 'token',
            refreshToken: 'refresh',
            userId: 41,
            role: 'DOCTOR',
            clinicContextId: 3,
          ),
        ),
        sessionNavigationGuard: guard,
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Center(child: Text('Sign in'))),
        sessionNavigationGuard: guard,
        patientPrescriptionDataSource: reader,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/patients/7/clinical-action');
      await tester.pumpAndSettle();
      expect(find.text('Clinical action grant unavailable.'), findsOneWidget);
      expect(requests, isEmpty);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('Sign in'), findsOneWidget);
      expect(requests, isEmpty);
    },
  );

  testWidgets('missing source refuses clinical-action hub without reads', (
    tester,
  ) async {
    final client = MockClient((request) async {
      throw StateError('Unexpected request ${request.url}');
    });
    addTearDown(client.close);
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Center(child: Text('Sign in'))),
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/patients/7/clinical-actions');
    await tester.pumpAndSettle();
    expect(
      find.text('Clinical action navigation unavailable.'),
      findsOneWidget,
    );
  });
}
