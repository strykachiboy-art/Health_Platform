import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';

void main() {
  testWidgets(
    'Surgery C direct links redirect before any HTTP without session',
    (tester) async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        throw StateError('Signed-out Surgery action may not reach HTTP');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard();
      addTearDown(guard.dispose);
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      );
      final dashboard = DashboardRemoteDataSource(
        apiClient: api,
        loadSession: () async => AuthSessionState.ready(
          const AuthTokenResponse(
            accessToken: 'test',
            refreshToken: 'refresh',
            userId: 10,
            role: 'doctor',
            clinicContextId: 3,
          ),
        ),
        sessionNavigationGuard: guard,
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(
          body: Center(child: Text('Login sentinel')),
        ),
        dashboardDataSource: dashboard,
        sessionNavigationGuard: guard,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/surgery/cases/8/actions');
      await tester.pumpAndSettle();
      expect(find.text('Login sentinel'), findsOneWidget);
      expect(requests, 0);
    },
  );
}
