import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/radiology/data/rad7_ris_remote_data_source.dart';
import 'package:clinic_system_pro/features/radiology/presentation/rad7_radiology_screen.dart';

class _Disconnected implements DeviceConnectivitySource {
  @override
  Future<DeviceNetworkStatus> checkStatus() async =>
      DeviceNetworkStatus.disconnected;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

void main() {
  testWidgets('RIS document boundary and OHIF denial are explicit', (
    tester,
  ) async {
    var networkCalls = 0;
    final mock = MockClient((request) async {
      networkCalls++;
      throw StateError('Unexpected network request: ${request.url}');
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
    final source = Rad7RisRemoteDataSource(
      dashboard: dashboard,
      guard: guard,
      connectivity: _Disconnected(),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Rad7RadiologyScreen(source: source, guard: guard),
      ),
    );
    expect(find.byKey(const ValueKey('rad7-ris-boundary')), findsOneWidget);
    expect(find.textContaining('Establish exam once'), findsOneWidget);
    expect(networkCalls, 0);
    await tester.enterText(find.byKey(const ValueKey('rad7-order-id')), '42');
    guard.invalidate();
    await tester.pump();
    final orderInput = tester.widget<TextField>(
      find.byKey(const ValueKey('rad7-order-id')),
    );
    expect(orderInput.controller!.text, isEmpty);
    expect(networkCalls, 0);
  });
}
