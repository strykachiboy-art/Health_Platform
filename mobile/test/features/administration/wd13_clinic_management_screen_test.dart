import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/administration/data/wd13_clinic_management_remote_data_source.dart';
import 'package:clinic_system_pro/features/administration/presentation/wd13_clinic_management_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

void main() {
  testWidgets('production lock blocks POST despite successful clinic list', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var posts = 0;
    final fake = MockClient((request) async {
      if (request.method == 'POST') posts++;
      return http.Response(jsonEncode({'data': <Object>[]}), 200);
    });
    addTearDown(fake.close);
    final source = Wd13ClinicManagementRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: fake,
      ),
      loadSession: () async => AuthSessionState.ready(
        const AuthTokenResponse(
          accessToken: 'token',
          refreshToken: 'refresh',
          userId: 1,
          role: 'super_admin',
          clinicContextId: null,
        ),
      ),
      sessionNavigationGuard: guard,
      createGate: Wd13RootClinicAttemptGate(),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Wd13ClinicManagementScreen(
          source: source,
          allowUnmeteredStagingCreation: false,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('wd13-create-locked')), findsOneWidget);
    final createButton = find.byKey(const ValueKey('wd13-create-root-clinic'));
    await tester.scrollUntilVisible(
      createButton,
      200.0,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();
    expect(createButton, findsOneWidget);
    final button = tester.widget<FilledButton>(createButton);
    expect(button.onPressed, isNull);
    expect(posts, 0);
  });

  testWidgets('even staging cannot POST before server LIST authorization', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var posts = 0;
    final fake = MockClient((request) async {
      if (request.method == 'POST') posts++;
      return http.Response(
        jsonEncode({'error': 'Insufficient permissions'}),
        403,
      );
    });
    addTearDown(fake.close);
    final source = Wd13ClinicManagementRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: fake,
      ),
      loadSession: () async => AuthSessionState.ready(
        const AuthTokenResponse(
          accessToken: 'token',
          refreshToken: 'refresh',
          userId: 1,
          role: 'super_admin',
          clinicContextId: null,
        ),
      ),
      sessionNavigationGuard: guard,
      createGate: Wd13RootClinicAttemptGate(),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Wd13ClinicManagementScreen(
          source: source,
          allowUnmeteredStagingCreation: true,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('wd13-clinic-error')), findsOneWidget);
    final createButton = find.byKey(const ValueKey('wd13-create-root-clinic'));
    await tester.scrollUntilVisible(
      createButton,
      200.0,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.pumpAndSettle();
    expect(createButton, findsOneWidget);
    final button = tester.widget<FilledButton>(createButton);
    expect(button.onPressed, isNull);
    expect(posts, 0);
  });
}
