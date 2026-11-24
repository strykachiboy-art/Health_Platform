import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:clinic_system_pro/features/hie/presentation/hie_workspace_screen.dart';

const roles = <String>[
  'admin',
  'doctor',
  'nurse',
  'pharmacist',
  'lab_technician',
  'super_admin',
  'patient',
  'receptionist',
  'accountant',
  'paramedic',
  'emt',
  'ambulance_dispatcher',
  'ambulance_coordinator',
  'driver',
  'other',
];
const permitted = <String>{
  'admin',
  'doctor',
  'nurse',
  'pharmacist',
  'lab_technician',
};

AuthSessionState session(String role, int? clinic) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'test-access',
    refreshToken: 'test-refresh',
    userId: 8,
    role: role,
    clinicContextId: clinic,
  ),
);

void main() {
  test('HIE navigation matches the exact five roles and requires clinic', () {
    for (final role in [...roles, 'unknown']) {
      for (final clinic in <int?>[null, 0, 4]) {
        final links = homeDestinationsFor(
          role: role,
          clinicContextId: clinic,
        ).where((d) => d.path == '/hie').toList();
        expect(
          links.length,
          clinic == 4 && permitted.contains(role) ? 1 : 0,
          reason: '$role / $clinic',
        );
      }
    }
  });
  testWidgets('direct HIE route requires an authenticated session', (
    tester,
  ) async {
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    final client = MockClient(
      (request) async => throw StateError('Unexpected HTTP'),
    );
    addTearDown(client.close);
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('Login sentinel')),
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/hie');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(find.byType(HieWorkspaceScreen), findsNothing);
  });
  for (final role in ['admin', 'doctor', 'super_admin', 'patient']) {
    testWidgets('HIE direct route role $role has correct staged actions', (
      tester,
    ) async {
      tester.view.physicalSize = const Size(1200, 1100);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
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
      Future<AuthSessionState> load() async => session(role, 4);
      final dashboard = DashboardRemoteDataSource(
        apiClient: api,
        loadSession: load,
        sessionNavigationGuard: guard,
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Text('Login sentinel')),
        dashboardDataSource: dashboard,
        sessionNavigationGuard: guard,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/hie');
      await tester.pumpAndSettle();
      expect(find.byType(HieWorkspaceScreen), findsOneWidget);
      expect(
        find.text('Create pending'),
        role == 'admin' ? findsOneWidget : findsNothing,
      );
      if (!permitted.contains(role)) {
        expect(
          find.text('HIE is unavailable for this session.'),
          findsOneWidget,
        );
      }
      expect(calls, 0);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.byType(HieWorkspaceScreen), findsNothing);
      expect(find.text('Login sentinel'), findsOneWidget);
    });
  }
}
