import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';

void main() {
  test('home shows Surgery only to allowed staff with clinic context', () {
    List<String> ids(String role, int? clinic) => homeDestinationsFor(
      role: role,
      clinicContextId: clinic,
    ).map((d) => d.id).toList();

    for (final role in const ['super_admin', 'admin', 'doctor', 'nurse']) {
      expect(ids(role, 3), contains('surgery'));
      expect(ids(role, null), isNot(contains('surgery')));
    }
    for (final role in const [
      'patient',
      'receptionist',
      'accountant',
      'pharmacist',
      'lab_technician',
      'driver',
    ]) {
      expect(ids(role, 3), isNot(contains('surgery')));
    }
  });

  testWidgets('Surgery worklist and detail direct links require session', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      throw StateError('No network access permitted: ${request.url.path}');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard(); // default protects /patients only
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
        AuthTokenResponse(
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
      loginScreen: const Scaffold(body: Center(child: Text('Login sentinel'))),
      sessionNavigationGuard: guard,
      dashboardDataSource: dashboard,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));

    router.go('/surgery');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    router.go('/surgery/cases/8');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(requests, 0);
  });
}
