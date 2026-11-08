import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/presentation/clinic_chat_inbox_screen.dart';

Map<String, dynamic> conversation() => {
  'id': 10,
  'clinic_id': 3,
  'conversation_type': 'direct',
  'status': 'active',
  'title': 'Clinical team',
  'description': null,
  'created_by_id': 11,
  'patient_id': null,
  'last_message_at': null,
  'created_at': '2026-11-08T09:00:00Z',
};

ClinicChatReadRemoteDataSource source(
  MockClient client,
  SessionNavigationGuard guard,
) => ClinicChatReadRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => AuthSessionState.ready(
    const AuthTokenResponse(
      accessToken: 'chat-token',
      refreshToken: 'refresh',
      userId: 11,
      role: 'nurse',
      clinicContextId: 3,
    ),
  ),
  sessionNavigationGuard: guard,
);

void main() {
  testWidgets('inbox is read-only, loads authorized conversations', (
    tester,
  ) async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method != 'GET') {
        writes++;
      }
      return http.Response(
        jsonEncode({
          'success': true,
          'data': [conversation()],
          'page': 1,
          'per_page': 25,
          'total': 1,
          'pages': 1,
          'has_next': false,
          'has_previous': false,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicChatInboxScreen(dataSource: source(client, guard)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Clinical team'), findsOneWidget);
    expect(find.textContaining('Read-only preview'), findsOneWidget);
    expect(find.byKey(const ValueKey('chat-b-search')), findsOneWidget);
    expect(writes, 0);
  });

  testWidgets('sign-out immediately clears conversation titles', (
    tester,
  ) async {
    final client = MockClient(
      (req) async => http.Response(
        jsonEncode({
          'success': true,
          'data': [conversation()],
          'page': 1,
          'per_page': 25,
          'total': 1,
          'pages': 1,
          'has_next': false,
          'has_previous': false,
        }),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicChatInboxScreen(dataSource: source(client, guard)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Clinical team'), findsOneWidget);
    guard.markSignedOut();
    await tester.pump();
    expect(find.text('Clinical team'), findsNothing);
    expect(find.byKey(const ValueKey('chat-b-locked')), findsOneWidget);
  });
}
