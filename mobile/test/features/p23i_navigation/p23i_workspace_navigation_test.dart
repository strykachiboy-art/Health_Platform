import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:clinic_system_pro/features/p23i_navigation/p23i_workspaces.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';

AuthSessionState _session(String role) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'p23i-token',
    refreshToken: 'p23i-refresh',
    userId: 41,
    clinicContextId: 3,
    role: role,
  ),
);

void main() {
  test('billing and ambulance home entries have exact scoped roles', () {
    bool entry(String role, int? clinicId, String id) => homeDestinationsFor(
      role: role,
      clinicContextId: clinicId,
    ).any((e) => e.id == id);

    expect(entry('admin', 3, 'billing'), isTrue);
    expect(entry('doctor', 3, 'billing'), isFalse);
    expect(entry('accountant', 3, 'billing'), isFalse);
    expect(entry('admin', null, 'billing'), isFalse);
    for (final role in const <String>{
      'admin',
      'ambulance_coordinator',
      'ambulance_dispatcher',
      'driver',
      'paramedic',
      'emt',
    }) {
      expect(entry(role, 3, 'ambulance'), isTrue, reason: role);
    }
    for (final role in const <String>{
      'patient',
      'doctor',
      'nurse',
      'receptionist',
      'accountant',
    }) {
      expect(entry(role, 3, 'ambulance'), isFalse, reason: role);
    }
    expect(entry('driver', null, 'ambulance'), isFalse);
  });

  testWidgets(
    'direct workspace links redirect before any GET or POST when signed out',
    (tester) async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return http.Response('{"success":false}', 403);
      });
      addTearDown(client.close);
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      );
      final guard = SessionNavigationGuard();
      addTearDown(guard.dispose);
      final reader = PatientPrescriptionRemoteDataSource(
        apiClient: api,
        loadSession: () async => _session('admin'),
        sessionNavigationGuard: guard,
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Text('P23I sign in')),
        sessionNavigationGuard: guard,
        patientPrescriptionDataSource: reader,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      await tester.pumpAndSettle();

      router.go('/billing');
      await tester.pumpAndSettle();
      expect(find.text('P23I sign in'), findsOneWidget);
      router.go('/ambulance');
      await tester.pumpAndSettle();
      expect(find.text('P23I sign in'), findsOneWidget);
      expect(requests, 0);
    },
  );

  testWidgets(
    'billing fails closed for crew; ambulance hides management actions',
    (tester) async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return http.Response('{"success":false}', 403);
      });
      addTearDown(client.close);
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      );
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final reader = PatientPrescriptionRemoteDataSource(
        apiClient: api,
        loadSession: () async => _session('driver'),
        sessionNavigationGuard: guard,
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Text('P23I sign in')),
        sessionNavigationGuard: guard,
        patientPrescriptionDataSource: reader,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/billing');
      await tester.pumpAndSettle();
      expect(find.text('Billing access unavailable.'), findsOneWidget);
      expect(find.byKey(const ValueKey('p23i-billing-create')), findsNothing);
      router.go('/ambulance');
      await tester.pumpAndSettle();
      expect(find.byType(P23IWorkspaceScreen), findsOneWidget);
      expect(find.byKey(const ValueKey('p23i-ambulance-read')), findsOneWidget);
      expect(
        find.byKey(const ValueKey('p23i-ambulance-dispatch')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('p23i-ambulance-invoice-link')),
        findsNothing,
      );
      expect(requests, 0);
    },
  );

  testWidgets(
    'admin billing shows actions; opening and cancelling never sends POST',
    (tester) async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return http.Response('{"success":false}', 403);
      });
      addTearDown(client.close);
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      );
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final reader = PatientPrescriptionRemoteDataSource(
        apiClient: api,
        loadSession: () async => _session('admin'),
        sessionNavigationGuard: guard,
      );
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Text('P23I sign in')),
        sessionNavigationGuard: guard,
        patientPrescriptionDataSource: reader,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/billing');
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23i-billing-create')), findsOneWidget);
      expect(
        find.byKey(const ValueKey('p23i-billing-payment')),
        findsOneWidget,
      );
      await tester.tap(find.byKey(const ValueKey('p23i-billing-create')));
      await tester.pumpAndSettle();
      expect(find.text('Choose patient for invoice'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(requests, 0);

      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('P23I sign in'), findsOneWidget);
      expect(requests, 0);
    },
  );

  testWidgets('missing injected source fails closed without backend work', (
    tester,
  ) async {
    final client = MockClient(
      (request) async => throw StateError('Unexpected request: ${request.url}'),
    );
    addTearDown(client.close);
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('P23I sign in')),
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/ambulance');
    await tester.pumpAndSettle();
    expect(find.text('Ambulance navigation unavailable.'), findsOneWidget);
    router.go('/billing');
    await tester.pumpAndSettle();
    expect(find.text('Billing navigation unavailable.'), findsOneWidget);
  });
}
