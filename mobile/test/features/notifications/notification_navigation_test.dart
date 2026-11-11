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
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

AuthSessionState ready() => const AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'p24a-token',
    refreshToken: 'p24a-refresh',
    userId: 41,
    clinicContextId: 3,
    role: 'patient',
  ),
);

void main() {
  test(
    'Home entry needs clinic; all signed-in roles including super admin',
    () {
      bool entry(String role, int? clinic) => homeDestinationsFor(
        role: role,
        clinicContextId: clinic,
      ).any((x) => x.id == 'notifications');
      expect(entry('patient', 3), true);
      expect(entry('super_admin', 3), true);
      expect(entry('nurse', 3), true);
      expect(entry('super_admin', null), false);
      expect(entry('patient', null), false);
      expect(entry('not_a_role', 3), false);
    },
  );

  testWidgets('signed-out direct /notifications cannot make any HTTP request', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      throw StateError('Unexpected HTTP');
    });
    addTearDown(client.close);
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    final reader = PatientAdmissionRemoteDataSource(
      apiClient: api,
      loadSession: () async => ready(),
      sessionNavigationGuard: guard,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Center(child: Text('P24A sign in'))),
      sessionNavigationGuard: guard,
      patientAdmissionDataSource: reader,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/notifications');
    await tester.pumpAndSettle();
    expect(find.text('P24A sign in'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets(
    'authenticated route uses injected client; session invalidation hides PHI',
    (tester) async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'items': [
                {
                  'id': 11,
                  'clinic_id': 3,
                  'user_id': 41,
                  'title': 'Private notification',
                  'message': 'Private message',
                  'notification_type': 'general',
                  'priority': 'normal',
                  'channel': 'in_app',
                  'status': 'pending',
                  'is_read': false,
                  'read_at': null,
                  'created_at': '2026-11-11T08:00:00+00:00',
                  'reference_type': null,
                  'reference_id': null,
                },
              ],
              'page': 1,
              'per_page': 20,
              'total': 1,
              'pages': 1,
              'has_next': false,
              'has_prev': false,
            },
          }),
          200,
        );
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
      final reader = PatientAdmissionRemoteDataSource(
        apiClient: api,
        loadSession: () async => ready(),
        sessionNavigationGuard: guard,
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Center(child: Text('P24A sign in'))),
        sessionNavigationGuard: guard,
        patientAdmissionDataSource: reader,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/notifications');
      await tester.pumpAndSettle();
      expect(find.text('Private notification'), findsOneWidget);
      expect(requests, 1);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('Private notification'), findsNothing);
      expect(find.text('P24A sign in'), findsOneWidget);
    },
  );
}
