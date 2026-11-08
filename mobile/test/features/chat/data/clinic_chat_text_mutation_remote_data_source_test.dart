import 'dart:async';
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
import 'package:clinic_system_pro/features/chat/data/clinic_chat_text_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/models/clinic_chat_read_models.dart';

Map<String, dynamic> _conversation({
  String status = 'active',
  int clinic = 3,
}) => {
  'id': 10,
  'clinic_id': clinic,
  'conversation_type': 'direct',
  'status': status,
  'title': 'Team channel',
  'description': null,
  'created_by_id': 42,
  'patient_id': null,
  'last_message_at': null,
  'created_at': '2026-11-08T10:00:00Z',
};

Map<String, dynamic> _message({
  int id = 50,
  int sender = 42,
  String text = 'Approved clinical message',
  String status = 'pending',
  String priority = 'normal',
  int? reply,
  int clinic = 3,
  int conversationId = 10,
}) => {
  'id': id,
  'clinic_id': clinic,
  'conversation_id': conversationId,
  'sender_id': sender,
  'message_type': 'text',
  'content': text,
  'reply_to_message_id': reply,
  'status': status,
  'priority': priority,
  'created_at': '2026-11-08T11:00:00Z',
};

http.Response _ok(Map<String, dynamic> data, [int code = 200]) =>
    http.Response(jsonEncode({'success': true, 'data': data}), code);

ClinicChatReadRemoteDataSource _read(
  MockClient client,
  SessionNavigationGuard guard, {
  int clinic = 3,
  int user = 42,
}) => ClinicChatReadRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => AuthSessionState.ready(
    AuthTokenResponse(
      accessToken: 'test-chat-token',
      refreshToken: 'test-refresh',
      userId: user,
      role: 'nurse',
      clinicContextId: clinic,
    ),
  ),
  sessionNavigationGuard: guard,
);

ClinicChatConversation _reviewed() =>
    ClinicChatConversation.fromJson(_conversation(), expectedClinicId: 3);

ClinicChatMessage _selected({int sender = 42}) => ClinicChatMessage.fromJson(
  _message(sender: sender, status: 'sent'),
  expectedClinicId: 3,
  expectedConversationId: 10,
);

void main() {
  test(
    'text POST once, authenticated readback, no idempotency header',
    () async {
      var posts = 0;
      var readbacks = 0;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          posts++;
          expect(req.url.path, '/api/v1/chat/conversations/10/messages');
          expect(req.headers['authorization'], 'Bearer test-chat-token');
          expect(req.headers.containsKey('idempotency-key'), isFalse);
          expect(jsonDecode(req.body), {
            'message_type': 'text',
            'content': 'Approved clinical message',
            'priority': 'urgent',
          });
          return _ok(_message(priority: 'urgent'), 201);
        }
        expect(req.method, 'GET');
        if (req.url.path.endsWith('/conversations/10')) {
          return _ok(_conversation());
        }
        if (req.url.path.endsWith('/messages/50')) {
          readbacks++;
          return _ok(_message(status: 'sent', priority: 'urgent'));
        }
        throw StateError('Unexpected request: ${req.url}');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final adapter = ClinicChatTextMutationRemoteDataSource(
        read: _read(client, guard),
      );
      final result = await adapter.sendText(
        reviewed: _reviewed(),
        content: ' Approved clinical message ',
        priority: ClinicMessagePriority.urgent,
      );
      expect(result.status, ClinicMessageStatus.sent);
      expect(posts, 1);
      expect(readbacks, 1);
      expect(adapter.writeLocked, isFalse);
    },
  );

  test(
    'reply checks original message and sends correct referenced ID',
    () async {
      var writes = 0;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          expect(jsonDecode(req.body)['reply_to_message_id'], 90);
          return _ok(_message(id: 51, text: 'Reply', reply: 90), 201);
        }
        if (req.url.path.endsWith('/conversations/10')) {
          return _ok(_conversation());
        }
        if (req.url.path.endsWith('/messages/90')) {
          return _ok(_message(id: 90, text: 'Original', status: 'sent'));
        }
        if (req.url.path.endsWith('/messages/51')) {
          return _ok(_message(id: 51, text: 'Reply', reply: 90));
        }
        throw StateError('Unexpected GET');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final adapter = ClinicChatTextMutationRemoteDataSource(
        read: _read(client, guard),
      );
      final result = await adapter.sendText(
        reviewed: _reviewed(),
        content: 'Reply',
        replyToMessageId: 90,
      );
      expect(result.replyToMessageId, 90);
      expect(writes, 1);
    },
  );

  test(
    'edit PATCH checks author and current server text, confirms edited',
    () async {
      var patches = 0;
      final client = MockClient((req) async {
        if (req.method == 'PATCH') {
          patches++;
          expect(req.url.path, '/api/v1/chat/messages/50');
          expect(req.headers.containsKey('idempotency-key'), isFalse);
          expect(jsonDecode(req.body), {'content': 'Corrected text'});
          return _ok(_message(status: 'edited', text: 'Corrected text'));
        }
        if (req.url.path.endsWith('/conversations/10')) {
          return _ok(_conversation());
        }
        return _ok(
          _message(
            status: patches == 0 ? 'sent' : 'edited',
            text: patches == 0 ? 'Approved clinical message' : 'Corrected text',
          ),
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final adapter = ClinicChatTextMutationRemoteDataSource(
        read: _read(client, guard),
      );
      final result = await adapter.editText(
        reviewed: _reviewed(),
        selected: _selected(),
        content: 'Corrected text',
      );
      expect(result.status, ClinicMessageStatus.edited);
      expect(result.content, 'Corrected text');
      expect(patches, 1);
    },
  );

  test(
    'delete requires confirmation result and server deleted readback',
    () async {
      var deletes = 0;
      final client = MockClient((req) async {
        if (req.method == 'DELETE') {
          deletes++;
          expect(req.url.path, '/api/v1/chat/messages/50');
          return _ok(_message(status: 'deleted'));
        }
        if (req.url.path.endsWith('/conversations/10')) {
          return _ok(_conversation());
        }
        return _ok(_message(status: deletes == 0 ? 'sent' : 'deleted'));
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final adapter = ClinicChatTextMutationRemoteDataSource(
        read: _read(client, guard),
      );
      final result = await adapter.deleteText(
        reviewed: _reviewed(),
        selected: _selected(),
      );
      expect(result.status, ClinicMessageStatus.deleted);
      expect(deletes, 1);
    },
  );

  test('refuses editing someone else before any write', () async {
    var mutations = 0;
    final client = MockClient((req) async {
      if (req.method != 'GET') {
        mutations++;
      }
      return _ok(_conversation());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final adapter = ClinicChatTextMutationRemoteDataSource(
      read: _read(client, guard),
    );
    await expectLater(
      adapter.editText(
        reviewed: _reviewed(),
        selected: _selected(sender: 999),
        content: 'Other author content',
      ),
      throwsA(isA<ClinicChatTextPreflightChanged>()),
    );
    expect(mutations, 0);
  });

  test('stale/closed server conversation refuses POST', () async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method != 'GET') {
        writes++;
      }
      return _ok(_conversation(status: 'archived'));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final adapter = ClinicChatTextMutationRemoteDataSource(
      read: _read(client, guard),
    );
    await expectLater(
      adapter.sendText(reviewed: _reviewed(), content: 'Blocked'),
      throwsA(isA<ClinicChatTextPreflightChanged>()),
    );
    expect(writes, 0);
  });

  test('clinic mismatch refuses write before POST', () async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method != 'GET') {
        writes++;
      }
      return _ok(_conversation(clinic: 8));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final adapter = ClinicChatTextMutationRemoteDataSource(
      read: _read(client, guard),
    );
    await expectLater(
      adapter.sendText(reviewed: _reviewed(), content: 'Blocked'),
      throwsFormatException,
    );
    expect(writes, 0);
  });

  test('lost POST response locks permanently with no replay', () async {
    var posts = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        posts++;
        throw http.ClientException('lost response');
      }
      return _ok(_conversation());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final adapter = ClinicChatTextMutationRemoteDataSource(
      read: _read(client, guard),
    );
    await expectLater(
      adapter.sendText(reviewed: _reviewed(), content: 'Potential duplicate'),
      throwsA(isA<ClinicChatTextOutcomeUnconfirmed>()),
    );
    expect(adapter.outcomeUnconfirmed, isTrue);
    await expectLater(
      adapter.sendText(reviewed: _reviewed(), content: 'Potential duplicate'),
      throwsA(isA<ClinicChatTextWriteLocked>()),
    );
    expect(posts, 1);
  });

  test('malformed successful POST locks, never retries', () async {
    var posts = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        posts++;
        return _ok(_message(clinic: 99), 201);
      }
      return _ok(_conversation());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final adapter = ClinicChatTextMutationRemoteDataSource(
      read: _read(client, guard),
    );
    await expectLater(
      adapter.sendText(reviewed: _reviewed(), content: 'Potential duplicate'),
      throwsA(isA<ClinicChatTextOutcomeUnconfirmed>()),
    );
    expect(posts, 1);
    expect(adapter.writeLocked, isTrue);
  });

  test('after verified readback a distinct new message is permitted', () async {
    var posts = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        posts++;
        final content =
            (jsonDecode(req.body) as Map<String, dynamic>)['content'] as String;
        return _ok(_message(id: 50 + posts, text: content), 201);
      }
      if (req.url.path.endsWith('/conversations/10')) {
        return _ok(_conversation());
      }
      final id = int.parse(req.url.path.split('/').last);
      return _ok(_message(id: id, text: id == 51 ? 'First' : 'Second'));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final adapter = ClinicChatTextMutationRemoteDataSource(
      read: _read(client, guard),
    );
    await adapter.sendText(reviewed: _reviewed(), content: 'First');
    await adapter.sendText(reviewed: _reviewed(), content: 'Second');
    expect(posts, 2);
    expect(adapter.outcomeUnconfirmed, isFalse);
  });

  test('concurrent preflight cannot create a second text POST', () async {
    final holdFirstRead = Completer<void>();
    var conversationReads = 0;
    var posts = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        posts++;
        return _ok(_message(id: 81, text: 'Only once'), 201);
      }
      if (req.url.path.endsWith('/conversations/10')) {
        conversationReads++;
        if (conversationReads == 1) {
          await holdFirstRead.future;
        }
        return _ok(_conversation());
      }
      if (req.url.path.endsWith('/messages/81')) {
        return _ok(_message(id: 81, text: 'Only once'));
      }
      throw StateError('Unexpected request');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final adapter = ClinicChatTextMutationRemoteDataSource(
      read: _read(client, guard),
    );
    final first = adapter.sendText(reviewed: _reviewed(), content: 'Only once');
    await Future<void>.delayed(Duration.zero);
    await expectLater(
      adapter.sendText(reviewed: _reviewed(), content: 'Only once'),
      throwsA(isA<ClinicChatTextWriteLocked>()),
    );
    holdFirstRead.complete();
    await first;
    expect(posts, 1);
    expect(adapter.writeLocked, isFalse);
  });

  test('signed-out guard blocks every request', () async {
    var requests = 0;
    final client = MockClient((req) async {
      requests++;
      return _ok(_conversation());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSignedOut();
    addTearDown(guard.dispose);
    final adapter = ClinicChatTextMutationRemoteDataSource(
      read: _read(client, guard),
    );
    await expectLater(
      adapter.sendText(reviewed: _reviewed(), content: 'Blocked'),
      throwsA(isA<ClinicChatTextSessionUnavailable>()),
    );
    expect(requests, 0);
  });
}
