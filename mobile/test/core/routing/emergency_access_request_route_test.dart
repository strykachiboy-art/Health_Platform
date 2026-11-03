import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_remote_data_source.dart';

void main() {
  testWidgets(
    'emergency request route is session protected and invalidation clears sensitive form state',
    (tester) async {
      final client = MockClient((request) async {
        throw StateError(
          'No HTTP request expected in route test: ${request.url}',
        );
      });
      addTearDown(client.close);

      final apiClient = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      );

      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const <String>[
          '/patients',
          '/emergency-access',
        ],
      );

      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: apiClient),
        emergencyAccessDataSource: EmergencyAccessRemoteDataSource(
          apiClient: apiClient,
          loadAccessToken: () async => 'test-token',
        ),
        loginScreen: const Scaffold(
          body: Center(child: Text('Session sign-in route')),
        ),
        sessionNavigationGuard: guard,
      );

      addTearDown(router.dispose);
      addTearDown(guard.dispose);

      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      await tester.pumpAndSettle();

      router.go('/emergency-access/request');
      await tester.pumpAndSettle();

      expect(find.text('Session sign-in route'), findsOneWidget);
      expect(find.text('Request emergency access'), findsNothing);

      guard.markSessionAvailable();
      await tester.pumpAndSettle();

      router.go('/emergency-access/request');
      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('emergency-access-request-submit-button')),
        findsOneWidget,
      );

      const sensitiveReason = 'SENSITIVE_EMERGENCY_REASON_TEST_ONLY';

      await tester.enterText(
        find.byKey(const ValueKey('emergency-access-request-reason')),
        sensitiveReason,
      );

      expect(find.text(sensitiveReason), findsOneWidget);

      guard.invalidate();
      await tester.pumpAndSettle();

      expect(find.text('Session sign-in route'), findsOneWidget);
      expect(find.text('Request emergency access'), findsNothing);
      expect(find.text(sensitiveReason), findsNothing);
      expect(guard.requiresLocalRecovery, isTrue);
    },
  );
}
