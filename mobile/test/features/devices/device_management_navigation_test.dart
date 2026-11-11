import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

class P24ENetwork implements DeviceConnectivitySource {
  @override
  Future<DeviceNetworkStatus> checkStatus() async =>
      DeviceNetworkStatus.connected;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

AuthSessionState p24eRouteSession({String role = 'patient'}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'p24e-route-access',
        refreshToken: 'p24e-route-refresh',
        userId: 41,
        role: role,
        clinicContextId: null,
      ),
    );

Map<String, dynamic> p24eRoutePage() => {
  'success': true,
  'data': {
    'items': [
      {
        'id': 7,
        'user_id': 41,
        'device_token': 'never-include-this-sensitive-device-identifier',
        'device_name': 'Test phone',
        'platform': 'android',
        'is_active': true,
        'created_at': '2026-11-09T10:10:10Z',
        'last_seen_at': null,
      },
    ],
    'page': 1,
    'per_page': 20,
    'total': 1,
    'pages': 1,
    'has_next': false,
    'has_prev': false,
  },
};

void main() {
  testWidgets('signed-out /devices never issues HTTP', (tester) async {
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
    final guard = SessionNavigationGuard()..markSignedOut();
    addTearDown(guard.dispose);
    final reader = PatientAdmissionRemoteDataSource(
      apiClient: api,
      loadSession: () async => p24eRouteSession(),
      sessionNavigationGuard: guard,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('Sign in P24E')),
      patientAdmissionDataSource: reader,
      sessionNavigationGuard: guard,
      deviceRegistrationConnectivitySource: P24ENetwork(),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/devices');
    await tester.pumpAndSettle();
    expect(find.text('Sign in P24E'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets('patient without clinic can read their devices but not token', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      expect(request.method, 'GET');
      expect(request.headers['Authorization'], 'Bearer p24e-route-access');
      return http.Response(jsonEncode(p24eRoutePage()), 200);
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
      loadSession: () async => p24eRouteSession(),
      sessionNavigationGuard: guard,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('Sign in P24E')),
      patientAdmissionDataSource: reader,
      sessionNavigationGuard: guard,
      deviceRegistrationConnectivitySource: P24ENetwork(),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/devices');
    await tester.pumpAndSettle();
    expect(find.text('My devices'), findsOneWidget);
    expect(find.text('Test phone'), findsOneWidget);
    expect(find.textContaining('never-include-this-sensitive'), findsNothing);
    expect(
      find.text('Current device unverified in this session.'),
      findsOneWidget,
    );
    expect(requests, 1);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Sign in P24E'), findsOneWidget);
  });

  testWidgets(
    'explicit deactivation is confirmed and never retries automatically',
    (tester) async {
      var writes = 0;
      var active = true;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          final page = p24eRoutePage();
          final data = page['data'] as Map<String, dynamic>;
          final items = data['items'] as List<dynamic>;
          (items.single as Map<String, dynamic>)['is_active'] = active;
          return http.Response(jsonEncode(page), 200);
        }
        writes++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/users/devices/7/deactivate');
        expect(jsonDecode(request.body), <String, dynamic>{});
        active = false;
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {'id': 7, 'user_id': 41, 'is_active': false},
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
        loadSession: () async => p24eRouteSession(),
        sessionNavigationGuard: guard,
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Text('Sign in P24E')),
        patientAdmissionDataSource: reader,
        sessionNavigationGuard: guard,
        deviceRegistrationConnectivitySource: P24ENetwork(),
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/devices');
      await tester.pumpAndSettle();
      final control = find.byKey(const ValueKey('p24e-deactivate-7'));
      await tester.ensureVisible(control);
      await tester.tap(control);
      await tester.pumpAndSettle();
      expect(
        find.textContaining('does not sign out or revoke sessions'),
        findsOneWidget,
      );
      expect(writes, 0);
      await tester.tap(find.byKey(const ValueKey('p24e-confirm-deactivate')));
      await tester.pumpAndSettle();
      expect(writes, 1);
      expect(find.text('Inactive'), findsOneWidget);
      expect(find.textContaining('never-include-this-sensitive'), findsNothing);
    },
  );

  testWidgets('super admin cannot read device list', (tester) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      return http.Response(jsonEncode(p24eRoutePage()), 200);
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
      loadSession: () async => p24eRouteSession(role: 'super_admin'),
      sessionNavigationGuard: guard,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('Sign in P24E')),
      patientAdmissionDataSource: reader,
      sessionNavigationGuard: guard,
      deviceRegistrationConnectivitySource: P24ENetwork(),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/devices');
    await tester.pumpAndSettle();
    expect(find.text('My devices'), findsOneWidget);
    expect(find.textContaining('session is unavailable'), findsOneWidget);
    expect(requests, 0);
  });
}
