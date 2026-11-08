import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';

AuthSessionState ready({int clinic = 3, String token = 'chat-token'}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: token,
        refreshToken: 'chat-refresh',
        userId: 11,
        role: 'nurse',
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> conversation({int clinic = 3, int id = 10}) => {
  'id': id,
  'clinic_id': clinic,
  'conversation_type': 'direct',
  'status': 'active',
  'title': 'Ward coordination',
  'description': null,
  'created_by_id': 11,
  'patient_id': null,
  'last_message_at': null,
  'created_at': '2026-11-08T09:00:00+00:00',
};

Map<String, dynamic> message({int clinic = 3, int convo = 10}) => {
  'id': 30,
  'clinic_id': clinic,
  'conversation_id': convo,
  'sender_id': 11,
  'message_type': 'text',
  'status': 'sent',
  'priority': 'normal',
  'content': 'Shift update',
  'reply_to_message_id': null,
  'created_at': '2026-11-08T10:00:00+00:00',
};

Map<String, dynamic> paged(List<Object> data, {int total = 1}) => {
  'success': true,
  'data': data,
  'page': 1,
  'per_page': 25,
  'total': total,
  'pages': total == 0 ? 0 : 1,
  'has_next': false,
  'has_previous': false,
};

http.Response jsonResponse(Object body, [int status = 200]) =>
    http.Response(jsonEncode(body), status);

ClinicChatReadRemoteDataSource source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loadSession,
}) => ClinicChatReadRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: loadSession ?? () async => ready(),
  sessionNavigationGuard: guard,
);

void main() {
  test('conversations GET is bearer-authenticated and read-only', () async {
    var reads = 0;
    final client = MockClient((req) async {
      reads++;
      expect(req.method, 'GET');
      expect(req.url.path, '/api/v1/chat/conversations');
      expect(req.url.queryParameters['page'], '1');
      expect(req.url.queryParameters['per_page'], '25');
      expect(req.headers['authorization'], 'Bearer chat-token');
      return jsonResponse(paged([conversation()]));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await source(client, guard).listConversations();
    expect(result.items.single.displayTitle, 'Ward coordination');
    expect(reads, 1);
  });

  test('conversation detail and message history remain GET-only', () async {
    var reads = 0;
    final client = MockClient((req) async {
      reads++;
      expect(req.method, 'GET');
      if (req.url.path == '/api/v1/chat/conversations/10') {
        return jsonResponse({'success': true, 'data': conversation()});
      }
      if (req.url.path == '/api/v1/chat/conversations/10/messages') {
        return jsonResponse(paged([message()]));
      }
      throw StateError('Unexpected route: ${req.url.path}');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final data = source(client, guard);
    expect((await data.getConversation(10)).id, 10);
    final history = await data.listMessages(conversationId: 10);
    expect(history.items.single.safePreview, 'Shift update');
    expect(reads, 2);
  });

  test('wrong-clinic conversation is rejected before display', () async {
    final client = MockClient(
      (req) async => jsonResponse(paged([conversation(clinic: 4)])),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      source(client, guard).listConversations(),
      throwsFormatException,
    );
  });

  test('wrong-conversation messages are rejected before display', () async {
    final client = MockClient(
      (req) async => jsonResponse(paged([message(convo: 999)])),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      source(client, guard).listMessages(conversationId: 10),
      throwsFormatException,
    );
  });

  test('stale auth token after GET fails closed', () async {
    var currentToken = 'chat-token';
    final client = MockClient((req) async {
      currentToken = 'rotated-token';
      return jsonResponse(paged([conversation()]));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final data = source(
      client,
      guard,
      loadSession: () async => ready(token: currentToken),
    );
    await expectLater(
      data.listConversations(),
      throwsA(isA<ClinicChatSessionUnavailable>()),
    );
  });

  test('sign out before GET prevents transport access', () async {
    var transportCalls = 0;
    final client = MockClient((req) async {
      transportCalls++;
      return jsonResponse(paged([conversation()]));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final data = source(client, guard);
    guard.markSignedOut();
    await expectLater(
      data.listConversations(),
      throwsA(isA<ClinicChatSessionUnavailable>()),
    );
    expect(transportCalls, 0);
  });

  test('duplicate server conversation ID fails closed', () async {
    final client = MockClient(
      (req) async =>
          jsonResponse(paged([conversation(), conversation()], total: 2)),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      source(client, guard).listConversations(),
      throwsFormatException,
    );
  });

  test('cross-conversation search validates message scope', () async {
    var reads = 0;
    final client = MockClient((req) async {
      reads++;
      expect(req.method, 'GET');
      expect(req.url.path, '/api/v1/chat/messages/search');
      expect(req.url.queryParameters['query'], 'handoff');
      expect(req.url.queryParameters['conversation_id'], '10');
      return jsonResponse(paged([message()]));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await source(
      client,
      guard,
    ).searchMessages(query: 'handoff', conversationId: 10);
    expect(result.items.single.conversationId, 10);
    expect(reads, 1);
  });

  test('invalid input is rejected without a request', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      return jsonResponse(paged(<Object>[], total: 0));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final data = source(client, guard);
    await expectLater(
      data.listMessages(conversationId: 0),
      throwsArgumentError,
    );
    await expectLater(data.searchMessages(query: '    '), throwsArgumentError);
    expect(calls, 0);
  });
}
