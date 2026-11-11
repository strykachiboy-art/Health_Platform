import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';

void main() {
  testWidgets('P25 direct URL is guarded even under default /patients policy', (
    tester,
  ) async {
    final client = MockClient(
      (request) async =>
          throw StateError('Unexpected backend request: ${request.url}'),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard();
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
      loginScreen: const Scaffold(body: Text('P25 login sentinel')),
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/pending-work');
    await tester.pumpAndSettle();
    expect(find.text('P25 login sentinel'), findsOneWidget);
    guard.markSessionAvailable();
    router.go('/pending-work');
    await tester.pumpAndSettle();
    expect(find.text('Pending work unavailable.'), findsOneWidget);
  });
}
