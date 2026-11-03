import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_remote_data_source.dart';

Map<String, dynamic> _requestedGrant() {
  return <String, dynamic>{
    'id': 17,
    'clinic_id': 3,
    'patient_id': 7,
    'requester_user_id': 42,
    'requester_role': 'DOCTOR',
    'reason': 'Emergency treatment',
    'purpose': 'Immediate clinical review',
    'scope': <String>['patient:read'],
    'status': 'requested',
    'requested_at': '2026-11-03T12:00:00+00:00',
    'granted_at': null,
    'expires_at': null,
    'revoked_at': null,
    'reviewed_at': null,
    'reviewed_by_user_id': null,
    'review_notes': null,
  };
}

void main() {
  testWidgets(
    'review route is guarded and invalidation clears sensitive review input',
    (tester) async {
      var requests = 0;

      final client = MockClient((request) async {
        requests++;

        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/emergency-access/requests/17');

        return http.Response(
          jsonEncode(<String, dynamic>{
            'success': true,
            'data': _requestedGrant(),
          }),
          200,
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

      router.go('/emergency-access/requests/17');
      await tester.pumpAndSettle();

      expect(find.text('Session sign-in route'), findsOneWidget);
      expect(requests, 0);

      guard.markSessionAvailable();
      await tester.pumpAndSettle();

      router.go('/emergency-access/requests/17');
      await tester.pumpAndSettle();

      expect(requests, 1);
      expect(
        find.byKey(const ValueKey('emergency-access-review-title')),
        findsOneWidget,
      );

      const sensitiveNotes = 'SENSITIVE_REVIEW_NOTES_TEST_ONLY';

      await tester.enterText(
        find.byKey(const ValueKey('emergency-access-review-notes')),
        sensitiveNotes,
      );

      expect(find.text(sensitiveNotes), findsOneWidget);

      guard.invalidate();
      await tester.pumpAndSettle();

      expect(find.text('Session sign-in route'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('emergency-access-review-title')),
        findsNothing,
      );
      expect(find.text(sensitiveNotes), findsNothing);
      expect(guard.requiresLocalRecovery, isTrue);
    },
  );
}
