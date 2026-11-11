import 'dart:async';
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

AuthSessionState _session({int? clinic = 3, String role = 'patient'}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'p24c-secret-access',
        refreshToken: 'p24c-secret-refresh',
        userId: 41,
        clinicContextId: clinic,
        role: role,
      ),
    );

Map<String, dynamic> _notification({int clinic = 3, int user = 41}) => {
  'id': 7,
  'clinic_id': clinic,
  'user_id': user,
  'title': 'Do not leak this notification',
  'message': 'Sensitive scoped data',
  'notification_type': 'general',
  'priority': 'normal',
  'channel': 'in_app',
  'status': 'pending',
  'is_read': false,
  'created_at': '2026-11-11T08:00:00+00:00',
  'read_at': null,
  'reference_type': 'patient',
  'reference_id': 555,
};

void main() {
  for (final role in <String>['super_admin', 'wrong_role']) {
    testWidgets('deep link does not bypass clinic and role for $role', (
      tester,
    ) async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return http.Response('Unreachable', 200);
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
        loadSession: () async =>
            _session(clinic: role == 'super_admin' ? null : 3, role: role),
        sessionNavigationGuard: guard,
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Text('P24C sign in')),
        sessionNavigationGuard: guard,
        patientAdmissionDataSource: reader,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/notifications/7');
      await tester.pumpAndSettle();
      expect(requests, 0);
      expect(find.text('Do not leak this notification'), findsNothing);
    });
  }

  for (final clinic in <int>[4, 3]) {
    testWidgets('backend owner or clinic mismatch fails closed: case $clinic', (
      tester,
    ) async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return http.Response(
          jsonEncode({
            'success': true,
            'data': _notification(clinic: clinic, user: clinic == 3 ? 42 : 41),
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
        loadSession: () async => _session(),
        sessionNavigationGuard: guard,
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Text('P24C sign in')),
        sessionNavigationGuard: guard,
        patientAdmissionDataSource: reader,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/notifications/7');
      await tester.pumpAndSettle();
      expect(requests, 1);
      expect(find.text('Do not leak this notification'), findsNothing);
      expect(find.text('Sensitive scoped data'), findsNothing);
      expect(
        find.textContaining('Notification data could not be verified.'),
        findsOneWidget,
      );
    });
  }

  testWidgets('link query parameter is rejected before HTTP dispatch', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      return http.Response('{}', 200);
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
      loadSession: () async => _session(),
      sessionNavigationGuard: guard,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('P24C sign in')),
      sessionNavigationGuard: guard,
      patientAdmissionDataSource: reader,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/notifications/7?access_token=do-not-accept');
    await tester.pumpAndSettle();
    expect(
      find.text('Invalid or unsupported notification link.'),
      findsOneWidget,
    );
    expect(requests, 0);
  });

  testWidgets('logout before delayed detail returns cannot expose PHI', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    var requests = 0;
    final client = MockClient((request) {
      requests++;
      return pending.future;
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
      loadSession: () async => _session(),
      sessionNavigationGuard: guard,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('P24C sign in')),
      sessionNavigationGuard: guard,
      patientAdmissionDataSource: reader,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/notifications/7');
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 200));
    expect(requests, 1);
    guard.invalidate();
    pending.complete(
      http.Response(
        jsonEncode({'success': true, 'data': _notification()}),
        200,
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('P24C sign in'), findsOneWidget);
    expect(find.text('Do not leak this notification'), findsNothing);
  });
}
