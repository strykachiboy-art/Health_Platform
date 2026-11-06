import 'dart:async';

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
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';

AuthSessionState _ready([String role = 'doctor']) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'token-for-tests-only',
    refreshToken: 'refresh-for-tests-only',
    userId: 19,
    role: role,
    clinicContextId: 3,
  ),
);

void main() {
  testWidgets('P22.RunA automatic landing on sign-in/restore, session controls stay reachable',
      (tester) async {
    final client = MockClient((request) async =>
        throw StateError('Unexpected HTTP: ${request.url}'));
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/home', '/patients'],
    );
    addTearDown(guard.dispose);
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
      ),
      loginScreen: const Scaffold(body: Text('Login sentinel')),
      sessionNavigationGuard: guard,
      loadHomeSession: () async => _ready(),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);

    // Existing login and restore flows call markSessionAvailable after the
    // coordinator confirms a session. The router must land on /home itself.
    guard.markSessionAvailable();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p22-home-shell')), findsOneWidget);
    expect(find.text('Login sentinel'), findsNothing);

    // Authenticated users must still reach the existing logout/recovery UI.
    router.go('/login?session=1');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);

    guard.markSignedOut();
    router.go('/home');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-home-shell')), findsNothing);
  });

  for (final width in <double>[390, 1100]) {
    testWidgets('P22.RunA home responds to ${width.toInt()}px viewport',
        (tester) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(MaterialApp(
        home: AuthenticatedHomeScreen(loadSession: () async => _ready()),
      ));
      await tester.pumpAndSettle();
      final scaffold = tester.widget<Scaffold>(
        find.byKey(const ValueKey('p22-home-shell')),
      );
      if (width >= 850) {
        expect(find.byType(NavigationRail), findsOneWidget);
        expect(scaffold.drawer, isNull);
      } else {
        expect(find.byType(NavigationRail), findsNothing);
        expect(scaffold.drawer, isNotNull);
      }
      expect(find.byKey(const ValueKey('p22-home-patients')), findsOneWidget);
    });
  }

  for (final width in <double>[390, 1100]) {
    testWidgets('P22.RunA feature shell ${width.toInt()}px navigation returns home',
        (tester) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      final client = MockClient((request) async =>
          throw StateError('Unexpected HTTP: ${request.url}'));
      addTearDown(client.close);
      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const <String>['/home', '/patients'],
      )..markSessionAvailable();
      addTearDown(guard.dispose);
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(
          apiClient: ApiClient(
            config: const BackendConfig(
              baseUrl: 'http://127.0.0.1:5000',
              allowInsecureHttp: true,
            ),
            client: client,
          ),
        ),
        loginScreen: const Scaffold(body: Text('Login sentinel')),
        sessionNavigationGuard: guard,
        loadHomeSession: () async => _ready(),
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      await tester.pumpAndSettle();
      router.go('/patients/register');
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p22-feature-shell')), findsOneWidget);
      final scaffold = tester.widget<Scaffold>(
        find.byKey(const ValueKey('p22-feature-shell')),
      );
      if (width >= 850) {
        expect(find.byType(NavigationRail), findsOneWidget);
        expect(scaffold.drawer, isNull);
        expect(find.byKey(const ValueKey('p22-feature-menu')), findsNothing);
      } else {
        expect(find.byType(NavigationRail), findsNothing);
        expect(scaffold.drawer, isNotNull);
        await tester.tap(find.byKey(const ValueKey('p22-feature-menu')));
        await tester.pumpAndSettle();
        expect(find.byKey(const ValueKey('p22-feature-drawer-home')), findsOneWidget);
        // Close the drawer before testing the persistent Home action.
        await tester.tapAt(const Offset(380, 500));
        await tester.pumpAndSettle();
      }

      await tester.tap(find.byKey(const ValueKey('p22-feature-home')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p22-home-shell')), findsOneWidget);
    });
  }

  testWidgets('P22.RunA in-flight feature restore cannot remount after sign-out',
      (tester) async {
    final client = MockClient((request) async =>
        throw StateError('Unexpected HTTP: ${request.url}'));
    addTearDown(client.close);
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/home', '/patients'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final pending = Completer<AuthSessionState>();
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
      ),
      loginScreen: const Scaffold(body: Text('Login sentinel')),
      sessionNavigationGuard: guard,
      loadHomeSession: () => pending.future,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/patients/register');
    await tester.pump();
    expect(find.byKey(const ValueKey('p22-feature-loading')), findsOneWidget);

    guard.invalidate();
    pending.complete(_ready());
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-feature-shell')), findsNothing);
  });
}
