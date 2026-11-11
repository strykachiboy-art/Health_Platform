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
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_state_remote_data_source.dart';

AuthSessionState _ready({int userId = 41, int clinicId = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'p24g-access',
        refreshToken: 'p24g-refresh',
        userId: userId,
        role: 'doctor',
        clinicContextId: clinicId,
      ),
    );

Map<String, dynamic> _conversation({int clinicId = 3}) => {
  'success': true,
  'data': {
    'id': 7,
    'clinic_id': clinicId,
    'conversation_type': 'direct',
    'status': 'active',
    'title': 'Clinical team',
    'description': null,
    'created_by_id': 41,
    'patient_id': null,
    'last_message_at': null,
    'created_at': '2026-11-10T09:00:00Z',
  },
};

Map<String, dynamic> _participant({
  int userId = 41,
  int clinicId = 3,
  Object? marker = 88,
}) => {
  'id': userId,
  'clinic_id': clinicId,
  'conversation_id': 7,
  'user_id': userId,
  'role': 'member',
  'status': 'accepted',
  'last_read_message_id': marker,
  'joined_at': '2026-11-10T09:00:00Z',
  'left_at': null,
  'removed_at': null,
  'created_at': '2026-11-10T09:00:00Z',
  'updated_at': '2026-11-10T09:00:00Z',
};

Map<String, dynamic> _participantPage({
  List<Map<String, dynamic>>? items,
  int page = 1,
  int? total,
}) {
  final rows = items ?? [_participant(userId: 98), _participant()];
  return {
    'data': rows,
    'page': page,
    'per_page': 100,
    'total': total ?? rows.length,
    'pages': 1,
    'has_next': false,
    'has_previous': page > 1,
  };
}

ClinicChatReadStateRemoteDataSource _source(
  http.Client client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? session,
}) => ClinicChatReadStateRemoteDataSource(
  read: ClinicChatReadRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: session ?? () async => _ready(),
    sessionNavigationGuard: guard,
  ),
);

void main() {
  test(
    'only own accepted read marker is returned; no writes or unread guess',
    () async {
      final calls = <String>[];
      final client = MockClient((request) async {
        calls.add('${request.method} ${request.url.path}');
        expect(request.method, 'GET');
        expect(request.headers['Authorization'], 'Bearer p24g-access');
        if (request.url.path.endsWith('/participants')) {
          expect(request.url.queryParameters['page'], '1');
          expect(request.url.queryParameters['per_page'], '100');
          return http.Response(jsonEncode(_participantPage()), 200);
        }
        expect(request.url.path, '/api/v1/chat/conversations/7');
        return http.Response(jsonEncode(_conversation()), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final state = await _source(client, guard).loadMine(7);
      expect(state.conversationId, 7);
      expect(state.lastReadMessageId, 88);
      expect(calls, [
        'GET /api/v1/chat/conversations/7',
        'GET /api/v1/chat/conversations/7/participants',
      ]);
    },
  );

  test('foreign clinic record is rejected rather than displayed', () async {
    final client = MockClient(
      (request) async => http.Response(
        jsonEncode(
          request.url.path.endsWith('/participants')
              ? _participantPage(items: [_participant(clinicId: 9)])
              : _conversation(),
        ),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).loadMine(7),
      throwsFormatException,
    );
  });

  test('malformed last-read marker fails closed', () async {
    final client = MockClient(
      (request) async => http.Response(
        jsonEncode(
          request.url.path.endsWith('/participants')
              ? _participantPage(items: [_participant(marker: '88')])
              : _conversation(),
        ),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).loadMine(7),
      throwsFormatException,
    );
  });

  test('signed-out guard refuses any network read', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).loadMine(7),
      throwsA(isA<ClinicChatSessionUnavailable>()),
    );
    expect(calls, 0);
  });

  test('session revision change after response hides old marker', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/participants')) {
        guard.invalidate();
        return http.Response(jsonEncode(_participantPage()), 200);
      }
      return http.Response(jsonEncode(_conversation()), 200);
    });
    addTearDown(client.close);
    await expectLater(
      _source(client, guard).loadMine(7),
      throwsA(isA<ClinicChatSessionUnavailable>()),
    );
  });

  test(
    'unverified owner and malformed pagination never invent read state',
    () async {
      final client = MockClient(
        (request) async => http.Response(
          jsonEncode(
            request.url.path.endsWith('/participants')
                ? _participantPage(items: [_participant(userId: 99)])
                : _conversation(),
          ),
          200,
        ),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(client, guard).loadMine(7),
        throwsFormatException,
      );
    },
  );
}
