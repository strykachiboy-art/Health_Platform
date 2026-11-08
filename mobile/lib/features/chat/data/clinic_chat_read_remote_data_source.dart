import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/clinic_chat_read_models.dart';

class ClinicChatSessionUnavailable implements Exception {
  const ClinicChatSessionUnavailable();
  @override
  String toString() => 'Clinic chat session unavailable.';
}

/// CHAT-B: reads only. No queue, PHI cache, socket connection or write method.
/// Backend membership and case eligibility remain authoritative on EVERY GET.
class ClinicChatReadRemoteDataSource {
  const ClinicChatReadRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static void _positive(int value, String label) {
    if (value < 1 || value > 2147483647) {
      throw ArgumentError.value(value, label, 'Must be a valid identifier.');
    }
  }

  static void _pagination(int page, int perPage) {
    _positive(page, 'page');
    if (perPage < 1 || perPage > 500) {
      throw ArgumentError.value(perPage, 'perPage', 'Must be 1 to 500.');
    }
  }

  static String? _search(String? value) {
    if (value == null) {
      return null;
    }
    final trimmed = value.trim();
    if (trimmed.length > 200) {
      throw ArgumentError.value(value, 'query', 'Maximum 200 characters.');
    }
    return trimmed.isEmpty ? null : trimmed;
  }

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _session(int revision) async {
    if (!_current(revision)) {
      throw const ClinicChatSessionUnavailable();
    }
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.trim().isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          session.refreshToken.trim().isEmpty ||
          session.role.trim().isEmpty ||
          !_current(revision)) {
        throw const ClinicChatSessionUnavailable();
      }
      return session;
    } on ClinicChatSessionUnavailable {
      rethrow;
    } on Object {
      throw const ClinicChatSessionUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<(Map<String, dynamic>, AuthTokenResponse)> _get(String path) async {
    final revision = sessionNavigationGuard.revision;
    final before = await _session(revision);
    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    final after = await _session(revision);
    if (!_same(before, after) || !_current(revision)) {
      throw const ClinicChatSessionUnavailable();
    }
    return (response, after);
  }

  Future<ClinicChatPage<ClinicChatConversation>> listConversations({
    int page = 1,
    int perPage = 25,
    String? search,
  }) async {
    _pagination(page, perPage);
    final query = _search(search);
    final path = Uri(
      path: 'api/v1/chat/conversations',
      queryParameters: {
        'page': '$page',
        'per_page': '$perPage',
        'search': ?query,
      },
    ).toString();
    final (json, session) = await _get(path);
    final result = ClinicChatPage.parse<ClinicChatConversation>(
      json,
      (row) => ClinicChatConversation.fromJson(
        row,
        expectedClinicId: session.clinicContextId!,
      ),
    );
    if (result.page != page || result.perPage != perPage) {
      throw const FormatException('Chat conversation pagination mismatch.');
    }
    final seen = <int>{};
    for (final item in result.items) {
      if (!seen.add(item.id)) {
        throw const FormatException('Duplicate conversation ID in page.');
      }
    }
    return result;
  }

  /// CHAT-E: canonical server-owned admission link, never inferred from titles.
  /// Double-reads the existing authorized conversation before showing PHI.
  Future<ClinicChatConversation> getAdmissionCaseConversation({
    required int admissionId,
    required int patientId,
  }) async {
    _positive(admissionId, 'admissionId');
    _positive(patientId, 'patientId');
    final revision = sessionNavigationGuard.revision;
    final before = await _session(revision);
    final (json, session) = await _get(
      'api/v1/chat/admissions/$admissionId/conversation',
    );
    if (json['success'] != true) {
      throw const FormatException('Admission case link was not confirmed.');
    }
    final link = ClinicChatDecode.object(json['data']);
    final clinicId = session.clinicContextId!;
    if (ClinicChatDecode.id(link, 'clinic_id') != clinicId ||
        ClinicChatDecode.id(link, 'admission_id') != admissionId ||
        ClinicChatDecode.id(link, 'patient_id') != patientId) {
      throw const FormatException('Admission case link scope mismatch.');
    }
    final linkedId = ClinicChatDecode.id(link, 'conversation_id');
    final linked = ClinicChatConversation.fromJson(
      ClinicChatDecode.object(link['conversation']),
      expectedClinicId: clinicId,
    );
    if (linked.id != linkedId ||
        linked.patientId != patientId ||
        linked.kind != ClinicChatKind.patient ||
        linked.status != ClinicChatStatus.active) {
      throw const FormatException('Admission case conversation is invalid.');
    }
    final verified = await getConversation(linkedId);
    final after = await _session(revision);
    if (!_same(before, after) || !_current(revision)) {
      throw const ClinicChatSessionUnavailable();
    }
    if (verified.id != linkedId ||
        verified.clinicId != clinicId ||
        verified.patientId != patientId ||
        verified.kind != ClinicChatKind.patient ||
        verified.status != ClinicChatStatus.active) {
      throw const FormatException('Admission case readback scope changed.');
    }
    return verified;
  }

  Future<ClinicChatConversation> getConversation(int conversationId) async {
    _positive(conversationId, 'conversationId');
    final (json, session) = await _get(
      'api/v1/chat/conversations/$conversationId',
    );
    if (json['success'] != true) {
      throw const FormatException('Chat conversation not confirmed.');
    }
    final item = ClinicChatConversation.fromJson(
      ClinicChatDecode.object(json['data']),
      expectedClinicId: session.clinicContextId!,
    );
    if (item.id != conversationId) {
      throw const FormatException('Chat conversation ID mismatch.');
    }
    return item;
  }

  Future<ClinicChatPage<ClinicChatMessage>> listMessages({
    required int conversationId,
    int page = 1,
    int perPage = 25,
  }) async {
    _positive(conversationId, 'conversationId');
    _pagination(page, perPage);
    final path = Uri(
      path: 'api/v1/chat/conversations/$conversationId/messages',
      queryParameters: {'page': '$page', 'per_page': '$perPage'},
    ).toString();
    final (json, session) = await _get(path);
    final result = ClinicChatPage.parse<ClinicChatMessage>(
      json,
      (row) => ClinicChatMessage.fromJson(
        row,
        expectedClinicId: session.clinicContextId!,
        expectedConversationId: conversationId,
      ),
    );
    if (result.page != page || result.perPage != perPage) {
      throw const FormatException('Chat message pagination mismatch.');
    }
    final seen = <int>{};
    for (final item in result.items) {
      if (!seen.add(item.id)) {
        throw const FormatException('Duplicate message ID in page.');
      }
    }
    return result;
  }

  Future<ClinicChatMessage> getMessage(int messageId) async {
    _positive(messageId, 'messageId');
    final (json, session) = await _get('api/v1/chat/messages/$messageId');
    if (json['success'] != true) {
      throw const FormatException('Chat message not confirmed.');
    }
    final item = ClinicChatMessage.fromJson(
      ClinicChatDecode.object(json['data']),
      expectedClinicId: session.clinicContextId!,
    );
    if (item.id != messageId) {
      throw const FormatException('Chat message ID mismatch.');
    }
    return item;
  }

  Future<ClinicChatPage<ClinicChatMessage>> searchMessages({
    required String query,
    int page = 1,
    int perPage = 25,
    int? conversationId,
  }) async {
    _pagination(page, perPage);
    if (conversationId != null) {
      _positive(conversationId, 'conversationId');
    }
    final needle = _search(query);
    if (needle == null) {
      throw ArgumentError.value(query, 'query', 'Search must not be empty.');
    }
    final path = Uri(
      path: 'api/v1/chat/messages/search',
      queryParameters: {
        'query': needle,
        'page': '$page',
        'per_page': '$perPage',
        if (conversationId != null) 'conversation_id': '$conversationId',
      },
    ).toString();
    final (json, session) = await _get(path);
    final result = ClinicChatPage.parse<ClinicChatMessage>(
      json,
      (row) => ClinicChatMessage.fromJson(
        row,
        expectedClinicId: session.clinicContextId!,
        expectedConversationId: conversationId,
      ),
    );
    if (result.page != page || result.perPage != perPage) {
      throw const FormatException('Chat message-search pagination mismatch.');
    }
    final seen = <int>{};
    for (final item in result.items) {
      if (!seen.add(item.id)) {
        throw const FormatException('Duplicate message ID in search.');
      }
    }
    return result;
  }
}
