import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';

void main() {
  test('RAD7 home roles suggest only eligible navigation', () {
    final doctor = homeDestinationsFor(role: 'doctor', clinicContextId: 2);
    expect(doctor.any((d) => d.path == '/radiology'), isTrue);
    expect(doctor.any((d) => d.path == '/printing'), isTrue);
    final accountant = homeDestinationsFor(
      role: 'accountant',
      clinicContextId: 2,
    );
    expect(accountant.any((d) => d.path == '/radiology'), isFalse);
    expect(accountant.any((d) => d.path == '/printing'), isTrue);
    final patient = homeDestinationsFor(role: 'patient', clinicContextId: 2);
    expect(patient.any((d) => d.path == '/radiology'), isFalse);
    expect(patient.any((d) => d.path == '/printing'), isFalse);
    final missingClinic = homeDestinationsFor(
      role: 'doctor',
      clinicContextId: null,
    );
    expect(missingClinic.any((d) => d.path == '/radiology'), isFalse);
    expect(missingClinic.any((d) => d.path == '/printing'), isFalse);
  });

  testWidgets('signed-out RAD7 direct links return to login', (tester) async {
    final mock = MockClient((request) async {
      throw StateError('No request expected: ${request.url}');
    });
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: mock,
    );
    final guard = SessionNavigationGuard();
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(
        body: Center(child: Text('Sign in required')),
      ),
      sessionNavigationGuard: guard,
    );
    addTearDown(() {
      router.dispose();
      guard.dispose();
      api.close();
      mock.close();
    });
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.pumpAndSettle();
    router.go('/radiology');
    await tester.pumpAndSettle();
    expect(find.text('Sign in required'), findsOneWidget);
    router.go('/printing');
    await tester.pumpAndSettle();
    expect(find.text('Sign in required'), findsOneWidget);
    expect(find.byKey(const ValueKey('rad7-ris-boundary')), findsNothing);
    expect(
      find.byKey(const ValueKey('rad7-print-release-locked')),
      findsNothing,
    );
  });
}
