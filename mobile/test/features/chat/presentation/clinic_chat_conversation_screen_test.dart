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
import 'package:clinic_system_pro/features/chat/presentation/clinic_chat_conversation_screen.dart';

Map<String, dynamic> conversation() => {
  'id': 10,
  'clinic_id': 3,
  'conversation_type': 'direct',
  'status': 'active',
  'title': 'Authorized discussion',
  'description': null,
  'created_by_id': 11,
  'patient_id': null,
  'last_message_at': null,
  'created_at': '2026-11-08T09:00:00Z',
};

Map<String, dynamic> message() => {
  'id': 30,
  'clinic_id': 3,
  'conversation_id': 10,
  'sender_id': 11,
  'message_type': 'text',
  'content': 'Clinical handoff update',
  'reply_to_message_id': null,
  'status': 'sent',
  'priority': 'urgent',
  'created_at': '2026-11-08T10:00:00Z',
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
  testWidgets('message read screen GETs detail before history', (tester) async {
    final routes = <String>[];
    final client = MockClient((req) async {
      routes.add(req.url.path);
      expect(req.method, 'GET');
      if (req.url.path.endsWith('/conversations/10')) {
        return http.Response(
          jsonEncode({'success': true, 'data': conversation()}),
          200,
        );
      }
      if (req.url.path.endsWith('/conversations/10/messages')) {
        return http.Response(
          jsonEncode({
            'success': true,
            'data': [message()],
            'page': 1,
            'per_page': 25,
            'total': 1,
            'pages': 1,
            'has_next': false,
            'has_previous': false,
          }),
          200,
        );
      }
      throw StateError('Unexpected read');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicChatConversationScreen(
          dataSource: source(client, guard),
          conversationId: 10,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Clinical handoff update'), findsOneWidget);
    expect(find.text('Authorized discussion'), findsOneWidget);
    expect(find.text('URGENT'), findsOneWidget);
    expect(routes, [
      '/api/v1/chat/conversations/10',
      '/api/v1/chat/conversations/10/messages',
    ]);
  });

  testWidgets('sign-out removes cached message text immediately', (
    tester,
  ) async {
    final client = MockClient((req) async {
      if (req.url.path.endsWith('/conversations/10')) {
        return http.Response(
          jsonEncode({'success': true, 'data': conversation()}),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'success': true,
          'data': [message()],
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
        home: ClinicChatConversationScreen(
          dataSource: source(client, guard),
          conversationId: 10,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Clinical handoff update'), findsOneWidget);
    guard.invalidate();
    await tester.pump();
    expect(find.text('Clinical handoff update'), findsNothing);
    expect(find.byKey(const ValueKey('chat-b-history-locked')), findsOneWidget);
  });
}
