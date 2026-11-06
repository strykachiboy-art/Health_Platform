import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';

AuthSessionState _ready(String role, {int? clinic}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'test-token',
    refreshToken: 'test-refresh',
    userId: 41,
    role: role,
    clinicContextId: clinic,
  ),
);

void main() {
  test('P22.S02 role destinations match inspected backend allowlists', () {
    List<String> ids(String role, {int? clinic}) => homeDestinationsFor(
      role: role,
      clinicContextId: clinic,
    ).map((destination) => destination.id).toList();

    expect(ids('PATIENT'), ['health']);
    expect(ids('accountant'), ['health']);
    expect(ids('driver'), ['health']);
    expect(ids('doctor'), [
      'clinical_safety',
      'patients',
      'health',
      'emergency_request',
    ]);
    expect(ids('RECEPTIONIST'), ['patients', 'health']);
    expect(ids('ADMIN'), ['clinical_safety', 'patients', 'health']);
    expect(ids('SUPER_ADMIN'), ['health']);
    expect(ids('SUPER_ADMIN', clinic: 3), ['clinical_safety', 'health']);
    expect(ids('not_a_role'), ['health']);
  });

  testWidgets('P22.S01 /home route redirects before auth and on invalidation', (
    tester,
  ) async {
    final client = MockClient((request) async {
      throw StateError('No HTTP expected: ${request.url}');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/home'],
    );
    addTearDown(guard.dispose);
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Center(child: Text('Login sentinel'))),
      sessionNavigationGuard: guard,
      loadHomeSession: () async => _ready('doctor'),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/home');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-home-shell')), findsNothing);
    guard.markSessionAvailable();
    router.go('/home');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p22-home-shell')), findsOneWidget);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-home-shell')), findsNothing);
  });

  testWidgets('P22.S01 patient home shows no staff routes or fake KPIs', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: () async => _ready('patient'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Patient workspace'), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-home-health')), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-home-patients')), findsNothing);
    expect(find.byKey(const ValueKey('p22-home-clinical_safety')), findsNothing);
    expect(find.textContaining('does not invent counts'), findsOneWidget);
  });

  testWidgets('P22.S01 admin home advertises only supported routes', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: () async => _ready('admin', clinic: 7),
          sessionNavigationGuard: guard,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Clinic management'), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-home-clinical_safety')), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-home-patients')), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-home-emergency_request')), findsNothing);
  });

  testWidgets('P22.S01 guard invalidation removes role-specific navigation', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: () async => _ready('doctor', clinic: 3),
          sessionNavigationGuard: guard,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p22-home-patients')), findsOneWidget);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p22-home-patients')), findsNothing);
    expect(find.byKey(const ValueKey('p22-home-session-unavailable')), findsOneWidget);
  });

  testWidgets('P22.S01 stale async restore cannot re-open invalidated shell', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final pending = Completer<AuthSessionState>();
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: () => pending.future,
          sessionNavigationGuard: guard,
        ),
      ),
    );
    await tester.pump();
    expect(find.byKey(const ValueKey('p22-home-loading')), findsOneWidget);
    guard.invalidate();
    pending.complete(_ready('admin'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p22-home-clinical_safety')), findsNothing);
    expect(find.byKey(const ValueKey('p22-home-session-unavailable')), findsOneWidget);
  });

  testWidgets('P22.S01 super admin without clinic context is not shown alert entry', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: () async => _ready('super_admin'),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p22-home-clinic-required')), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-home-clinical_safety')), findsNothing);
  });

  testWidgets('P22.S01 failed secure-store restore exposes no role routes', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: () async => throw StateError('secret test error'),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p22-home-session-unavailable')), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-home-patients')), findsNothing);
    expect(find.textContaining('secret test error'), findsNothing);
  });
}
