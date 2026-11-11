import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/models/clinic_chat_read_models.dart';

class ClinicChatFinalOutcomeUnconfirmed implements Exception {
  const ClinicChatFinalOutcomeUnconfirmed();
  @override
  String toString() => 'Chat outcome unconfirmed; refresh server state.';
}

class ClinicChatAuthoritativeUnread {
  const ClinicChatAuthoritativeUnread({
    required this.conversationId,
    required this.userId,
    required this.lastReadMessageId,
    required this.unreadCount,
  });
  final int conversationId;
  final int userId;
  final int? lastReadMessageId;
  final int unreadCount;
}

class ClinicChatReactionRecord {
  const ClinicChatReactionRecord({
    required this.id,
    required this.messageId,
    required this.userId,
    required this.reaction,
  });
  final int id;
  final int messageId;
  final int userId;
  final String reaction;
}

class ClinicChatMentionRecord {
  const ClinicChatMentionRecord({
    required this.id,
    required this.messageId,
    required this.userId,
  });
  final int id;
  final int messageId;
  final int userId;
}

/// Explicit authenticated online-only controls; no optimistic state, queue or
/// automatic request retry. Any uncertain HTTP write permanently locks writes
/// on this adapter until a new, user-reviewed screen is opened.
class ClinicChatFinalEngagementRemoteDataSource {
  ClinicChatFinalEngagementRemoteDataSource({required this.read});
  final ClinicChatReadRemoteDataSource read;
  bool _busy = false;
  bool _uncertain = false;
  bool get writeLocked => _busy || _uncertain;
  bool get outcomeUnconfirmed => _uncertain;

  void _id(int value, String name) {
    if (value < 1 || value > 2147483647) {
      throw ArgumentError.value(value, name);
    }
  }

  Future<AuthTokenResponse> _session(int revision) async {
    if (!read.sessionNavigationGuard.canAccessProtectedRoutes ||
        revision != read.sessionNavigationGuard.revision) {
      throw const ClinicChatSessionUnavailable();
    }
    final state = await read.loadSession();
    final session = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.userId < 1 ||
        session.clinicContextId == null ||
        session.clinicContextId! < 1 ||
        session.accessToken.trim().isEmpty ||
        session.accessToken.trim() != session.accessToken ||
        session.refreshToken.trim().isEmpty ||
        session.role.trim().isEmpty ||
        !read.sessionNavigationGuard.canAccessProtectedRoutes ||
        revision != read.sessionNavigationGuard.revision) {
      throw const ClinicChatSessionUnavailable();
    }
    return session;
  }

  bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> _verify(
    AuthTokenResponse before,
    int revision,
  ) async {
    final after = await _session(revision);
    if (!_same(before, after)) throw const ClinicChatSessionUnavailable();
    return after;
  }

  Future<AuthTokenResponse> _conversation(int id, int revision) async {
    _id(id, 'conversationId');
    final session = await _session(revision);
    final room = await read.getConversation(id);
    await _verify(session, revision);
    if (room.id != id || room.clinicId != session.clinicContextId) {
      throw const ClinicChatSessionUnavailable();
    }
    return session;
  }

  Future<ClinicChatMessage> _message(
    int conversationId,
    int messageId,
    AuthTokenResponse actor,
    int revision,
  ) async {
    _id(messageId, 'messageId');
    final message = await read.getMessage(messageId);
    await _verify(actor, revision);
    if (message.conversationId != conversationId ||
        message.clinicId != actor.clinicContextId ||
        message.status != ClinicMessageStatus.sent &&
            message.status != ClinicMessageStatus.edited ||
        message.kind == ClinicMessageKind.system) {
      throw const FormatException('Message cannot be used for this action.');
    }
    return message;
  }

  Future<ClinicChatAuthoritativeUnread> loadUnread(int conversationId) async {
    final revision = read.sessionNavigationGuard.revision;
    final actor = await _conversation(conversationId, revision);
    final envelope = await read.apiClient.getJson(
      'api/v1/chat/conversations/$conversationId/unread',
      bearerToken: actor.accessToken,
    );
    await _verify(actor, revision);
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Unread summary not verified.');
    }
    final data = envelope['data'] as Map<String, dynamic>;
    final count = data['unread_count'];
    final marker = data['last_read_message_id'];
    if (data['conversation_id'] != conversationId ||
        data['clinic_id'] != actor.clinicContextId ||
        data['user_id'] != actor.userId ||
        count is! int ||
        count < 0 ||
        marker != null && (marker is! int || marker < 1)) {
      throw const FormatException('Unread identity or count mismatch.');
    }
    return ClinicChatAuthoritativeUnread(
      conversationId: conversationId,
      userId: actor.userId,
      unreadCount: count,
      lastReadMessageId: marker as int?,
    );
  }

  Future<List<T>> _list<T>(
    int conversationId,
    int messageId,
    T Function(Map<String, dynamic>, AuthTokenResponse) parse,
    String segment,
  ) async {
    final revision = read.sessionNavigationGuard.revision;
    final actor = await _conversation(conversationId, revision);
    await _message(conversationId, messageId, actor, revision);
    final envelope = await read.apiClient.getJson(
      'api/v1/chat/messages/$messageId/$segment?page=1&per_page=100',
      bearerToken: actor.accessToken,
    );
    await _verify(actor, revision);
    if (envelope['success'] != true ||
        envelope['data'] is! List ||
        envelope['page'] != 1 ||
        envelope['per_page'] != 100 ||
        envelope['total'] is! int ||
        envelope['pages'] is! int ||
        envelope['has_next'] is! bool) {
      throw const FormatException('Chat engagement page invalid.');
    }
    final rows = envelope['data'] as List;
    final total = envelope['total'] as int;
    final pages = envelope['pages'] as int;
    if (total < 0 ||
        pages != (total + 99) ~/ 100 ||
        rows.length > 100 ||
        rows.length > total ||
        envelope['has_next'] != (pages > 1)) {
      throw const FormatException('Chat engagement pagination inconsistent.');
    }
    final items = <T>[];
    for (final item in rows) {
      if (item is! Map<String, dynamic>) {
        throw const FormatException('Chat engagement row invalid.');
      }
      items.add(parse(item, actor));
    }
    return List<T>.unmodifiable(items);
  }

  Future<List<ClinicChatReactionRecord>> loadReactions(
    int conversationId,
    int messageId,
  ) => _list(conversationId, messageId, (json, actor) {
    final id = ClinicChatDecode.id(json, 'id');
    final msg = ClinicChatDecode.id(json, 'message_id');
    final user = ClinicChatDecode.id(json, 'user_id');
    final reaction = ClinicChatDecode.text(json, 'reaction');
    if (json['clinic_id'] != actor.clinicContextId ||
        msg != messageId ||
        !const {'like', 'heart', 'ack', 'thanks'}.contains(reaction)) {
      throw const FormatException('Reaction scope mismatch.');
    }
    return ClinicChatReactionRecord(
      id: id,
      messageId: msg,
      userId: user,
      reaction: reaction,
    );
  }, 'reactions');

  Future<List<ClinicChatMentionRecord>> loadMentions(
    int conversationId,
    int messageId,
  ) => _list(conversationId, messageId, (json, actor) {
    final id = ClinicChatDecode.id(json, 'id');
    final msg = ClinicChatDecode.id(json, 'message_id');
    final user = ClinicChatDecode.id(json, 'mentioned_user_id');
    if (json['clinic_id'] != actor.clinicContextId ||
        msg != messageId ||
        json['mention_type'] != 'user') {
      throw const FormatException('Mention scope mismatch.');
    }
    return ClinicChatMentionRecord(id: id, messageId: msg, userId: user);
  }, 'mentions');

  Future<Map<String, dynamic>> _postOnce({
    required int conversationId,
    required int messageId,
    required String path,
    required Map<String, dynamic> body,
    required bool Function(ClinicChatMessage, AuthTokenResponse) permitted,
  }) async {
    if (writeLocked) throw const ClinicChatFinalOutcomeUnconfirmed();
    _busy = true;
    var started = false;
    try {
      final revision = read.sessionNavigationGuard.revision;
      final actor = await _conversation(conversationId, revision);
      final message = await _message(
        conversationId,
        messageId,
        actor,
        revision,
      );
      if (!permitted(message, actor)) {
        throw const FormatException(
          'Engagement action not authorized by client preflight.',
        );
      }
      await _verify(actor, revision);
      started = true;
      final response = await read.apiClient.postJson(
        path,
        body: body,
        bearerToken: actor.accessToken,
      );
      await _verify(actor, revision);
      if (response['success'] != true ||
          response['data'] is! Map<String, dynamic>) {
        throw const FormatException('Engagement write not confirmed.');
      }
      return response['data'] as Map<String, dynamic>;
    } on Object {
      if (started) _uncertain = true;
      rethrow;
    } finally {
      _busy = false;
    }
  }

  /// Backend enforces (created_at, id) monotonicity; caller selects a visible
  /// delivered message. No mark-on-open or offline replay is performed.
  Future<ClinicChatAuthoritativeUnread> acknowledge(
    int conversationId,
    int messageId,
  ) async {
    final body = await _postOnce(
      conversationId: conversationId,
      messageId: messageId,
      path: 'api/v1/chat/conversations/$conversationId/read',
      body: {'last_read_message_id': messageId},
      permitted: (_, _) => true,
    );
    if (body['clinic_id'] == null ||
        body['conversation_id'] != conversationId ||
        body['last_read_message_id'] != messageId) {
      _uncertain = true;
      throw const ClinicChatFinalOutcomeUnconfirmed();
    }
    final after = await loadUnread(conversationId);
    if (after.lastReadMessageId != messageId) {
      _uncertain = true;
      throw const ClinicChatFinalOutcomeUnconfirmed();
    }
    return after;
  }

  Future<List<ClinicChatReactionRecord>> react(
    int conversationId,
    int messageId,
    String reaction,
  ) async {
    if (!const {'like', 'heart', 'ack', 'thanks'}.contains(reaction)) {
      throw ArgumentError.value(reaction, 'reaction');
    }
    final response = await _postOnce(
      conversationId: conversationId,
      messageId: messageId,
      path: 'api/v1/chat/messages/$messageId/reactions',
      body: {'reaction': reaction},
      permitted: (_, _) => true,
    );
    if (response['reaction'] != reaction ||
        response['message_id'] != messageId) {
      _uncertain = true;
      throw const ClinicChatFinalOutcomeUnconfirmed();
    }
    final list = await loadReactions(conversationId, messageId);
    if (!list.any((r) => r.id == response['id'] && r.reaction == reaction)) {
      _uncertain = true;
      throw const ClinicChatFinalOutcomeUnconfirmed();
    }
    return list;
  }

  Future<List<ClinicChatReactionRecord>> removeOwnReaction(
    int conversationId,
    int messageId,
    ClinicChatReactionRecord item,
  ) async {
    if (writeLocked) throw const ClinicChatFinalOutcomeUnconfirmed();
    _busy = true;
    var started = false;
    try {
      final revision = read.sessionNavigationGuard.revision;
      final actor = await _conversation(conversationId, revision);
      await _message(conversationId, messageId, actor, revision);
      if (item.messageId != messageId || item.userId != actor.userId) {
        throw const FormatException('Reaction does not belong to actor.');
      }
      started = true;
      final response = await read.apiClient.deleteJson(
        'api/v1/chat/messages/$messageId/reactions/${item.id}',
        bearerToken: actor.accessToken,
      );
      await _verify(actor, revision);
      if (response['success'] != true) {
        throw const FormatException('Reaction removal not confirmed.');
      }
      final list = await loadReactions(conversationId, messageId);
      if (list.any((reaction) => reaction.id == item.id)) {
        throw const FormatException('Reaction still present after removal.');
      }
      return list;
    } on Object {
      if (started) _uncertain = true;
      rethrow;
    } finally {
      _busy = false;
    }
  }

  Future<List<ClinicChatMentionRecord>> mentionUser(
    int conversationId,
    int messageId,
    int mentionedUserId,
  ) async {
    _id(mentionedUserId, 'mentionedUserId');
    final response = await _postOnce(
      conversationId: conversationId,
      messageId: messageId,
      path: 'api/v1/chat/messages/$messageId/mentions',
      body: {'mentioned_user_id': mentionedUserId},
      permitted: (message, actor) =>
          message.senderId == actor.userId &&
          message.kind == ClinicMessageKind.text &&
          mentionedUserId != actor.userId,
    );
    if (response['message_id'] != messageId ||
        response['mentioned_user_id'] != mentionedUserId) {
      _uncertain = true;
      throw const ClinicChatFinalOutcomeUnconfirmed();
    }
    final list = await loadMentions(conversationId, messageId);
    if (!list.any(
      (m) => m.id == response['id'] && m.userId == mentionedUserId,
    )) {
      _uncertain = true;
      throw const ClinicChatFinalOutcomeUnconfirmed();
    }
    return list;
  }
}
