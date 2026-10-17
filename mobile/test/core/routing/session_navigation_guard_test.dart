import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';

void main() {
  testWidgets(
    'patient routes redirect until authenticated and invalidation removes mounted form state',
    (tester) async {
      final client = MockClient((request) async {
        throw StateError(
          'No network request is expected in this route test: ${request.url}',
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
      final guard = SessionNavigationGuard();
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: apiClient),
        patientDataSource: PatientRemoteDataSource(apiClient: apiClient),
        loginScreen: const Scaffold(
          body: Center(child: Text('Session sign-in route')),
        ),
        sessionNavigationGuard: guard,
      );
      addTearDown(router.dispose);
      addTearDown(guard.dispose);

      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      await tester.pumpAndSettle();
      expect(find.text('Session sign-in route'), findsOneWidget);

      router.go('/patients/register', extra: 'test-access-token');
      await tester.pumpAndSettle();
      expect(find.text('Session sign-in route'), findsOneWidget);
      expect(find.text('Register a new patient'), findsNothing);

      guard.markSessionAvailable();
      await tester.pumpAndSettle();
      router.go('/patients/register', extra: 'test-access-token');
      await tester.pumpAndSettle();
      expect(find.text('Register a new patient'), findsOneWidget);

      const sensitiveDraft = 'SENSITIVE_DRAFT_SLICE_4A';
      await tester.enterText(
        find.byKey(const ValueKey('patient-registration-first-name')),
        sensitiveDraft,
      );
      expect(find.text(sensitiveDraft), findsOneWidget);

      guard.invalidate();
      await tester.pumpAndSettle();

      expect(find.text('Session sign-in route'), findsOneWidget);
      expect(find.text('Register a new patient'), findsNothing);
      expect(
        find.byKey(const ValueKey('patient-registration-first-name')),
        findsNothing,
      );
      expect(find.text(sensitiveDraft), findsNothing);
      expect(guard.requiresLocalRecovery, isTrue);
    },
  );
}
