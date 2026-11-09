import 'dart:convert';

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
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

AuthSessionState _session({String role = 'nurse', int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'chat-f-token',
        refreshToken: 'chat-f-refresh',
        userId: 12,
        role: role,
        clinicContextId: clinicId,
      ),
    );

ApiClient _api(MockClient client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

void main() {
  test('CHAT-F navigation requires a clinic and appropriate staff role', () {
    List<String> destinations(String role, int? clinic) => homeDestinationsFor(
      role: role,
      clinicContextId: clinic,
    ).map((entry) => entry.id).toList();

    for (final role in ['admin', 'doctor', 'nurse']) {
      expect(destinations(role, 3), contains('chat'));
      expect(destinations(role, null), isNot(contains('chat')));
    }
    for (final role in [
      'patient',
      'super_admin',
      'accountant',
      'receptionist',
    ]) {
      expect(destinations(role, 3), isNot(contains('chat')));
    }
    final entry = homeDestinationsFor(
      role: 'nurse',
      clinicContextId: 3,
    ).singleWhere((item) => item.id == 'chat');
    expect(entry.path, '/chat');
    expect(entry.label, 'Clinic messages');
  });

  testWidgets(
    'CHAT-F /chat route cannot display inbox without active session',
    (tester) async {
      final client = MockClient((request) async {
        throw StateError('No transport expected: ${request.url}');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const ['/chat', '/home'],
      );
      addTearDown(guard.dispose);
      final api = _api(client);
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        patientAdmissionDataSource: PatientAdmissionRemoteDataSource(
          apiClient: api,
          loadSession: () async => _session(),
          sessionNavigationGuard: guard,
        ),
        loginScreen: const Scaffold(
          body: Center(child: Text('Sign in required')),
        ),
        sessionNavigationGuard: guard,
        loadHomeSession: () async => _session(),
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/chat');
      await tester.pumpAndSettle();
      expect(find.text('Sign in required'), findsOneWidget);
      expect(find.byKey(const ValueKey('chat-b-inbox')), findsNothing);
    },
  );

  testWidgets(
    'CHAT-F router opens server-authorized inbox and revokes on signout',
    (tester) async {
      var reads = 0;
      final client = MockClient((request) async {
        reads++;
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/chat/conversations');
        expect(request.headers['authorization'], 'Bearer chat-f-token');
        return http.Response(
          jsonEncode({
            'success': true,
            'data': <Object>[],
            'page': 1,
            'per_page': 25,
            'total': 0,
            'pages': 0,
            'has_next': false,
            'has_previous': false,
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const ['/chat', '/home'],
      )..markSessionAvailable();
      addTearDown(guard.dispose);
      final api = _api(client);
      final router = createAppRouter(
        healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
        patientAdmissionDataSource: PatientAdmissionRemoteDataSource(
          apiClient: api,
          loadSession: () async => _session(),
          sessionNavigationGuard: guard,
        ),
        loginScreen: const Scaffold(
          body: Center(child: Text('Sign in required')),
        ),
        sessionNavigationGuard: guard,
        loadHomeSession: () async => _session(),
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/chat');
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('chat-b-inbox')), findsOneWidget);
      expect(find.textContaining('Authorized conversations'), findsOneWidget);
      expect(reads, 1);
      guard.markSignedOut();
      await tester.pumpAndSettle();
      expect(find.text('Sign in required'), findsOneWidget);
      expect(find.byKey(const ValueKey('chat-b-inbox')), findsNothing);
    },
  );

  testWidgets('CHAT-F missing authorized data source fails closed', (
    tester,
  ) async {
    final client = MockClient((request) async {
      throw StateError('No transport expected: ${request.url}');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: _api(client)),
      loginScreen: const Scaffold(body: Text('Sign in required')),
      sessionNavigationGuard: guard,
      loadHomeSession: () async => _session(),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/chat');
    await tester.pumpAndSettle();
    expect(find.text('Clinic messages unavailable.'), findsOneWidget);
    expect(find.byKey(const ValueKey('chat-b-inbox')), findsNothing);
  });
}
