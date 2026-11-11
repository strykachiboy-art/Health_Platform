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

AuthSessionState p24cSession({int clinic = 3, String role = 'doctor'}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'p24c-token',
        refreshToken: 'p24c-refresh',
        userId: 41,
        clinicContextId: clinic,
        role: role,
      ),
    );

Map<String, dynamic> p24cNotification({int clinic = 3}) => {
  'id': 7,
  'clinic_id': clinic,
  'user_id': 41,
  'title': 'Authorized private alert',
  'message': 'Verified only by the backend',
  'notification_type': 'general',
  'priority': 'normal',
  'channel': 'in_app',
  'status': 'pending',
  'is_read': false,
  'created_at': '2026-11-11T08:00:00+00:00',
  'read_at': null,
  'reference_type': 'patient',
  'reference_id': 55,
};

ApiClient p24cApi(http.Client client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

void main() {
  testWidgets('signed-out deep link redirects before any HTTP request', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      throw StateError('Unexpected request');
    });
    addTearDown(client.close);
    final api = p24cApi(client);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    final reader = PatientAdmissionRemoteDataSource(
      apiClient: api,
      loadSession: () async => p24cSession(),
      sessionNavigationGuard: guard,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('P24C login')),
      sessionNavigationGuard: guard,
      patientAdmissionDataSource: reader,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/notifications/7');
    await tester.pumpAndSettle();
    expect(find.text('P24C login'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets(
    'direct link displays only freshly verified detail; invalidation clears it',
    (tester) async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/notifications/7');
        return http.Response(
          jsonEncode({'success': true, 'data': p24cNotification()}),
          200,
        );
      });
      addTearDown(client.close);
      final api = p24cApi(client);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final reader = PatientAdmissionRemoteDataSource(
        apiClient: api,
        loadSession: () async => p24cSession(),
        sessionNavigationGuard: guard,
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Text('P24C login')),
        sessionNavigationGuard: guard,
        patientAdmissionDataSource: reader,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/notifications/7');
      await tester.pumpAndSettle();
      expect(find.text('Authorized private alert'), findsOneWidget);
      expect(find.text('Verified only by the backend'), findsOneWidget);
      expect(requests, 1);
      // A notification's patient reference never automatically opens a chart.
      expect(find.text('Patient directory'), findsNothing);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('Authorized private alert'), findsNothing);
      expect(find.text('P24C login'), findsOneWidget);
    },
  );

  testWidgets('inbox selection uses the same protected deep-link destination', (
    tester,
  ) async {
    final paths = <String>[];
    final client = MockClient((request) async {
      paths.add(request.url.path);
      if (request.url.path == '/api/v1/notifications/') {
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'items': [p24cNotification()],
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
      }
      if (request.url.path == '/api/v1/notifications/7') {
        return http.Response(
          jsonEncode({'success': true, 'data': p24cNotification()}),
          200,
        );
      }
      throw StateError('Unexpected navigation request');
    });
    addTearDown(client.close);
    final api = p24cApi(client);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final reader = PatientAdmissionRemoteDataSource(
      apiClient: api,
      loadSession: () async => p24cSession(),
      sessionNavigationGuard: guard,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('P24C login')),
      sessionNavigationGuard: guard,
      patientAdmissionDataSource: reader,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/notifications');
    await tester.pumpAndSettle();
    expect(find.text('Authorized private alert'), findsOneWidget);
    await tester.tap(find.text('Authorized private alert'));
    await tester.pumpAndSettle();
    expect(find.text('Verified only by the backend'), findsOneWidget);
    expect(paths, <String>[
      '/api/v1/notifications/',
      '/api/v1/notifications/7',
    ]);
  });
}
