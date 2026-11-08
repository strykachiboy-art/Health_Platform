import 'dart:async';

import 'package:flutter/foundation.dart';

import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/models/clinic_chat_read_models.dart';

import 'clinic_chat_socket_transport.dart';

enum ClinicChatRealtimeState {
  offline,
  connecting,
  waitingForJoin,
  syncing,
  ready,
  unavailable,
  disposed,
}

/// Server notifications contain identifiers, not authoritative message text.
/// No Socket.IO message writes or replays. Each reconnection needs fresh
/// session authentication and a complete REST resync.
class ClinicChatRealtimeReconciler extends ChangeNotifier {
  ClinicChatRealtimeReconciler({
    required this.read,
    required this.conversationId,
    ClinicChatSocketFactory? socketFactory,
  }) : _socketFactory = socketFactory ?? clinicChatSocketFactory {
    if (conversationId <= 0 || conversationId > 2147483647) {
      throw ArgumentError.value(conversationId, 'conversationId');
    }
    read.sessionNavigationGuard.addListener(_onSessionRevision);
  }

  final ClinicChatReadRemoteDataSource read;
  final int conversationId;
  final ClinicChatSocketFactory _socketFactory;

  ClinicChatSocketTransport? _socket;
  AuthTokenResponse? _authenticated;
  ClinicChatRealtimeState _state = ClinicChatRealtimeState.offline;
  ClinicChatPage<ClinicChatMessage>? _messages;
  ClinicChatConversation? _conversation;
  int _epoch = 0;
  int? _revision;
  bool _disposed = false;
  bool _syncing = false;
  bool _needsSync = false;
  final Set<int> _seenEventIds = <int>{};

  ClinicChatRealtimeState get state => _state;
  ClinicChatPage<ClinicChatMessage>? get messages => _messages;
  ClinicChatConversation? get conversation => _conversation;
  bool get isReady => _state == ClinicChatRealtimeState.ready;

  bool _active(int epoch) =>
      !_disposed &&
      epoch == _epoch &&
      read.sessionNavigationGuard.canAccessProtectedRoutes &&
      read.sessionNavigationGuard.revision == _revision;

  void _setState(ClinicChatRealtimeState next) {
    if (_disposed) return;
    _state = next;
    notifyListeners();
  }

  void _drop({required ClinicChatRealtimeState state}) {
    _epoch++;
    _socket?.dispose();
    _socket = null;
    _authenticated = null;
    _revision = null;
    _syncing = false;
    _needsSync = false;
    _messages = null;
    _conversation = null;
    _seenEventIds.clear();
    _setState(state);
  }

  void _onSessionRevision() {
    // On logout, revoked session, or context change: destroy socket & PHI.
    _drop(state: ClinicChatRealtimeState.offline);
  }

  AuthTokenResponse _requireReady(AuthSessionState state) {
    final session = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.userId <= 0 ||
        session.clinicContextId == null ||
        session.clinicContextId! <= 0 ||
        session.accessToken.isEmpty ||
        session.accessToken.trim() != session.accessToken ||
        session.refreshToken.isEmpty ||
        session.role.trim().isEmpty) {
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

  Future<AuthTokenResponse> _checkSession(int epoch) async {
    if (!_active(epoch)) throw const ClinicChatSessionUnavailable();
    final result = _requireReady(await read.loadSession());
    if (!_active(epoch) ||
        _authenticated == null ||
        !_same(result, _authenticated!)) {
      throw const ClinicChatSessionUnavailable();
    }
    return result;
  }

  /// Explicit start/reconnect, with a fresh JWT and full authoritative REST
  /// readback. Never automatically reconnects with a previously used token.
  Future<void> start() async {
    if (_disposed) throw StateError('Chat realtime controller disposed.');
    _drop(state: ClinicChatRealtimeState.offline);
    final epoch = _epoch;
    final revision = read.sessionNavigationGuard.revision;
    if (!read.sessionNavigationGuard.canAccessProtectedRoutes) return;
    _revision = revision;
    _setState(ClinicChatRealtimeState.connecting);
    try {
      final before = _requireReady(await read.loadSession());
      if (!_active(epoch)) return;
      final fresh = _requireReady(await read.loadSession());
      if (!_active(epoch) || !_same(before, fresh)) {
        throw const ClinicChatSessionUnavailable();
      }
      // Require authorized REST access before subscribing to any events.
      final approved = await read.getConversation(conversationId);
      if (!_active(epoch)) return;
      final after = _requireReady(await read.loadSession());
      if (!_same(before, after) ||
          approved.clinicId != before.clinicContextId ||
          approved.id != conversationId) {
        throw const ClinicChatSessionUnavailable();
      }
      _authenticated = before;
      _conversation = null; // Do not expose stale data before joining.
      final socket = _socketFactory(
        origin: read.apiClient.config.baseUri,
        accessToken: before.accessToken,
      );
      if (!_active(epoch)) {
        socket.dispose();
        return;
      }
      _socket = socket;
      socket.on('chat.connected', (value) => _onConnected(epoch, value));
      socket.on('conversation.joined', (value) => _onJoined(epoch, value));
      socket.on('chat.error', (value) => _dropIfActive(epoch));
      socket.on('connect_error', (value) => _dropIfActive(epoch));
      socket.on('disconnect', (value) => _dropIfActive(epoch));
      for (final name in const <String>[
        'message.created',
        'message.updated',
        'message.deleted',
      ]) {
        socket.on(name, (value) => _onMessageEvent(epoch, name, value));
      }
      socket.connect();
    } on Object {
      if (_active(epoch)) _drop(state: ClinicChatRealtimeState.unavailable);
    }
  }

  void _dropIfActive(int epoch) {
    if (_active(epoch)) _drop(state: ClinicChatRealtimeState.unavailable);
  }

  static Map<String, dynamic>? _payload(Object? raw) {
    if (raw is! Map) return null;
    if (raw.keys.any((key) => key is! String)) return null;
    return Map<String, dynamic>.from(raw);
  }

  static int? _id(Object? value) =>
      value is int && value > 0 && value <= 2147483647 ? value : null;

  void _onConnected(int epoch, Object? raw) {
    if (!_active(epoch) || _state != ClinicChatRealtimeState.connecting) return;
    final payload = _payload(raw);
    final auth = _authenticated;
    if (payload == null ||
        auth == null ||
        _id(payload['user_id']) != auth.userId ||
        _id(payload['clinic_id']) != auth.clinicContextId) {
      _dropIfActive(epoch);
      return;
    }
    _setState(ClinicChatRealtimeState.waitingForJoin);
    _socket?.emit('conversation.join', {'conversation_id': conversationId});
  }

  void _onJoined(int epoch, Object? raw) {
    if (!_active(epoch) || _state != ClinicChatRealtimeState.waitingForJoin) {
      return;
    }
    final payload = _payload(raw);
    if (payload == null ||
        _id(payload['conversation_id']) != conversationId ||
        _id(payload['user_id']) != _authenticated?.userId) {
      _dropIfActive(epoch);
      return;
    }
    _needsSync = true;
    unawaited(_sync(epoch));
  }

  void _onMessageEvent(int epoch, String name, Object? raw) {
    if (!_active(epoch) ||
        (_state != ClinicChatRealtimeState.syncing &&
            _state != ClinicChatRealtimeState.ready)) {
      return;
    }
    final payload = _payload(raw);
    final auth = _authenticated;
    if (payload == null ||
        auth == null ||
        payload['event_type'] != name ||
        _id(payload['clinic_id']) != auth.clinicContextId ||
        _id(payload['conversation_id']) == null ||
        _id(payload['message_id']) == null ||
        _id(payload['event_id']) == null) {
      _dropIfActive(epoch);
      return;
    }
    // The backend delivers every authorized conversation to this user room.
    // Other conversations are irrelevant to this controller, not errors.
    if (payload['conversation_id'] != conversationId) return;
    final eventId = payload['event_id'] as int;
    if (!_seenEventIds.add(eventId)) return;
    if (_seenEventIds.length > 256) _seenEventIds.clear();
    _needsSync = true;
    unawaited(_sync(epoch));
  }

  Future<void> _sync(int epoch) async {
    if (!_active(epoch) || !_needsSync || _syncing) return;
    _syncing = true;
    _messages = null;
    _conversation = null;
    _setState(ClinicChatRealtimeState.syncing);
    try {
      while (_active(epoch) && _needsSync) {
        _needsSync = false;
        await _checkSession(epoch);
        final approved = await read.getConversation(conversationId);
        if (!_active(epoch)) return;
        final page = await read.listMessages(conversationId: conversationId);
        if (!_active(epoch)) return;
        await _checkSession(epoch);
        if (approved.id != conversationId ||
            approved.clinicId != _authenticated!.clinicContextId ||
            page.items.any(
              (message) =>
                  message.clinicId != approved.clinicId ||
                  message.conversationId != conversationId,
            )) {
          throw const FormatException('Chat realtime REST scope mismatch.');
        }
        _conversation = approved;
        _messages = page;
      }
      if (_active(epoch)) _setState(ClinicChatRealtimeState.ready);
    } on Object {
      if (_active(epoch)) _drop(state: ClinicChatRealtimeState.unavailable);
    } finally {
      if (_epoch == epoch) _syncing = false;
    }
  }

  /// Hides all formerly loaded messages immediately. A further start() is
  /// an explicit new session and never retries any message mutation.
  void stop() {
    if (!_disposed) _drop(state: ClinicChatRealtimeState.offline);
  }

  @override
  void dispose() {
    if (_disposed) return;
    read.sessionNavigationGuard.removeListener(_onSessionRevision);
    _drop(state: ClinicChatRealtimeState.offline);
    _disposed = true;
    _state = ClinicChatRealtimeState.disposed;
    super.dispose();
  }
}
