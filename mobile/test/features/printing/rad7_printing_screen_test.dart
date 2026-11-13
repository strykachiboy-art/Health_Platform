import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/printing/presentation/rad7_printing_screen.dart';

void main() {
  testWidgets('printing is visibly locked and sends no network requests', (
    tester,
  ) async {
    var calls = 0;
    final mock = MockClient((req) async {
      calls++;
      throw StateError('No printing network request authorized: ${req.url}');
    });
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: mock,
    );
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(() {
      api.close();
      mock.close();
      guard.dispose();
    });
    final dashboard = DashboardRemoteDataSource(
      apiClient: api,
      sessionNavigationGuard: guard,
      loadSession: () async => const AuthSessionState.ready(
        AuthTokenResponse(
          accessToken: 'token',
          refreshToken: 'refresh',
          userId: 3,
          role: 'doctor',
          clinicContextId: 2,
        ),
      ),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Rad7PrintingScreen(dashboard: dashboard, guard: guard),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('rad7-print-release-locked')),
      findsOneWidget,
    );
    expect(
      find.text('Radiology PRIVATE UNSIGNED working note'),
      findsOneWidget,
    );
    final action = tester.widget<FilledButton>(find.byType(FilledButton));
    expect(action.onPressed, isNull);
    expect(calls, 0);

    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Radiology PRIVATE UNSIGNED working note'), findsNothing);
    expect(calls, 0);
  });
}
