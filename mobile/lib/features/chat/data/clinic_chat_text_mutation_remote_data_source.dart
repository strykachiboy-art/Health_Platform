import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/models/clinic_chat_read_models.dart';

class ClinicChatTextSessionUnavailable implements Exception {
  const ClinicChatTextSessionUnavailable();
  @override
  String toString() => 'Clinic chat session unavailable.';
}

class ClinicChatTextPreflightChanged implements Exception {
  const ClinicChatTextPreflightChanged();
  @override
  String toString() => 'Conversation or message changed. Review again.';
}

class ClinicChatTextOutcomeUnconfirmed implements Exception {
  const ClinicChatTextOutcomeUnconfirmed();
  @override
  String toString() =>
      'Message outcome not confirmed. Review server history; do not retry.';
}

class ClinicChatTextWriteLocked implements Exception {
  const ClinicChatTextWriteLocked();
  @override
  String toString() => 'Message write locked pending reconciliation.';
}

/// CHAT-C: text-only REST writes. No idempotency support exists for chat POST.
/// Exactly one HTTP mutation per logical action; no automatic retry/replay.
/// After any unconfirmed attempt, this instance permanently blocks all writes.
/// A successful action must pass an authenticated server GET before the next.
class ClinicChatTextMutationRemoteDataSource {
  ClinicChatTextMutationRemoteDataSource({required this.read});

  final ClinicChatReadRemoteDataSource read;
  bool _inFlight = false;
  bool _operationInFlight = false;
  bool _unconfirmed = false;
  bool get writeLocked => _operationInFlight || _inFlight || _unconfirmed;
  bool get outcomeUnconfirmed => _unconfirmed;

  static void _id(int value, String label) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, label, 'Must be a valid identifier.');
    }
  }

  static String _text(String input) {
    final value = input.trim();
    // The backend's default clinic policy limits messages to 5000 chars.
    // Per-clinic policies and the server's 10000-char hard cap still govern.
    if (value.isEmpty || value.length > 5000) {
      throw ArgumentError.value(input, 'content', 'Use 1 to 5000 characters.');
    }
    return value;
  }

  bool _current(int revision) =>
      read.sessionNavigationGuard.canAccessProtectedRoutes &&
      read.sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _session(int revision) async {
    if (!_current(revision)) {
      throw const ClinicChatTextSessionUnavailable();
    }
    try {
      final state = await read.loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          session.refreshToken.isEmpty ||
          session.role.trim().isEmpty ||
          !_current(revision)) {
        throw const ClinicChatTextSessionUnavailable();
      }
      return session;
    } on ClinicChatTextSessionUnavailable {
      rethrow;
    } on Object {
      throw const ClinicChatTextSessionUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> _freshConversation(
    ClinicChatConversation reviewed,
  ) async {
    if (_inFlight || _unconfirmed) {
      throw const ClinicChatTextWriteLocked();
    }
    _id(reviewed.id, 'conversationId');
    if (reviewed.status != ClinicChatStatus.active) {
      throw const ClinicChatTextPreflightChanged();
    }
    final revision = read.sessionNavigationGuard.revision;
    final before = await _session(revision);
    if (reviewed.clinicId != before.clinicContextId) {
      throw const ClinicChatTextSessionUnavailable();
    }
    final fresh = await read.getConversation(reviewed.id);
    final after = await _session(revision);
    if (!_same(before, after)) {
      throw const ClinicChatTextSessionUnavailable();
    }
    if (fresh.clinicId != reviewed.clinicId ||
        fresh.id != reviewed.id ||
        fresh.kind != reviewed.kind ||
        fresh.patientId != reviewed.patientId ||
        fresh.status != ClinicChatStatus.active) {
      throw const ClinicChatTextPreflightChanged();
    }
    return after;
  }

  Future<AuthTokenResponse> _freshOwnText({
    required ClinicChatConversation reviewed,
    required ClinicChatMessage selected,
    required bool forEdit,
  }) async {
    final before = await _freshConversation(reviewed);
    if (selected.clinicId != reviewed.clinicId ||
        selected.conversationId != reviewed.id ||
        selected.senderId != before.userId ||
        selected.kind != ClinicMessageKind.text ||
        selected.status == ClinicMessageStatus.deleted) {
      throw const ClinicChatTextPreflightChanged();
    }
    final actual = await read.getMessage(selected.id);
    final after = await _session(read.sessionNavigationGuard.revision);
    if (!_same(before, after)) {
      throw const ClinicChatTextSessionUnavailable();
    }
    if (actual.id != selected.id ||
        actual.clinicId != selected.clinicId ||
        actual.conversationId != selected.conversationId ||
        actual.senderId != selected.senderId ||
        actual.kind != selected.kind ||
        actual.status != selected.status ||
        actual.content != selected.content ||
        actual.createdAt != selected.createdAt ||
        (forEdit && actual.content == null)) {
      throw const ClinicChatTextPreflightChanged();
    }
    return after;
  }

  static ClinicChatMessage _parseWrite(
    Map<String, dynamic> envelope,
    AuthTokenResponse actor,
    int conversationId,
  ) {
    if (envelope['success'] != true) {
      throw const FormatException('Chat mutation not confirmed.');
    }
    final item = ClinicChatMessage.fromJson(
      ClinicChatDecode.object(envelope['data']),
      expectedClinicId: actor.clinicContextId!,
      expectedConversationId: conversationId,
    );
    if (item.senderId != actor.userId || item.kind != ClinicMessageKind.text) {
      throw const FormatException('Chat mutation identity mismatch.');
    }
    return item;
  }

  Future<ClinicChatMessage> _attempt({
    required String method,
    required String path,
    required AuthTokenResponse approved,
    required int conversationId,
    required String? expectedText,
    int? replyToMessageId,
    ClinicMessagePriority? priority,
  }) async {
    if (_inFlight || _unconfirmed) {
      throw const ClinicChatTextWriteLocked();
    }
    final revision = read.sessionNavigationGuard.revision;
    // Gate is set before the first network mutation; duplicates are blocked.
    _inFlight = true;
    try {
      final Map<String, dynamic> json;
      if (method == 'POST') {
        json = await read.apiClient.postJson(
          path,
          body: <String, dynamic>{
            'message_type': 'text',
            'content': expectedText,
            'priority': priority!.name,
            'reply_to_message_id': ?replyToMessageId,
          },
          bearerToken: approved.accessToken,
        );
      } else if (method == 'PATCH') {
        json = await read.apiClient.patchJson(
          path,
          body: <String, dynamic>{'content': expectedText},
          bearerToken: approved.accessToken,
        );
      } else {
        json = await read.apiClient.deleteJson(
          path,
          bearerToken: approved.accessToken,
        );
      }
      final after = await _session(revision);
      if (!_same(approved, after)) {
        throw const FormatException('Chat session changed after write.');
      }
      final created = _parseWrite(json, approved, conversationId);
      if (method == 'POST') {
        if (created.content != expectedText ||
            created.priority != priority ||
            created.replyToMessageId != replyToMessageId ||
            created.status == ClinicMessageStatus.deleted) {
          throw const FormatException('Chat POST confirmation inconsistent.');
        }
      } else if (method == 'PATCH') {
        if (created.content != expectedText ||
            created.status != ClinicMessageStatus.edited) {
          throw const FormatException('Chat PATCH confirmation inconsistent.');
        }
      } else if (created.status != ClinicMessageStatus.deleted) {
        throw const FormatException('Chat DELETE confirmation inconsistent.');
      }
      // A POST/PATCH/DELETE response alone is not enough for the next action.
      final confirmed = await read.getMessage(created.id);
      final finalSession = await _session(revision);
      if (!_same(approved, finalSession) ||
          confirmed.id != created.id ||
          confirmed.conversationId != conversationId ||
          confirmed.senderId != approved.userId ||
          confirmed.clinicId != approved.clinicContextId ||
          confirmed.kind != ClinicMessageKind.text ||
          confirmed.replyToMessageId != created.replyToMessageId ||
          (method == 'POST' &&
              (confirmed.content != expectedText ||
                  confirmed.priority != priority ||
                  confirmed.status == ClinicMessageStatus.deleted)) ||
          (method == 'PATCH' &&
              (confirmed.content != expectedText ||
                  confirmed.status != ClinicMessageStatus.edited)) ||
          (method == 'DELETE' &&
              confirmed.status != ClinicMessageStatus.deleted)) {
        throw const FormatException(
          'Chat authoritative readback inconsistent.',
        );
      }
      _inFlight = false;
      return confirmed;
    } on Object {
      // All errors after starting the HTTP action are treated as unknown.
      // No automatic replay, including after malformed JSON or lost response.
      _unconfirmed = true;
      _inFlight = false;
      throw const ClinicChatTextOutcomeUnconfirmed();
    }
  }

  Future<ClinicChatMessage> sendText({
    required ClinicChatConversation reviewed,
    required String content,
    int? replyToMessageId,
    ClinicMessagePriority priority = ClinicMessagePriority.normal,
  }) async {
    if (writeLocked) {
      throw const ClinicChatTextWriteLocked();
    }
    _operationInFlight = true;
    try {
      final text = _text(content);
      if (replyToMessageId != null) {
        _id(replyToMessageId, 'replyToMessageId');
      }
      final approved = await _freshConversation(reviewed);
      if (replyToMessageId != null) {
        final referenced = await read.getMessage(replyToMessageId);
        final after = await _session(read.sessionNavigationGuard.revision);
        if (!_same(approved, after) ||
            referenced.clinicId != reviewed.clinicId ||
            referenced.conversationId != reviewed.id ||
            referenced.status == ClinicMessageStatus.deleted) {
          throw const ClinicChatTextPreflightChanged();
        }
      }
      return await _attempt(
        method: 'POST',
        path: 'api/v1/chat/conversations/${reviewed.id}/messages',
        approved: approved,
        conversationId: reviewed.id,
        expectedText: text,
        replyToMessageId: replyToMessageId,
        priority: priority,
      );
    } finally {
      _operationInFlight = false;
    }
  }

  Future<ClinicChatMessage> editText({
    required ClinicChatConversation reviewed,
    required ClinicChatMessage selected,
    required String content,
  }) async {
    if (writeLocked) {
      throw const ClinicChatTextWriteLocked();
    }
    _operationInFlight = true;
    try {
      final text = _text(content);
      if (text == selected.content) {
        throw const ClinicChatTextPreflightChanged();
      }
      final approved = await _freshOwnText(
        reviewed: reviewed,
        selected: selected,
        forEdit: true,
      );
      return await _attempt(
        method: 'PATCH',
        path: 'api/v1/chat/messages/${selected.id}',
        approved: approved,
        conversationId: reviewed.id,
        expectedText: text,
      );
    } finally {
      _operationInFlight = false;
    }
  }

  Future<ClinicChatMessage> deleteText({
    required ClinicChatConversation reviewed,
    required ClinicChatMessage selected,
  }) async {
    if (writeLocked) {
      throw const ClinicChatTextWriteLocked();
    }
    _operationInFlight = true;
    try {
      final approved = await _freshOwnText(
        reviewed: reviewed,
        selected: selected,
        forEdit: false,
      );
      return await _attempt(
        method: 'DELETE',
        path: 'api/v1/chat/messages/${selected.id}',
        approved: approved,
        conversationId: reviewed.id,
        expectedText: null,
      );
    } finally {
      _operationInFlight = false;
    }
  }
}
