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
import 'package:clinic_system_pro/features/chat/data/clinic_chat_realtime_reconciler.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_socket_transport.dart';

class _FakeSocket implements ClinicChatSocketTransport {
  final Map<String, void Function(Object?)> handlers = {};
  final List<String> sent = [];
  Map<String, dynamic>? join;
  bool connected = false;
  bool disposed = false;

  @override
  void on(String event, void Function(Object?) callback) {
    handlers[event] = callback;
  }

  @override
  void emit(String event, Map<String, dynamic> payload) {
    sent.add(event);
    if (event == 'conversation.join') join = payload;
  }

  @override
  void connect() => connected = true;

  @override
  void dispose() => disposed = true;

  void fire(String event, Object? payload) => handlers[event]?.call(payload);
}

Map<String, dynamic> _conversation() => {
  'id': 10,
  'clinic_id': 3,
  'conversation_type': 'direct',
  'status': 'active',
  'title': 'Team rounds',
  'description': null,
  'created_by_id': 42,
  'patient_id': null,
  'last_message_at': null,
  'created_at': '2026-11-08T10:00:00Z',
};

Map<String, dynamic> _message({String content = 'Server truth'}) => {
  'id': 51,
  'clinic_id': 3,
  'conversation_id': 10,
  'sender_id': 42,
  'message_type': 'text',
  'content': content,
  'reply_to_message_id': null,
  'status': 'sent',
  'priority': 'normal',
  'created_at': '2026-11-08T11:00:00Z',
};

Map<String, dynamic> _event({
  int clinicId = 3,
  int eventId = 5,
  String eventType = 'message.created',
}) => {
  'clinic_id': clinicId,
  'event_id': eventId,
  'event_type': eventType,
  'conversation_id': 10,
  'message_id': 51,
  'sender_id': 42,
  'content': 'MALICIOUS_SOCKET_CONTENT_SHOULD_NEVER_RENDER',
};

Future<void> _flush() async {
  for (var i = 0; i < 12; i++) {
    await Future<void>.delayed(Duration.zero);
  }
}

class _Harness {
  _Harness();
  final SessionNavigationGuard guard = SessionNavigationGuard()
    ..markSessionAvailable();
  final List<_FakeSocket> sockets = [];
  String text = 'Server truth';
  String token = 'token-A';
  int reads = 0;
  bool failRead = false;
  late final http.Client httpClient = MockClient((request) async {
    final path = request.url.path;
    if (request.method != 'GET') {
      throw StateError('Socket events must not send REST writes');
    }
    if (path == '/api/v1/chat/conversations/10') {
      return http.Response(
        jsonEncode({'success': true, 'data': _conversation()}),
        200,
      );
    }
    if (path == '/api/v1/chat/conversations/10/messages') {
      reads++;
      if (failRead) return http.Response('server unavailable', 500);
      return http.Response(
        jsonEncode({
          'success': true,
          'data': [_message(content: text)],
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
    throw StateError('Unexpected REST path: $path');
  });
  late final ClinicChatReadRemoteDataSource read =
      ClinicChatReadRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: httpClient,
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

  late final ClinicChatRealtimeReconciler controller =
      ClinicChatRealtimeReconciler(
        read: read,
        conversationId: 10,
        socketFactory: ({required origin, required accessToken}) {
          expect(origin.host, '127.0.0.1');
          expect(accessToken, token);
          final socket = _FakeSocket();
          sockets.add(socket);
          return socket;
        },
      );

  Future<_FakeSocket> connect() async {
    await controller.start();
    final socket = sockets.last;
    expect(socket.connected, isTrue);
    socket.fire('chat.connected', {'user_id': 42, 'clinic_id': 3});
    expect(socket.join, {'conversation_id': 10});
    socket.fire('conversation.joined', {'conversation_id': 10, 'user_id': 42});
    await _flush();
    expect(controller.state, ClinicChatRealtimeState.ready);
    return socket;
  }

  void close() {
    controller.dispose();
    guard.dispose();
    httpClient.close();
  }
}

void main() {
  test('handshake joins then REST read supplies all clinical text', () async {
    final h = _Harness();
    addTearDown(h.close);
    final socket = await h.connect();
    expect(h.controller.messages!.items.single.content, 'Server truth');
    h.text = 'Changed on server';
    socket.fire('message.created', _event());
    await _flush();
    expect(h.controller.messages!.items.single.content, 'Changed on server');
    expect(
      h.controller.messages!.items.single.content,
      isNot(contains('MALICIOUS_SOCKET_CONTENT')),
    );
    expect(socket.sent, ['conversation.join']);
  });

  test('same outbox event id is not processed twice', () async {
    final h = _Harness();
    addTearDown(h.close);
    final socket = await h.connect();
    final first = h.reads;
    socket.fire('message.updated', _event(eventType: 'message.updated'));
    await _flush();
    expect(h.reads, first + 1);
    socket.fire('message.updated', _event(eventType: 'message.updated'));
    await _flush();
    expect(h.reads, first + 1);
  });

  test('cross clinic notification fails closed without REST refresh', () async {
    final h = _Harness();
    addTearDown(h.close);
    final socket = await h.connect();
    final reads = h.reads;
    socket.fire('message.created', _event(clinicId: 90));
    expect(h.controller.state, ClinicChatRealtimeState.unavailable);
    expect(h.controller.messages, isNull);
    expect(socket.disposed, isTrue);
    await _flush();
    expect(h.reads, reads);
  });

  test('notification for another authorized conversation is ignored', () async {
    final h = _Harness();
    addTearDown(h.close);
    final socket = await h.connect();
    final before = h.reads;
    final event = _event()..['conversation_id'] = 11;
    socket.fire('message.created', event);
    await _flush();
    expect(h.reads, before);
    expect(h.controller.state, ClinicChatRealtimeState.ready);
    expect(h.controller.messages!.items.single.content, 'Server truth');
  });

  test('wrong handshake cannot authorize a conversation', () async {
    final h = _Harness();
    addTearDown(h.close);
    await h.controller.start();
    final socket = h.sockets.single;
    socket.fire('chat.connected', {'user_id': 42, 'clinic_id': 99});
    expect(h.controller.state, ClinicChatRealtimeState.unavailable);
    expect(socket.join, isNull);
    expect(h.controller.messages, isNull);
  });

  test('chat.error rejects join and clears the snapshot', () async {
    final h = _Harness();
    addTearDown(h.close);
    await h.controller.start();
    final socket = h.sockets.single;
    socket.fire('chat.connected', {'user_id': 42, 'clinic_id': 3});
    socket.fire('chat.error', {'code': 'conversation_join_failed'});
    expect(h.controller.state, ClinicChatRealtimeState.unavailable);
    expect(h.controller.messages, isNull);
    expect(socket.disposed, isTrue);
  });

  test('logout clears PHI and disposes the socket immediately', () async {
    final h = _Harness();
    addTearDown(h.close);
    final socket = await h.connect();
    h.guard.markSignedOut();
    expect(h.controller.state, ClinicChatRealtimeState.offline);
    expect(h.controller.messages, isNull);
    expect(socket.disposed, isTrue);
    socket.fire('message.created', _event());
    expect(h.controller.messages, isNull);
  });

  test('REST failure clears previously authorized messages', () async {
    final h = _Harness();
    addTearDown(h.close);
    final socket = await h.connect();
    h.failRead = true;
    socket.fire('message.deleted', _event(eventType: 'message.deleted'));
    await _flush();
    expect(h.controller.state, ClinicChatRealtimeState.unavailable);
    expect(h.controller.messages, isNull);
  });

  test('explicit restart makes fresh socket and rechecks REST', () async {
    final h = _Harness();
    addTearDown(h.close);
    final first = await h.connect();
    first.fire('disconnect', 'transport closed');
    expect(h.controller.messages, isNull);
    expect(first.disposed, isTrue);
    h.token = 'token-B';
    h.text = 'Fresh authoritative history';
    final second = await h.connect();
    expect(second, isNot(same(first)));
    expect(
      h.controller.messages!.items.single.content,
      'Fresh authoritative history',
    );
  });
}
