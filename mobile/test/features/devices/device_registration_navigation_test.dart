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
import 'package:clinic_system_pro/features/devices/data/device_registration_models.dart';
import 'package:clinic_system_pro/features/devices/data/device_token_provider.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

class ReadyTokenProvider implements DeviceTokenProvider {
  @override
  Future<ProviderDeviceToken?> currentToken() async => ProviderDeviceToken(
    deviceToken: 'verified-device-token-123456',
    platform: RegisteredDevicePlatform.android,
    deviceName: 'Test phone',
  );
}

class Connected implements DeviceConnectivitySource {
  @override
  Future<DeviceNetworkStatus> checkStatus() async =>
      DeviceNetworkStatus.connected;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

AuthSessionState ready() => const AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'route-access',
    refreshToken: 'route-refresh',
    userId: 41,
    role: 'patient',
    clinicContextId: null,
  ),
);

void main() {
  testWidgets('signed-out direct device route redirects before any HTTP', (
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
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    final reader = PatientAdmissionRemoteDataSource(
      apiClient: api,
      loadSession: () async => ready(),
      sessionNavigationGuard: guard,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('Sign in P24B')),
      sessionNavigationGuard: guard,
      patientAdmissionDataSource: reader,
      deviceTokenProvider: ReadyTokenProvider(),
      deviceRegistrationConnectivitySource: Connected(),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/devices/register');
    await tester.pumpAndSettle();
    expect(find.text('Sign in P24B'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets(
    'provider-injected route can register; invalidation hides receipt',
    (tester) async {
      var writes = 0;
      final client = MockClient((request) async {
        writes++;
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'id': 13,
              'user_id': 41,
              'device_token': body['device_token'],
              'platform': 'android',
              'device_name': 'Test phone',
              'is_active': true,
            },
          }),
          201,
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
        loginScreen: const Scaffold(body: Text('Sign in P24B')),
        sessionNavigationGuard: guard,
        patientAdmissionDataSource: reader,
        deviceTokenProvider: ReadyTokenProvider(),
        deviceRegistrationConnectivitySource: Connected(),
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/devices/register');
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p24b-register-device')),
        findsOneWidget,
      );
      await tester.tap(find.byKey(const ValueKey('p24b-register-device')));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p24b-registration-confirmed')),
        findsOneWidget,
      );
      expect(writes, 1);
      expect(find.textContaining('verified-device-token'), findsNothing);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p24b-registration-confirmed')),
        findsNothing,
      );
      expect(find.text('Sign in P24B'), findsOneWidget);
    },
  );

  testWidgets('no provider means no registration request', (tester) async {
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
      loadSession: () async => ready(),
      sessionNavigationGuard: guard,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('Sign in P24B')),
      sessionNavigationGuard: guard,
      patientAdmissionDataSource: reader,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/devices/register');
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Device registration unavailable'),
      findsOneWidget,
    );
    expect(requests, 0);
  });
}
