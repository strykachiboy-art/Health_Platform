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
import 'package:clinic_system_pro/features/hie/data/hie_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';

AuthSessionState _ready(String role, {int? clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'router-token',
        refreshToken: 'router-refresh',
        userId: 12,
        role: role,
        clinicContextId: clinic,
      ),
    );

void main() {
  test('homeDestinationsFor includes hie only for HIE_VIEW_ROLES with clinic', () {
    List<String> ids(String role, {int? clinic}) => homeDestinationsFor(
      role: role,
      clinicContextId: clinic,
    ).map((d) => d.id).toList();

    for (final role in ['admin', 'doctor', 'nurse', 'lab_technician', 'pharmacist']) {
      expect(ids(role, clinic: 3), contains('hie'));
      expect(ids(role), isNot(contains('hie')));
    }
    for (final role in ['patient', 'receptionist', 'accountant', 'super_admin']) {
      expect(ids(role, clinic: 3), isNot(contains('hie')));
    }
  });

  testWidgets('/hie and /hie/patient/:id require active session guard', (
    tester,
  ) async {
    final client = MockClient((req) async {
      throw StateError('No HTTP expected: ${req.url}');
    });
    addTearDown(client.close);
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/hie', '/home'],
    );
    addTearDown(guard.dispose);
    final hieDataSource = HieRemoteDataSource(
      apiClient: api,
      loadSession: () async => _ready('admin'),
      sessionNavigationGuard: guard,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Center(child: Text('Login sentinel'))),
      loadHomeSession: () async => _ready('admin'),
      hieDataSource: hieDataSource,
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);

    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/hie');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);

    guard.markSessionAvailable();
    router.go('/hie');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('hie-fhir-route')), findsOneWidget);

    router.go('/hie/patient/25');
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('hie-fhir-patient-route-25')),
      findsOneWidget,
    );

    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
  });
}
