import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';

AuthSessionState _ready(String role, {int? clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'p22-s06-test-access',
        refreshToken: 'p22-s06-test-refresh',
        userId: 42,
        role: role,
        clinicContextId: clinicId,
      ),
    );

void main() {
  for (final width in <double>[390, 1100]) {
    testWidgets(
      'P22.S06 ${width.toInt()}px responsive shell exposes labeled controls',
      (tester) async {
        tester.view.physicalSize = Size(width, 900);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        final semantics = tester.ensureSemantics();
        try {
          final guard = SessionNavigationGuard()..markSessionAvailable();
          addTearDown(guard.dispose);

          await tester.pumpWidget(
            MaterialApp(
              builder: (context, child) => MediaQuery(
                data: MediaQuery.of(context)
                    .copyWith(textScaler: const TextScaler.linear(1.25)),
                child: child!,
              ),
              home: AuthenticatedHomeScreen(
                loadSession: () async => _ready('doctor'),
                sessionNavigationGuard: guard,
              ),
            ),
          );

          await tester.pumpAndSettle();

          expect(tester.takeException(), isNull);
          expect(find.byKey(const ValueKey('p22-home-shell')), findsOneWidget);
          expect(find.byTooltip('Profile and clinic'), findsOneWidget);
          expect(find.byTooltip('Session and sign out'), findsOneWidget);

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

          expect(
            find.byKey(const ValueKey('p22-home-patients')),
            findsOneWidget,
          );

          expect(
            find.byKey(const ValueKey('p22-home-server-authority')),
            findsOneWidget,
          );

          guard.invalidate();
          await tester.pumpAndSettle();

          expect(find.byKey(const ValueKey('p22-home-patients')), findsNothing);
          expect(
            find.byKey(const ValueKey('p22-home-clinical_safety')),
            findsNothing,
          );
          expect(
            find.byKey(const ValueKey('p22-home-session-unavailable')),
            findsOneWidget,
          );
          expect(tester.takeException(), isNull);
        } finally {
          semantics.dispose();
        }
      },
    );
  }

  testWidgets('P22.S06 patient shell never exposes staff navigation', (
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

    expect(find.byKey(const ValueKey('p22-home-patients')), findsNothing);
    expect(
      find.byKey(const ValueKey('p22-home-clinical_safety')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('p22-home-emergency_request')),
      findsNothing,
    );
    expect(find.byKey(const ValueKey('p22-home-health')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'P22.S06 super-admin requires clinic context for clinical entry',
    (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: AuthenticatedHomeScreen(
            loadSession: () async => _ready('super_admin', clinicId: null),
          ),
        ),
      );

      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('p22-home-clinic-required')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('p22-home-clinical_safety')),
        findsNothing,
      );
      expect(tester.takeException(), isNull);
    },
  );
}
