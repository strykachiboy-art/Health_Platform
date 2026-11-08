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
import 'package:clinic_system_pro/features/chat/data/clinic_chat_socket_transport.dart';
import 'package:clinic_system_pro/features/chat/presentation/clinic_chat_text_conversation_screen.dart';

class _FakeSocket implements ClinicChatSocketTransport {
  final Map<String, void Function(Object?)> callbacks = {};
  final List<String> emitted = [];
  bool disposed = false;
  bool connected = false;

  @override
  void on(String event, void Function(Object? payload) callback) {
    callbacks[event] = callback;
  }

  @override
  void emit(String event, Map<String, dynamic> payload) {
    emitted.add(event);
  }

  @override
  void connect() => connected = true;

  @override
  void dispose() => disposed = true;

  void fire(String event, Object? payload) => callbacks[event]?.call(payload);
}

Map<String, dynamic> _conversation() => {
  'id': 10,
  'clinic_id': 3,
  'conversation_type': 'direct',
  'status': 'active',
  'title': 'Ward care team',
  'description': null,
  'created_by_id': 42,
  'patient_id': null,
  'last_message_at': null,
  'created_at': '2026-11-08T10:00:00Z',
};

Map<String, dynamic> _message(String text) => {
  'id': 51,
  'clinic_id': 3,
  'conversation_id': 10,
  'sender_id': 42,
  'message_type': 'text',
  'content': text,
  'reply_to_message_id': null,
  'status': 'sent',
  'priority': 'normal',
  'created_at': '2026-11-08T11:00:00Z',
};

class _Fixture {
  final guard = SessionNavigationGuard()..markSessionAvailable();
  final sockets = <_FakeSocket>[];
  String token = 'jwt-1';
  String text = 'Verified clinical history';
  int reads = 0;
  int writes = 0;
  bool rejectReads = false;

  late final http.Client client = MockClient((req) async {
    if (req.method != 'GET') {
      writes++;
      throw StateError('This test does not authorize a message mutation.');
    }
    if (req.url.path == '/api/v1/chat/conversations/10') {
      return http.Response(
        jsonEncode({'success': true, 'data': _conversation()}),
        200,
      );
    }
    if (req.url.path == '/api/v1/chat/conversations/10/messages') {
      reads++;
      if (rejectReads) {
        return http.Response('unavailable', 503);
      }
      return http.Response(
        jsonEncode({
          'success': true,
          'data': [_message(text)],
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
    throw StateError('Unexpected read endpoint: ${req.url.path}');
  });

  late final source = ClinicChatReadRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: () async => AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: token,
        refreshToken: 'refresh',
        userId: 42,
        role: 'nurse',
        clinicContextId: 3,
      ),
    ),
    sessionNavigationGuard: guard,
  );

  Future<void> mount(WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicChatTextConversationScreen(
          dataSource: source,
          conversationId: 10,
          realtimeEnabled: true,
          socketFactory: ({required origin, required accessToken}) {
            expect(origin.host, '127.0.0.1');
            expect(accessToken, token);
            final socket = _FakeSocket();
            sockets.add(socket);
            return socket;
          },
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  Future<_FakeSocket> authorize(WidgetTester tester) async {
    await tester.pumpAndSettle();
    final socket = sockets.last;
    expect(socket.connected, isTrue);
    socket.fire('chat.connected', {'user_id': 42, 'clinic_id': 3});
    expect(socket.emitted, contains('conversation.join'));
    socket.fire('conversation.joined', {'conversation_id': 10, 'user_id': 42});
    await tester.pumpAndSettle();
    expect(find.text(text), findsOneWidget);
    return socket;
  }

  void close() {
    client.close();
    guard.dispose();
  }
}

void main() {
  testWidgets('history is hidden before authorized socket join', (
    tester,
  ) async {
    final f = _Fixture();
    addTearDown(f.close);
    await f.mount(tester);
    expect(find.byKey(const ValueKey('chat-d2-live-blocked')), findsOneWidget);
    expect(find.text('Verified clinical history'), findsNothing);
    expect(find.byKey(const ValueKey('chat-c-compose')), findsNothing);
    await f.authorize(tester);
    expect(find.byKey(const ValueKey('chat-c-compose')), findsOneWidget);
    expect(f.writes, 0);
  });

  testWidgets('event body is never rendered; REST provides new text', (
    tester,
  ) async {
    final f = _Fixture();
    addTearDown(f.close);
    await f.mount(tester);
    final socket = await f.authorize(tester);
    f.text = 'Server-verified update';
    socket.fire('message.updated', {
      'clinic_id': 3,
      'event_id': 8,
      'event_type': 'message.updated',
      'conversation_id': 10,
      'message_id': 51,
      'content': 'UNTRUSTED SOCKET BODY',
    });
    await tester.pumpAndSettle();
    expect(find.text('Server-verified update'), findsOneWidget);
    expect(find.textContaining('UNTRUSTED SOCKET BODY'), findsNothing);
    expect(f.writes, 0);
  });

  testWidgets('disconnect clears PHI; reconnect uses fresh socket and REST', (
    tester,
  ) async {
    final f = _Fixture();
    addTearDown(f.close);
    await f.mount(tester);
    final old = await f.authorize(tester);
    old.fire('disconnect', 'network lost');
    await tester.pump();
    expect(old.disposed, isTrue);
    expect(find.text('Verified clinical history'), findsNothing);
    expect(find.byKey(const ValueKey('chat-d2-live-blocked')), findsOneWidget);
    f.text = 'History after rejoining';
    f.token = 'jwt-2';
    await tester.tap(find.byKey(const ValueKey('chat-d2-reconnect')));
    await tester.pumpAndSettle();
    expect(f.sockets.length, 2);
    expect(f.sockets.last, isNot(same(old)));
    await f.authorize(tester);
    expect(find.text('Verified clinical history'), findsNothing);
    expect(f.writes, 0);
  });

  testWidgets('cross-clinic event clears history without replay', (
    tester,
  ) async {
    final f = _Fixture();
    addTearDown(f.close);
    await f.mount(tester);
    final socket = await f.authorize(tester);
    final before = f.reads;
    socket.fire('message.created', {
      'clinic_id': 99,
      'event_id': 10,
      'event_type': 'message.created',
      'conversation_id': 10,
      'message_id': 51,
    });
    await tester.pump();
    expect(find.text('Verified clinical history'), findsNothing);
    expect(find.byKey(const ValueKey('chat-d2-live-blocked')), findsOneWidget);
    expect(socket.disposed, isTrue);
    expect(f.reads, before);
  });

  testWidgets('REST authorization failure after event hides all PHI', (
    tester,
  ) async {
    final f = _Fixture();
    addTearDown(f.close);
    await f.mount(tester);
    final socket = await f.authorize(tester);
    f.rejectReads = true;
    socket.fire('message.deleted', {
      'clinic_id': 3,
      'event_id': 11,
      'event_type': 'message.deleted',
      'conversation_id': 10,
      'message_id': 51,
    });
    await tester.pumpAndSettle();
    expect(find.text('Verified clinical history'), findsNothing);
    expect(find.byKey(const ValueKey('chat-d2-live-blocked')), findsOneWidget);
    expect(f.writes, 0);
  });

  testWidgets('logout clears history and releases socket', (tester) async {
    final f = _Fixture();
    addTearDown(f.close);
    await f.mount(tester);
    final socket = await f.authorize(tester);
    f.guard.markSignedOut();
    await tester.pump();
    expect(socket.disposed, isTrue);
    expect(find.text('Verified clinical history'), findsNothing);
    expect(find.byKey(const ValueKey('chat-c-locked')), findsOneWidget);
  });

  testWidgets('missing socket acknowledgement times out fail closed', (
    tester,
  ) async {
    final f = _Fixture();
    addTearDown(f.close);
    await f.mount(tester);
    expect(find.byKey(const ValueKey('chat-d2-live-blocked')), findsOneWidget);
    await tester.pump(const Duration(seconds: 16));
    await tester.pump();
    expect(f.sockets.single.disposed, isTrue);
    expect(find.text('Verified clinical history'), findsNothing);
    expect(find.byKey(const ValueKey('chat-d2-reconnect')), findsOneWidget);
  });
}
