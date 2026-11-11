import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/auth/presentation/session_security_screen.dart';

AuthSessionState p24fReady({String role = 'patient', int userId = 41}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'fake-test-access',
        refreshToken: 'fake-test-refresh',
        userId: userId,
        role: role,
        clinicContextId: null,
      ),
    );

void main() {
  testWidgets('confirmation cancel never signs out or deletes queued work', (
    tester,
  ) async {
    var calls = 0;
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: SessionSecurityScreen(
          loadSession: () async => p24fReady(),
          sessionNavigationGuard: guard,
          deviceManagementConfigured: false,
          performSignOut: () async {
            calls++;
            return const AuthLogoutResult(
              remoteStatus: AuthLogoutRemoteStatus.uncertain,
            );
          },
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p24f-queue-ownership')), findsOneWidget);
    expect(find.byKey(const ValueKey('p24f-sign-out')), findsOneWidget);
    expect(find.byKey(const ValueKey('p24f-manage-devices')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('p24f-sign-out')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p24f-signout-warning')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p24f-cancel-signout')));
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(find.byKey(const ValueKey('p24f-sign-out')), findsOneWidget);
  });

  testWidgets(
    'confirmed intent invokes shared signout exactly once and clears UI',
    (tester) async {
      var calls = 0;
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: SessionSecurityScreen(
            loadSession: () async => p24fReady(),
            sessionNavigationGuard: guard,
            performSignOut: () async {
              calls++;
              guard.markSignedOut();
              return const AuthLogoutResult(
                remoteStatus: AuthLogoutRemoteStatus.uncertain,
              );
            },
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('p24f-sign-out')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('p24f-confirm-signout')));
      await tester.pumpAndSettle();
      expect(calls, 1);
      expect(find.byKey(const ValueKey('p24f-sign-out')), findsNothing);
      expect(find.byKey(const ValueKey('p24f-session-status')), findsOneWidget);
      expect(find.textContaining('fake-test-access'), findsNothing);
    },
  );

  testWidgets('super admin never gets device management shortcut', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: SessionSecurityScreen(
          loadSession: () async => p24fReady(role: 'super_admin'),
          sessionNavigationGuard: guard,
          deviceManagementConfigured: true,
          performSignOut: () async => const AuthLogoutResult(
            remoteStatus: AuthLogoutRemoteStatus.notAttempted,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p24f-sign-out')), findsOneWidget);
    expect(find.byKey(const ValueKey('p24f-manage-devices')), findsNothing);
  });

  testWidgets('stale async restore cannot reveal a security action', (
    tester,
  ) async {
    var calls = 0;
    final pending = Completer<AuthSessionState>();
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: SessionSecurityScreen(
          loadSession: () => pending.future,
          sessionNavigationGuard: guard,
          deviceManagementConfigured: true,
          performSignOut: () async {
            calls++;
            return const AuthLogoutResult(
              remoteStatus: AuthLogoutRemoteStatus.confirmed,
            );
          },
        ),
      ),
    );
    await tester.pump();
    expect(find.byKey(const ValueKey('p24f-loading')), findsOneWidget);
    guard.invalidate();
    pending.complete(p24fReady());
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p24f-sign-out')), findsNothing);
    expect(find.byKey(const ValueKey('p24f-manage-devices')), findsNothing);
    expect(calls, 0);
  });

  testWidgets('in-flight account replacement blocks signout before dialog', (
    tester,
  ) async {
    var currentUser = 41;
    var calls = 0;
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: SessionSecurityScreen(
          loadSession: () async => p24fReady(userId: currentUser),
          sessionNavigationGuard: guard,
          performSignOut: () async {
            calls++;
            return const AuthLogoutResult(
              remoteStatus: AuthLogoutRemoteStatus.confirmed,
            );
          },
        ),
      ),
    );
    await tester.pumpAndSettle();
    currentUser = 99;
    await tester.tap(find.byKey(const ValueKey('p24f-sign-out')));
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(find.byKey(const ValueKey('p24f-confirm-signout')), findsNothing);
    expect(find.byKey(const ValueKey('p24f-sign-out')), findsNothing);
  });
}
