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
import 'package:clinic_system_pro/features/chat/presentation/clinic_chat_text_conversation_screen.dart';

Map<String, dynamic> _conversation() => {
  'id': 10,
  'clinic_id': 3,
  'conversation_type': 'direct',
  'status': 'active',
  'title': 'Clinical discussion',
  'description': null,
  'created_by_id': 42,
  'patient_id': null,
  'last_message_at': null,
  'created_at': '2026-11-08T10:00:00Z',
};

Map<String, dynamic> _message({
  int id = 50,
  String content = 'Earlier message',
  String status = 'sent',
  int sender = 42,
  String priority = 'normal',
}) => {
  'id': id,
  'clinic_id': 3,
  'conversation_id': 10,
  'sender_id': sender,
  'message_type': 'text',
  'content': content,
  'reply_to_message_id': null,
  'status': status,
  'priority': priority,
  'created_at': '2026-11-08T11:00:00Z',
};

http.Response _single(Map<String, dynamic> row, [int status = 200]) =>
    http.Response(jsonEncode({'success': true, 'data': row}), status);

http.Response _page(List<Map<String, dynamic>> rows) => http.Response(
  jsonEncode({
    'success': true,
    'data': rows,
    'page': 1,
    'per_page': 25,
    'total': rows.length,
    'pages': rows.isEmpty ? 0 : 1,
    'has_next': false,
    'has_previous': false,
  }),
  200,
);

ClinicChatReadRemoteDataSource _source(
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
      userId: 42,
      role: 'nurse',
      clinicContextId: 3,
    ),
  ),
  sessionNavigationGuard: guard,
);

void main() {
  testWidgets('send text uses one POST, then authenticated GET readback', (
    tester,
  ) async {
    var posts = 0;
    final routes = <String>[];
    final client = MockClient((req) async {
      routes.add('${req.method} ${req.url.path}');
      if (req.method == 'POST') {
        posts++;
        return _single(
          _message(id: 51, content: 'Hello team', status: 'pending'),
          201,
        );
      }
      if (req.url.path.endsWith('/conversations/10')) {
        return _single(_conversation());
      }
      if (req.url.path.endsWith('/messages/51')) {
        return _single(_message(id: 51, content: 'Hello team', status: 'sent'));
      }
      return _page([
        _message(),
        if (posts > 0) _message(id: 51, content: 'Hello team'),
      ]);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicChatTextConversationScreen(
          dataSource: _source(client, guard),
          conversationId: 10,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Earlier message'), findsOneWidget);
    await tester.enterText(
      find.byKey(const ValueKey('chat-c-compose')),
      'Hello team',
    );
    await tester.pump();
    final enabledSubmit = tester.widget<FilledButton>(
      find.byKey(const ValueKey('chat-c-submit')),
    );
    expect(enabledSubmit.onPressed, isNotNull);
    await tester.tap(find.byKey(const ValueKey('chat-c-submit')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(routes, contains('POST /api/v1/chat/conversations/10/messages'));
    expect(routes, contains('GET /api/v1/chat/messages/51'));
    expect(find.textContaining('Server confirmed message'), findsOneWidget);
  });

  testWidgets('failed POST blocks replay and clears stale message history', (
    tester,
  ) async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        throw http.ClientException('lost response');
      }
      if (req.url.path.endsWith('/conversations/10')) {
        return _single(_conversation());
      }
      return _page([_message()]);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicChatTextConversationScreen(
          dataSource: _source(client, guard),
          conversationId: 10,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('chat-c-compose')),
      'Uncertain',
    );
    await tester.pump();
    final enabledSubmit = tester.widget<FilledButton>(
      find.byKey(const ValueKey('chat-c-submit')),
    );
    expect(enabledSubmit.onPressed, isNotNull);
    await tester.tap(find.byKey(const ValueKey('chat-c-submit')));
    await tester.pumpAndSettle();
    expect(writes, 1);
    expect(find.byKey(const ValueKey('chat-c-unconfirmed')), findsOneWidget);
    expect(find.text('Earlier message'), findsNothing);
    final button = tester.widget<FilledButton>(
      find.byKey(const ValueKey('chat-c-submit')),
    );
    expect(button.onPressed, isNull);
  });

  testWidgets('signout immediately removes private messages and composer', (
    tester,
  ) async {
    final client = MockClient(
      (req) async => req.url.path.endsWith('/conversations/10')
          ? _single(_conversation())
          : _page([_message()]),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicChatTextConversationScreen(
          dataSource: _source(client, guard),
          conversationId: 10,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Earlier message'), findsOneWidget);
    guard.markSignedOut();
    await tester.pump();
    expect(find.text('Earlier message'), findsNothing);
    expect(find.byKey(const ValueKey('chat-c-compose')), findsNothing);
    expect(find.byKey(const ValueKey('chat-c-locked')), findsOneWidget);
  });

  testWidgets('own message offers edit/delete, others only reply', (
    tester,
  ) async {
    final client = MockClient(
      (req) async => req.url.path.endsWith('/conversations/10')
          ? _single(_conversation())
          : _page([_message(), _message(id: 60, sender: 999)]),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicChatTextConversationScreen(
          dataSource: _source(client, guard),
          conversationId: 10,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('chat-c-reply-60')), findsOneWidget);
    expect(find.byKey(const ValueKey('chat-c-edit-50')), findsOneWidget);
    expect(find.byKey(const ValueKey('chat-c-delete-50')), findsOneWidget);
    expect(find.byKey(const ValueKey('chat-c-edit-60')), findsNothing);
    expect(find.byKey(const ValueKey('chat-c-delete-60')), findsNothing);
  });
}
