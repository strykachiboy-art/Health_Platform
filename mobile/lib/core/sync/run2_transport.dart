import '../networking/api_client.dart';
import '../../features/auth/data/models/auth_token_response.dart';
import 'run2_policy.dart';

/// Exact endpoints only. No arbitrary route, method or client-selected clinic.
class Run2ConversationChoice {
  const Run2ConversationChoice({required this.id, required this.label});
  final int id;
  final String label;
}

abstract class Run2Transport {
  Future<void> verifyScope(AuthTokenResponse session);
  Future<bool> checkHealth();
  Future<List<Run2ConversationChoice>> listOrdinaryChats(
    AuthTokenResponse session,
  );
  Future<void> verifyOrdinaryChat(
    AuthTokenResponse session,
    int conversationId,
  );
  Future<void> deliver(
    AuthTokenResponse session,
    String type,
    Map<String, dynamic> payload,
    String key,
  );
}

class ApiRun2Transport implements Run2Transport {
  const ApiRun2Transport(this.api);
  final ApiClient api;

  static Map<String, dynamic> data(Map<String, dynamic> envelope) {
    final value = envelope['data'];
    if (envelope['success'] != true || value is! Map<String, dynamic>) {
      throw const FormatException('Server receipt unavailable.');
    }
    return value;
  }

  @override
  Future<void> verifyScope(AuthTokenResponse s) async {
    final value = data(
      await api.getJson(
        'api/v1/auth/tenant-context',
        bearerToken: s.accessToken,
      ),
    );
    if (value['tenant_id'] != s.tenantId ||
        value['clinic_id'] != s.clinicContextId) {
      throw const FormatException('Server scope mismatch.');
    }
  }

  @override
  Future<bool> checkHealth() async {
    final value = await api.getJson('health/live');
    if (value['success'] != true || value['status'] != 'ok') {
      throw const FormatException('Backend health unconfirmed.');
    }
    return true;
  }

  static bool ordinary(Map<String, dynamic> item, AuthTokenResponse s) {
    final id = item['id'];
    return id is int &&
        id > 0 &&
        id <= 2147483647 &&
        item['clinic_id'] == s.clinicContextId &&
        const {'direct', 'group'}.contains(item['conversation_type']) &&
        item['status'] == 'active' &&
        const [
          'patient_id',
          'appointment_id',
          'consultation_id',
        ].every((key) => item.containsKey(key) && item[key] == null);
  }

  @override
  Future<List<Run2ConversationChoice>> listOrdinaryChats(
    AuthTokenResponse s,
  ) async {
    if (!Run2Policy.chatRoles.contains(Run2Policy.role(s))) {
      throw StateError('Chat not available for this role.');
    }
    final raw = await api.getJson(
      'api/v1/chat/conversations?page=1&per_page=50&status=active',
      bearerToken: s.accessToken,
    );
    final items = raw['data'];
    if (raw['success'] != true || items is! List || items.length > 50) {
      throw const FormatException('Invalid conversation page.');
    }
    final result = <Run2ConversationChoice>[];
    final seen = <int>{};
    for (final item in items) {
      if (item is! Map<String, dynamic>) {
        throw const FormatException('Invalid conversation row.');
      }
      if (!ordinary(item, s)) {
        continue;
      }
      final id = item['id'] as int;
      if (!seen.add(id)) {
        throw const FormatException('Duplicate conversation.');
      }
      final title = item['title'];
      result.add(
        Run2ConversationChoice(
          id: id,
          label:
              title is String && title.trim().isNotEmpty && title.length <= 200
              ? title
              : 'Conversation $id',
        ),
      );
    }
    return List.unmodifiable(result);
  }

  @override
  Future<void> verifyOrdinaryChat(AuthTokenResponse s, int id) async {
    if (!Run2Policy.chatRoles.contains(Run2Policy.role(s)) ||
        id <= 0 ||
        id > 2147483647) {
      throw const FormatException('Invalid ordinary conversation.');
    }
    final value = data(
      await api.getJson(
        'api/v1/chat/conversations/$id',
        bearerToken: s.accessToken,
      ),
    );
    if (!ordinary(value, s) || value['id'] != id) {
      throw const FormatException('Conversation is no longer eligible.');
    }
  }

  static int positiveId(Map<String, dynamic> row) {
    final id = row['id'];
    if (id is! int || id <= 0) {
      throw const FormatException('Invalid server record.');
    }
    return id;
  }

  static int validateFeedback(
    Map<String, dynamic> row,
    AuthTokenResponse s,
    Map<String, dynamic> body,
  ) {
    final id = positiveId(row);
    if (row['clinic_id'] != s.clinicContextId ||
        row['submitted_by_user_id'] != s.userId ||
        !body.entries.every((entry) => row[entry.key] == entry.value) ||
        !const [
          'target_module',
          'target_resource_type',
          'target_resource_id',
        ].every((key) => row.containsKey(key) && row[key] == null)) {
      throw const FormatException('Feedback receipt mismatch.');
    }
    return id;
  }

  static int validateChat(
    Map<String, dynamic> row,
    AuthTokenResponse s,
    int conversationId,
    Map<String, dynamic> body,
  ) {
    final id = positiveId(row);
    if (row['clinic_id'] != s.clinicContextId ||
        row['sender_id'] != s.userId ||
        row['conversation_id'] != conversationId ||
        !body.entries.every((entry) => row[entry.key] == entry.value) ||
        !row.containsKey('reply_to_message_id') ||
        row['reply_to_message_id'] != null ||
        !const {'pending', 'sent', 'failed'}.contains(row['status'])) {
      throw const FormatException('Chat receipt mismatch.');
    }
    // A persisted server message is not a recipient delivery receipt.
    return id;
  }

  @override
  Future<void> deliver(
    AuthTokenResponse s,
    String type,
    Map<String, dynamic> p,
    String key,
  ) async {
    Run2Policy.validate(type, p, Run2Policy.role(s));
    if (!RegExp(r'^run2_[0-9a-f]{48}$').hasMatch(key)) {
      throw const FormatException('Invalid operation key.');
    }
    if (type == Run2Policy.feedback) {
      final body = Run2Policy.feedbackBody(p['preset'] as String);
      final row = data(
        await api.postJson(
          'api/v1/feedback',
          body: body,
          bearerToken: s.accessToken,
          idempotencyKey: key,
        ),
      );
      final id = validateFeedback(row, s, body);
      final confirmed = data(
        await api.getJson('api/v1/feedback/$id', bearerToken: s.accessToken),
      );
      if (validateFeedback(confirmed, s, body) != id) {
        throw const FormatException('Feedback readback mismatch.');
      }
    } else if (type == Run2Policy.chat) {
      final conversation = p['conversation_id'] as int;
      final body = Run2Policy.chatBody(p['preset'] as String);
      final row = data(
        await api.postJson(
          'api/v1/chat/conversations/$conversation/messages',
          body: body,
          bearerToken: s.accessToken,
          idempotencyKey: key,
        ),
      );
      final id = validateChat(row, s, conversation, body);
      final confirmed = data(
        await api.getJson(
          'api/v1/chat/messages/$id',
          bearerToken: s.accessToken,
        ),
      );
      if (validateChat(confirmed, s, conversation, body) != id) {
        throw const FormatException('Chat readback mismatch.');
      }
    } else {
      throw const FormatException('No delivery adapter.');
    }
  }
}
