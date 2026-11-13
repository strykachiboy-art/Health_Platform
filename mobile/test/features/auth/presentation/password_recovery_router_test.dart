import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';

void main() {
  testWidgets(
    'public recovery screens are reachable while patients remain guarded',
    (tester) async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response('{}', 200);
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
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        loginScreen: const Scaffold(body: Center(child: Text('Test login'))),
        authRemoteDataSource: AuthRemoteDataSource(apiClient: api),
        sessionNavigationGuard: guard,
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/forgot-password');
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('auth-r01-forgot-email')),
        findsOneWidget,
      );
      router.go('/reset-password');
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('auth-r01-reset-link')), findsOneWidget);
      expect(guard.redirect('/patients/99'), '/login');
      expect(calls, 0);
    },
  );

  testWidgets('missing auth data source does not render an active reset form', (
    tester,
  ) async {
    final client = MockClient((_) async => http.Response('{}', 200));
    addTearDown(client.close);
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Text('Test login')),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/reset-password');
    await tester.pumpAndSettle();
    expect(find.text('Password recovery unavailable.'), findsOneWidget);
    expect(find.byKey(const ValueKey('auth-r01-reset-submit')), findsNothing);
  });
}
