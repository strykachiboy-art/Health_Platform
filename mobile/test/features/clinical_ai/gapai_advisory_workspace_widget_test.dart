import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_ai/data/clinical_ai_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_ai/presentation/clinical_ai_workspace_screen.dart';

class _Online implements DeviceConnectivitySource {
  @override
  Future<DeviceNetworkStatus> checkStatus() async =>
      DeviceNetworkStatus.connected;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

void main() {
  testWidgets(
    'GAP-AI advisory requires an explicit action and does not write clinical records',
    (tester) async {
      var requests = 0;
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final client = MockClient((request) async {
        requests++;
        expect(request.url.path, '/api/v1/ai/drug-interactions');
        expect(request.method, 'POST');
        expect(jsonDecode(request.body), {
          'drug_names': ['Drug A', 'Drug B'],
        });
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'summary': 'Review prescribed medicines',
              'interactions': [],
              'recommendations': ['Clinician must decide'],
            },
          }),
          200,
        );
      });
      addTearDown(client.close);
      final api = ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      );
      addTearDown(api.close);
      final dashboard = DashboardRemoteDataSource(
        apiClient: api,
        loadSession: () async => AuthSessionState.ready(
          const AuthTokenResponse(
            accessToken: 'aaa.bbb.ccc',
            refreshToken: 'ddd.eee.fff',
            userId: 1,
            role: 'DOCTOR',
            tenantId: '123e4567-e89b-42d3-a456-426614174001',
            clinicContextId: 8,
          ),
        ),
        sessionNavigationGuard: guard,
      );
      await tester.pumpWidget(
        MaterialApp(
          home: ClinicalAiWorkspaceScreen(
            source: ClinicalAiRemoteDataSource(
              dashboard: dashboard,
              guard: guard,
              connectivity: _Online(),
            ),
            guard: guard,
          ),
        ),
      );
      expect(
        find.byKey(const ValueKey('gapai-advisory-disclaimer')),
        findsOneWidget,
      );
      expect(requests, 0);
      await tester.enterText(
        find.byKey(const ValueKey('gapai-drugs')),
        'Drug A,Drug B',
      );
      await tester.ensureVisible(find.byKey(const ValueKey('gapai-request')));
      await tester.tap(find.byKey(const ValueKey('gapai-request')));
      await tester.pumpAndSettle();
      expect(requests, 1);
      expect(
        find.byKey(const ValueKey('gapai-result-warning')),
        findsOneWidget,
      );
      expect(find.text('Clinician must decide'), findsOneWidget);
      guard.invalidate();
      await tester.pump();
      expect(find.byKey(const ValueKey('gapai-result-warning')), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );
}
