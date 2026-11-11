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
import 'package:clinic_system_pro/features/chat/data/clinic_chat_final_engagement_remote_data_source.dart';

Map<String, dynamic> _room({int clinic = 3}) => {
  'id': 10,
  'clinic_id': clinic,
  'conversation_type': 'direct',
  'status': 'active',
  'title': 'Team',
  'description': null,
  'created_by_id': 42,
  'patient_id': null,
  'last_message_at': null,
  'created_at': '2026-11-10T09:00:00Z',
};
Map<String, dynamic> _message() => {
  'id': 50,
  'clinic_id': 3,
  'conversation_id': 10,
  'sender_id': 42,
  'message_type': 'text',
  'content': 'Confirmed note',
  'status': 'sent',
  'priority': 'normal',
  'reply_to_message_id': null,
  'created_at': '2026-11-10T10:00:00Z',
};
http.Response _one(Map<String, dynamic> row, [int status = 200]) =>
    http.Response(jsonEncode({'success': true, 'data': row}), status);
Map<String, dynamic> _unread({int clinic = 3, int? last, int count = 2}) => {
  'conversation_id': 10,
  'clinic_id': clinic,
  'user_id': 42,
  'last_read_message_id': last,
  'unread_count': count,
};
Map<String, dynamic> _page(List<Map<String, dynamic>> items) => {
  'success': true,
  'data': items,
  'page': 1,
  'per_page': 100,
  'total': items.length,
  'pages': items.isEmpty ? 0 : 1,
  'has_next': false,
  'has_previous': false,
};

ClinicChatFinalEngagementRemoteDataSource _adapter(
  MockClient client,
  SessionNavigationGuard guard,
) => ClinicChatFinalEngagementRemoteDataSource(
  read: ClinicChatReadRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    sessionNavigationGuard: guard,
    loadSession: () async => const AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'chat-token',
        refreshToken: 'chat-refresh',
        userId: 42,
        role: 'doctor',
        clinicContextId: 3,
      ),
    ),
  ),
);

void main() {
  test(
    'authoritative unread from backend; no derived counter or mutation',
    () async {
      final methods = <String>[];
      final client = MockClient((req) async {
        methods.add(req.method);
        if (req.url.path.endsWith('/unread')) return _one(_unread());
        return _one(_room());
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final result = await _adapter(client, guard).loadUnread(10);
      expect(result.unreadCount, 2);
      expect(result.lastReadMessageId, isNull);
      expect(methods, ['GET', 'GET']);
    },
  );

  test('foreign clinic unread response is rejected', () async {
    final client = MockClient(
      (req) async => req.url.path.endsWith('/unread')
          ? _one(_unread(clinic: 6))
          : _one(_room()),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _adapter(client, guard).loadUnread(10),
      throwsFormatException,
    );
  });

  test('explicit mark read POST once and authoritative readback', () async {
    var posts = 0;
    final client = MockClient((req) async {
      if (req.url.path.endsWith('/read')) {
        expect(req.method, 'POST');
        expect(jsonDecode(req.body), {'last_read_message_id': 50});
        posts++;
        return _one({
          'clinic_id': 3,
          'conversation_id': 10,
          'last_read_message_id': 50,
        });
      }
      if (req.url.path.endsWith('/unread')) {
        return _one(_unread(last: 50, count: 0));
      }
      if (req.url.path.endsWith('/messages/50')) return _one(_message());
      return _one(_room());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _adapter(client, guard);
    final result = await source.acknowledge(10, 50);
    expect(result.lastReadMessageId, 50);
    expect(result.unreadCount, 0);
    expect(posts, 1);
    expect(source.outcomeUnconfirmed, isFalse);
  });

  test('reactions and mentions use typed verified server readbacks', () async {
    var reactions = 0;
    var mentions = 0;
    final client = MockClient((req) async {
      if (req.url.path.endsWith('/messages/50')) return _one(_message());
      if (req.url.path.endsWith('/reactions')) {
        if (req.method == 'POST') {
          reactions++;
          expect(jsonDecode(req.body), {'reaction': 'ack'});
          return _one({'id': 7, 'message_id': 50, 'reaction': 'ack'});
        }
        return http.Response(
          jsonEncode(
            _page([
              {
                'id': 7,
                'message_id': 50,
                'clinic_id': 3,
                'user_id': 42,
                'reaction': 'ack',
              },
            ]),
          ),
          200,
        );
      }
      if (req.url.path.endsWith('/mentions')) {
        if (req.method == 'POST') {
          mentions++;
          expect(jsonDecode(req.body), {'mentioned_user_id': 77});
          return _one({
            'id': 8,
            'message_id': 50,
            'mentioned_user_id': 77,
          }, 201);
        }
        return http.Response(
          jsonEncode(
            _page([
              {
                'id': 8,
                'message_id': 50,
                'clinic_id': 3,
                'mention_type': 'user',
                'mentioned_user_id': 77,
              },
            ]),
          ),
          200,
        );
      }
      return _one(_room());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _adapter(client, guard);
    expect((await source.react(10, 50, 'ack')).single.reaction, 'ack');
    expect((await source.mentionUser(10, 50, 77)).single.userId, 77);
    expect(reactions, 1);
    expect(mentions, 1);
  });

  test('uncertain write locks further writes and never auto retries', () async {
    var posts = 0;
    final client = MockClient((req) async {
      if (req.url.path.endsWith('/reactions') && req.method == 'POST') {
        posts++;
        throw StateError('response lost');
      }
      if (req.url.path.endsWith('/messages/50')) return _one(_message());
      return _one(_room());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _adapter(client, guard);
    await expectLater(source.react(10, 50, 'ack'), throwsA(anything));
    expect(source.outcomeUnconfirmed, isTrue);
    await expectLater(
      source.react(10, 50, 'ack'),
      throwsA(isA<ClinicChatFinalOutcomeUnconfirmed>()),
    );
    expect(posts, 1);
  });
}
