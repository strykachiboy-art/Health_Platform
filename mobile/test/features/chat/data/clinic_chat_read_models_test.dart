import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/features/chat/data/models/clinic_chat_read_models.dart';

Map<String, dynamic> conversation({
  int id = 10,
  int clinic = 3,
  String type = 'direct',
  String status = 'active',
  int? patient,
}) => {
  'id': id,
  'clinic_id': clinic,
  'conversation_type': type,
  'status': status,
  'title': 'Care conversation',
  'description': null,
  'created_by_id': 5,
  'patient_id': patient,
  'last_message_at': '2026-11-08T10:00:00+00:00',
  'created_at': '2026-11-08T09:00:00Z',
};

Map<String, dynamic> message({
  int id = 20,
  int clinic = 3,
  int convo = 10,
  String type = 'text',
  String status = 'sent',
  String? content = 'Report received',
}) => {
  'id': id,
  'clinic_id': clinic,
  'conversation_id': convo,
  'sender_id': 5,
  'message_type': type,
  'content': content,
  'reply_to_message_id': null,
  'status': status,
  'priority': 'normal',
  'created_at': '2026-11-08T10:00:00+00:00',
};

Map<String, dynamic> page(List<Object> data, {int total = 1}) => {
  'success': true,
  'data': data,
  'page': 1,
  'per_page': 25,
  'total': total,
  'pages': total == 0 ? 0 : (total + 24) ~/ 25,
  'has_next': false,
  'has_previous': false,
};

void main() {
  test('direct and linked patient conversations parse in clinic', () {
    final direct = ClinicChatConversation.fromJson(
      conversation(),
      expectedClinicId: 3,
    );
    expect(direct.id, 10);
    expect(direct.kind, ClinicChatKind.direct);
    final patient = ClinicChatConversation.fromJson(
      conversation(type: 'patient', patient: 7),
      expectedClinicId: 3,
    );
    expect(patient.kind, ClinicChatKind.patient);
    expect(patient.patientId, 7);
  });

  test('cross-clinic and incomplete case context fail closed', () {
    expect(
      () => ClinicChatConversation.fromJson(
        conversation(clinic: 4),
        expectedClinicId: 3,
      ),
      throwsFormatException,
    );
    expect(
      () => ClinicChatConversation.fromJson(
        conversation(type: 'patient'),
        expectedClinicId: 3,
      ),
      throwsFormatException,
    );
  });

  test('unknown conversation enum and timezone-free timestamp rejected', () {
    expect(
      () => ClinicChatConversation.fromJson(
        conversation(type: 'secret'),
        expectedClinicId: 3,
      ),
      throwsFormatException,
    );
    final raw = conversation();
    raw['created_at'] = '2026-11-08T10:00:00';
    expect(
      () => ClinicChatConversation.fromJson(raw, expectedClinicId: 3),
      throwsFormatException,
    );
  });

  test('message content, scope and status parse strictly', () {
    final read = ClinicChatMessage.fromJson(
      message(),
      expectedClinicId: 3,
      expectedConversationId: 10,
    );
    expect(read.id, 20);
    expect(read.safePreview, 'Report received');
    expect(read.priority, ClinicMessagePriority.normal);
    expect(
      () => ClinicChatMessage.fromJson(
        message(convo: 11),
        expectedClinicId: 3,
        expectedConversationId: 10,
      ),
      throwsFormatException,
    );
    expect(
      () => ClinicChatMessage.fromJson(message(clinic: 4), expectedClinicId: 3),
      throwsFormatException,
    );
  });

  test('deleted content and unsupported media never show original text', () {
    final deleted = ClinicChatMessage.fromJson(
      message(status: 'deleted', content: 'Do not display'),
      expectedClinicId: 3,
    );
    expect(deleted.safePreview, 'Message deleted');
    final media = ClinicChatMessage.fromJson(
      message(type: 'image', content: 'Hidden media'),
      expectedClinicId: 3,
    );
    expect(media.safePreview, 'Unsupported media message');
    expect(
      () =>
          ClinicChatMessage.fromJson(message(content: ''), expectedClinicId: 3),
      throwsFormatException,
    );
  });

  test('backend page envelope parsed with metadata', () {
    final parsed = ClinicChatPage.parse<ClinicChatConversation>(
      page([conversation()]),
      (item) => ClinicChatConversation.fromJson(item, expectedClinicId: 3),
    );
    expect(parsed.items.single.id, 10);
    expect(parsed.total, 1);
    expect(parsed.hasNext, isFalse);
    expect(parsed.hasPrevious, isFalse);
  });

  test('inconsistent pagination or success marker rejected', () {
    final bad = page([conversation()]);
    bad['has_next'] = true;
    expect(
      () => ClinicChatPage.parse<ClinicChatConversation>(
        bad,
        (item) => ClinicChatConversation.fromJson(item, expectedClinicId: 3),
      ),
      throwsFormatException,
    );
    final failure = page([conversation()]);
    failure['success'] = false;
    expect(
      () => ClinicChatPage.parse<ClinicChatConversation>(
        failure,
        (item) => ClinicChatConversation.fromJson(item, expectedClinicId: 3),
      ),
      throwsFormatException,
    );
  });
}
