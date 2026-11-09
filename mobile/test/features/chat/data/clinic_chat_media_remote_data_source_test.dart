import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_media_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/models/clinic_chat_read_models.dart';

const requestId = '550e8400-e29b-41d4-a716-446655440000';

Map<String, dynamic> conversation() => {
  'id': 10,
  'clinic_id': 3,
  'conversation_type': 'direct',
  'status': 'active',
  'title': 'Authorized test chat',
  'description': null,
  'created_by_id': 11,
  'patient_id': null,
  'last_message_at': null,
  'created_at': '2026-11-09T00:00:00Z',
};

Map<String, dynamic> message() => {
  'id': 71,
  'clinic_id': 3,
  'conversation_id': 10,
  'sender_id': 11,
  'message_type': 'image',
  'content': null,
  'reply_to_message_id': null,
  'status': 'pending',
  'priority': 'normal',
  'created_at': '2026-11-09T00:00:00Z',
  'attachments': [
    {
      'id': 31,
      'attachment_type': 'image',
      'file_name': 'photo.png',
      'mime_type': 'image/png',
      'file_size_bytes': 8,
      'created_at': '2026-11-09T00:00:00Z',
    },
  ],
};

ClinicChatReadRemoteDataSource read(
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
      accessToken: 'synthetic-token',
      refreshToken: 'refresh',
      userId: 11,
      role: 'nurse',
      clinicContextId: 3,
    ),
  ),
  sessionNavigationGuard: guard,
);

void main() {
  test('media metadata is strictly scoped to the canonical message', () {
    final item = ClinicChatMessage.fromJson(
      message(),
      expectedClinicId: 3,
      expectedConversationId: 10,
    );
    expect(item.attachments.single.id, 31);
    expect(item.kind, ClinicMessageKind.image);
    expect(item.safePreview, 'Image attachment');
    final bad = message();
    final attachment =
        (bad['attachments'] as List).single as Map<String, Object>;
    attachment['id'] = -1;
    expect(
      () => ClinicChatMessage.fromJson(bad, expectedClinicId: 3),
      throwsFormatException,
    );
  });

  test(
    'uploads exactly once, verifies canonical readback and no public URL',
    () async {
      final requests = <String>[];
      final client = MockClient((req) async {
        requests.add('${req.method} ${req.url.path}');
        if (req.method == 'GET' && req.url.path.endsWith('/conversations/10')) {
          return http.Response(
            jsonEncode({'success': true, 'data': conversation()}),
            200,
          );
        }
        if (req.method == 'POST' &&
            req.url.path.endsWith('/conversations/10/media')) {
          expect(req.headers['authorization'], 'Bearer synthetic-token');
          expect(req.headers['content-type'], contains('multipart/form-data'));
          return http.Response(
            jsonEncode({'success': true, 'data': message()}),
            201,
          );
        }
        if (req.method == 'GET' && req.url.path.endsWith('/messages/71')) {
          return http.Response(
            jsonEncode({'success': true, 'data': message()}),
            200,
          );
        }
        throw StateError('Unexpected request ${req.url}');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = ClinicChatMediaRemoteDataSource(
        read: read(client, guard),
        client: client,
      );
      final saved = await source.upload(
        conversationId: 10,
        requestId: requestId,
        selection: ClinicChatMediaSelection(
          kind: 'image',
          name: 'photo.png',
          mime: 'image/png',
          bytes: Uint8List.fromList([137, 80, 78, 71, 13, 10, 26, 10]),
        ),
      );
      expect(saved.id, 71);
      expect(saved.attachments.single.id, 31);
      expect(requests.where((s) => s.startsWith('POST ')).length, 1);
      expect(source.outcomeUnconfirmed, false);
      source.dispose();
    },
  );

  test(
    'guard blocks media and size/type validation rejects unsupported bytes',
    () async {
      final client = MockClient(
        (req) async => throw StateError('No HTTP expected'),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = ClinicChatMediaRemoteDataSource(
        read: read(client, guard),
        client: client,
      );
      expect(
        () => ClinicChatMediaRemoteDataSource.validate(
          ClinicChatMediaSelection(
            kind: 'image',
            name: 'bad.svg',
            mime: 'image/svg+xml',
            bytes: Uint8List(10),
          ),
        ),
        throwsFormatException,
      );
      guard.invalidate();
      await expectLater(
        source.upload(
          conversationId: 10,
          requestId: requestId,
          selection: ClinicChatMediaSelection(
            kind: 'image',
            name: 'ok.png',
            mime: 'image/png',
            bytes: Uint8List(8),
          ),
        ),
        throwsA(isA<ClinicChatSessionUnavailable>()),
      );
      source.dispose();
    },
  );

  test(
    'unconfirmed upload locks further writes instead of auto-retrying',
    () async {
      var count = 0;
      final client = MockClient((req) async {
        if (req.method == 'GET') {
          return http.Response(
            jsonEncode({'success': true, 'data': conversation()}),
            200,
          );
        }
        count++;
        throw http.ClientException('synthetic connection loss');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = ClinicChatMediaRemoteDataSource(
        read: read(client, guard),
        client: client,
      );
      Future<void> attempt() async {
        await source.upload(
          conversationId: 10,
          requestId: requestId,
          selection: ClinicChatMediaSelection(
            kind: 'image',
            name: 'photo.png',
            mime: 'image/png',
            bytes: Uint8List(8),
          ),
        );
      }

      await expectLater(
        attempt(),
        throwsA(isA<ClinicChatMediaOutcomeUnconfirmed>()),
      );
      expect(source.outcomeUnconfirmed, true);
      await expectLater(attempt(), throwsStateError);
      expect(count, 1);
      source.dispose();
    },
  );
}
