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

AuthSessionState p24fSession() => const AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'test-access',
    refreshToken: 'test-refresh',
    userId: 41,
    role: 'patient',
    clinicContextId: null,
  ),
);

void main() {
  testWidgets('direct security link redirects signed-out users before HTTP', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
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
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Center(child: Text('P24F Login'))),
      loadHomeSession: () async => p24fSession(),
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/account/security');
    await tester.pumpAndSettle();
    expect(find.text('P24F Login'), findsOneWidget);
    expect(find.byKey(const ValueKey('p24f-security-screen')), findsNothing);
    expect(calls, 0);
  });

  testWidgets('missing trusted signout integration fails closed', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
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
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Center(child: Text('P24F Login'))),
      loadHomeSession: () async => p24fSession(),
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/account/security');
    await tester.pumpAndSettle();
    expect(find.text('Session security unavailable.'), findsOneWidget);
    expect(find.byKey(const ValueKey('p24f-sign-out')), findsNothing);
    expect(calls, 0);
  });
}
