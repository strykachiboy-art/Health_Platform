import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';

/// The server's position for the authenticated participant only.
/// This is NOT a calculated unread count, delivery receipt, or read mutation.
class ClinicChatOwnReadState {
  const ClinicChatOwnReadState({
    required this.conversationId,
    required this.lastReadMessageId,
  });

  final int conversationId;
  final int? lastReadMessageId;
}

/// P24G.S01: read-only, owner- and clinic-verified participant read marker.
/// Uses the existing CHAT-B authenticated REST reader and authorization guard.
/// No socket, cache, automatic read acknowledgement, or message write.
class ClinicChatReadStateRemoteDataSource {
  const ClinicChatReadStateRemoteDataSource({required this.read});

  final ClinicChatReadRemoteDataSource read;

  static const int _perPage = 100;
  static const int _maxPages = 10;

  bool _active(int revision) =>
      read.sessionNavigationGuard.canAccessProtectedRoutes &&
      read.sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _session(int revision) async {
    if (!_active(revision)) throw const ClinicChatSessionUnavailable();
    try {
      final state = await read.loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken != session.accessToken.trim() ||
          session.refreshToken.isEmpty ||
          session.role.trim().isEmpty ||
          !_active(revision)) {
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

  Future<void> _recheck(AuthTokenResponse before, int revision) async {
    final after = await _session(revision);
    if (!_same(before, after) || !_active(revision)) {
      throw const ClinicChatSessionUnavailable();
    }
  }

  /// Fails closed if own accepted participant cannot be proved within a
  /// bounded number of server pages. Never invents an unread count.
  Future<ClinicChatOwnReadState> loadMine(int conversationId) async {
    if (conversationId <= 0 || conversationId > 2147483647) {
      throw ArgumentError.value(conversationId, 'conversationId');
    }
    final revision = read.sessionNavigationGuard.revision;
    final before = await _session(revision);
    final conversation = await read.getConversation(conversationId);
    await _recheck(before, revision);
    if (conversation.id != conversationId ||
        conversation.clinicId != before.clinicContextId) {
      throw const ClinicChatSessionUnavailable();
    }

    for (var page = 1; page <= _maxPages; page++) {
      await _recheck(before, revision);
      final result = await read.apiClient.getJson(
        'api/v1/chat/conversations/$conversationId/participants'
        '?page=$page&per_page=$_perPage',
        bearerToken: before.accessToken,
      );
      await _recheck(before, revision);
      final items = result['data'];
      final serverPage = result['page'];
      final perPage = result['per_page'];
      final total = result['total'];
      final pages = result['pages'];
      final hasNext = result['has_next'];
      final hasPrevious = result['has_previous'];
      if (items is! List ||
          serverPage != page ||
          perPage != _perPage ||
          total is! int ||
          total < 0 ||
          pages is! int ||
          pages < 0 ||
          pages != (total + _perPage - 1) ~/ _perPage ||
          hasNext is! bool ||
          hasNext != (page < pages) ||
          hasPrevious is! bool ||
          hasPrevious != (page > 1 && total > 0) ||
          items.length > _perPage ||
          items.length > total) {
        throw const FormatException('Invalid participant page.');
      }
      for (final raw in items) {
        if (raw is! Map<String, dynamic> ||
            raw['clinic_id'] != before.clinicContextId ||
            raw['conversation_id'] != conversationId ||
            raw['user_id'] is! int ||
            (raw['user_id'] as int) <= 0) {
          throw const FormatException('Participant scope mismatch.');
        }
        if (raw['user_id'] == before.userId) {
          final marker = raw['last_read_message_id'];
          if (raw['status'] != 'accepted' ||
              marker != null &&
                  (marker is! int || marker <= 0 || marker > 2147483647)) {
            throw const FormatException('Unverified participant read state.');
          }
          await _recheck(before, revision);
          return ClinicChatOwnReadState(
            conversationId: conversationId,
            lastReadMessageId: marker as int?,
          );
        }
      }
      if (!hasNext) break;
    }
    throw const FormatException('Own read marker could not be verified.');
  }
}
