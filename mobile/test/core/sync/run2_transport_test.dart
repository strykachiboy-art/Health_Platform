import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/sync/run2_policy.dart';
import 'package:clinic_system_pro/core/sync/run2_transport.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const actor = AuthTokenResponse(
  accessToken: 'test-access',
  refreshToken: 'test-refresh',
  userId: 7,
  role: 'doctor',
  clinicContextId: 3,
  tenantId: '12345678-1234-1234-1234-123456789abc',
);
Map<String, dynamic> ordinary() => {
  'id': 11,
  'clinic_id': 3,
  'conversation_type': 'group',
  'status': 'active',
  'patient_id': null,
  'appointment_id': null,
  'consultation_id': null,
};
Map<String, dynamic> feedback() => {
  'id': 22,
  'clinic_id': 3,
  'submitted_by_user_id': 7,
  'target_module': null,
  'target_resource_type': null,
  'target_resource_id': null,
  ...Run2Policy.feedbackBody('navigation'),
};
Map<String, dynamic> chat() => {
  'id': 33,
  'clinic_id': 3,
  'sender_id': 7,
  'conversation_id': 11,
  'reply_to_message_id': null,
  'status': 'pending',
  ...Run2Policy.chatBody('thanks'),
};

void main() {
  test(
    'feedback POST uses exact stable key and authoritative readback',
    () async {
      final calls = <String>[];
      final key = 'run2_${List.filled(48, 'a').join()}';
      final client = MockClient((request) async {
        calls.add('${request.method} ${request.url.path}');
        expect(request.headers['authorization'], 'Bearer test-access');
        if (request.method == 'POST') {
          expect(request.headers['idempotency-key'], key);
          expect(
            jsonDecode(request.body),
            Run2Policy.feedbackBody('navigation'),
          );
        }
        return http.Response(
          jsonEncode({'success': true, 'data': feedback()}),
          request.method == 'POST' ? 201 : 200,
        );
      });
      addTearDown(client.close);
      final transport = ApiRun2Transport(
        ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
      );
      await transport.deliver(actor, Run2Policy.feedback, {
        'role': 'doctor',
        'captured_at': '2026-11-26T12:00:00.000Z',
        'preset': 'navigation',
      }, key);
      expect(calls, ['POST /api/v1/feedback', 'GET /api/v1/feedback/22']);
    },
  );
  test(
    'chat preflight and keyed mutation use only the exact conversation',
    () async {
      final calls = <String>[];
      final key = 'run2_${List.filled(48, 'b').join()}';
      final client = MockClient((request) async {
        calls.add('${request.method} ${request.url.path}');
        expect(request.headers['authorization'], 'Bearer test-access');
        if (request.method == 'POST') {
          expect(request.headers['idempotency-key'], key);
          expect(jsonDecode(request.body), Run2Policy.chatBody('thanks'));
        }
        final row = request.url.path.endsWith('/conversations/11')
            ? ordinary()
            : chat();
        return http.Response(
          jsonEncode({'success': true, 'data': row}),
          request.method == 'POST' ? 201 : 200,
        );
      });
      addTearDown(client.close);
      final transport = ApiRun2Transport(
        ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
      );
      await transport.verifyOrdinaryChat(actor, 11);
      await transport.deliver(actor, Run2Policy.chat, {
        'role': 'doctor',
        'captured_at': '2026-11-26T12:00:00.000Z',
        'conversation_id': 11,
        'preset': 'thanks',
      }, key);
      expect(calls, [
        'GET /api/v1/chat/conversations/11',
        'POST /api/v1/chat/conversations/11/messages',
        'GET /api/v1/chat/messages/33',
      ]);
    },
  );
  test('malformed feedback readback never confirms the mutation', () async {
    final client = MockClient(
      (request) async => http.Response(
        jsonEncode({
          'success': true,
          'data': request.method == 'POST'
              ? feedback()
              : {...feedback(), 'id': 99},
        }),
        request.method == 'POST' ? 201 : 200,
      ),
    );
    addTearDown(client.close);
    final transport = ApiRun2Transport(
      ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
    );
    await expectLater(
      transport.deliver(actor, Run2Policy.feedback, {
        'role': 'doctor',
        'captured_at': '2026-11-26T12:00:00.000Z',
        'preset': 'navigation',
      }, 'run2_${List.filled(48, 'c').join()}'),
      throwsFormatException,
    );
  });
  test('ordinary direct and group chats only, exact clinic and explicit null clinical links', () {
    expect(ApiRun2Transport.ordinary(ordinary(), actor), isTrue);
    expect(
      ApiRun2Transport.ordinary({
        ...ordinary(),
        'conversation_type': 'direct',
      }, actor),
      isTrue,
    );
    for (final key in ['patient_id', 'appointment_id', 'consultation_id']) {
      expect(
        ApiRun2Transport.ordinary({...ordinary(), key: 1}, actor),
        isFalse,
      );
      expect(
        ApiRun2Transport.ordinary(ordinary()..remove(key), actor),
        isFalse,
      );
    }
    for (final value in ['patient', 'department', 'team']) {
      expect(
        ApiRun2Transport.ordinary({
          ...ordinary(),
          'conversation_type': value,
        }, actor),
        isFalse,
      );
    }
    expect(
      ApiRun2Transport.ordinary({...ordinary(), 'clinic_id': 4}, actor),
      isFalse,
    );
    expect(
      ApiRun2Transport.ordinary({...ordinary(), 'status': 'closed'}, actor),
      isFalse,
    );
  });
  test('feedback requires exact actor, clinic, immutable body and untargeted response', () {
    final body = Run2Policy.feedbackBody('navigation');
    expect(ApiRun2Transport.validateFeedback(feedback(), actor, body), 22);
    for (final change in <Map<String, dynamic>>[
      {'id': 0},
      {'clinic_id': 4},
      {'submitted_by_user_id': 8},
      {'message': 'different'},
      {'target_module': 'patient'},
    ]) {
      expect(
        () => ApiRun2Transport.validateFeedback(
          {...feedback(), ...change},
          actor,
          body,
        ),
        throwsFormatException,
      );
    }
  });
  test(
    'chat requires exact sender and conversation, no reply or changed message',
    () {
      final body = Run2Policy.chatBody('thanks');
      expect(ApiRun2Transport.validateChat(chat(), actor, 11, body), 33);
      for (final change in <Map<String, dynamic>>[
        {'id': 0},
        {'clinic_id': 4},
        {'sender_id': 8},
        {'conversation_id': 12},
        {'content': 'changed'},
        {'reply_to_message_id': 1},
        {'priority': 'urgent'},
        {'status': 'deleted'},
      ]) {
        expect(
          () => ApiRun2Transport.validateChat(
            {...chat(), ...change},
            actor,
            11,
            body,
          ),
          throwsFormatException,
        );
      }
    },
  );
  test('malformed or unsuccessful server envelopes fail closed', () {
    expect(
      () => ApiRun2Transport.data({'success': false, 'data': feedback()}),
      throwsFormatException,
    );
    expect(
      () => ApiRun2Transport.data({'success': true, 'data': []}),
      throwsFormatException,
    );
    expect(
      ApiRun2Transport.data({'success': true, 'data': feedback()}),
      feedback(),
    );
  });
  test('presets have no custom content or resource targets', () {
    for (final preset in Run2Policy.feedbackPresets.keys) {
      expect(Run2Policy.feedbackBody(preset).keys.toSet(), {
        'feedback_type',
        'category',
        'subject',
        'message',
      });
    }
    for (final preset in Run2Policy.chatPresets.keys) {
      expect(Run2Policy.chatBody(preset).keys.toSet(), {
        'message_type',
        'priority',
        'content',
      });
    }
    expect(
      () => Run2Policy.chatBody('urgent clinical message'),
      throwsFormatException,
    );
    expect(
      () => Run2Policy.feedbackBody('private patient note'),
      throwsFormatException,
    );
  });
}
