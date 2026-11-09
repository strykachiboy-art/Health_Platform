import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'clinic_chat_read_remote_data_source.dart';
import 'models/clinic_chat_read_models.dart';

/// A sent upload may have committed without its HTTP acknowledgement.
/// No automatic replay or new UUID is permitted for an uncertain outcome.
class ClinicChatMediaOutcomeUnconfirmed implements Exception {
  const ClinicChatMediaOutcomeUnconfirmed();
}

class ClinicChatMediaSelection {
  const ClinicChatMediaSelection({
    required this.kind,
    required this.name,
    required this.mime,
    required this.bytes,
  });

  final String kind;
  final String name;
  final String mime;
  final Uint8List bytes;
}

/// CHAT-F1/F2: authenticated private transport, no public media URLs or cache.
/// Shares the canonical CHAT-B session guard and REST readback logic.
class ClinicChatMediaRemoteDataSource {
  ClinicChatMediaRemoteDataSource({
    required this.read,
    http.Client? client,
    this.timeout = const Duration(seconds: 40),
  }) : _client = client ?? http.Client(),
       _ownsClient = client == null;

  final ClinicChatReadRemoteDataSource read;
  final http.Client _client;
  final bool _ownsClient;
  final Duration timeout;
  bool _outcomeUnconfirmed = false;

  bool get outcomeUnconfirmed => _outcomeUnconfirmed;

  void dispose() {
    if (_ownsClient) _client.close();
  }

  Future<AuthTokenResponse> _session(int revision) async {
    final guard = read.sessionNavigationGuard;
    if (guard.revision != revision || !guard.canAccessProtectedRoutes) {
      throw const ClinicChatSessionUnavailable();
    }
    final state = await read.loadSession();
    final item = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        item == null ||
        item.userId <= 0 ||
        item.clinicContextId == null ||
        item.clinicContextId! <= 0 ||
        item.accessToken.isEmpty ||
        item.accessToken.trim() != item.accessToken ||
        item.refreshToken.isEmpty ||
        item.role.trim().isEmpty ||
        guard.revision != revision ||
        !guard.canAccessProtectedRoutes) {
      throw const ClinicChatSessionUnavailable();
    }
    return item;
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  static void validate(ClinicChatMediaSelection item) {
    const types = <String, Set<String>>{
      'image': {'image/jpeg', 'image/png', 'image/webp'},
      'document': {
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      },
      'audio': {'audio/wav', 'audio/ogg', 'audio/mp4'},
    };
    if (types[item.kind]?.contains(item.mime) != true ||
        item.bytes.isEmpty ||
        item.bytes.length > 25 * 1024 * 1024 ||
        item.name.isEmpty ||
        item.name.length > 180 ||
        item.name.contains('/') ||
        item.name.contains('\\') ||
        item.name.codeUnits.any((c) => c < 32)) {
      throw const FormatException('Unsupported or oversized chat media.');
    }
  }

  Future<ClinicChatMessage> upload({
    required int conversationId,
    required ClinicChatMediaSelection selection,
    required String requestId,
    String? caption,
  }) async {
    if (conversationId <= 0 || _outcomeUnconfirmed) {
      throw StateError('Chat media write is locked.');
    }
    validate(selection);
    if (!RegExp(
      r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    ).hasMatch(requestId)) {
      throw const FormatException('Invalid client request UUID.');
    }
    final revision = read.sessionNavigationGuard.revision;
    final before = await _session(revision);
    final current = await read.getConversation(conversationId);
    if (current.status != ClinicChatStatus.active ||
        current.clinicId != before.clinicContextId) {
      throw const ClinicChatSessionUnavailable();
    }
    final preflight = await _session(revision);
    if (!_same(before, preflight)) {
      throw const ClinicChatSessionUnavailable();
    }
    final path = 'api/v1/chat/conversations/$conversationId/media';
    final uri = read.apiClient.config.endpoint(path);
    final request = http.MultipartRequest('POST', uri)
      ..headers['Authorization'] = 'Bearer ${before.accessToken}'
      ..headers['Accept'] = 'application/json'
      ..fields['kind'] = selection.kind
      ..fields['client_request_id'] = requestId;
    if (caption != null && caption.trim().isNotEmpty) {
      request.fields['caption'] = caption.trim();
    }
    request.files.add(
      http.MultipartFile.fromBytes(
        'file',
        selection.bytes,
        filename: selection.name,
        contentType: MediaType.parse(selection.mime),
      ),
    );
    late final http.Response response;
    try {
      final streamed = await _client.send(request).timeout(timeout);
      response = await http.Response.fromStream(streamed).timeout(timeout);
    } on Object {
      _outcomeUnconfirmed = true;
      throw const ClinicChatMediaOutcomeUnconfirmed();
    }
    if (response.statusCode == 401) {
      read.apiClient.onAuthenticationRejected?.call();
    }
    if (response.statusCode != 200 && response.statusCode != 201) {
      if (response.statusCode >= 500 || response.statusCode == 408) {
        _outcomeUnconfirmed = true;
        throw const ClinicChatMediaOutcomeUnconfirmed();
      }
      throw ApiException(
        kind: ApiFailureKind.httpStatus,
        message: 'Media upload rejected by backend.',
        uri: uri,
        statusCode: response.statusCode,
      );
    }
    Map<String, dynamic> body;
    try {
      final decoded = jsonDecode(response.body);
      body = ClinicChatDecode.object(decoded);
    } on Object {
      _outcomeUnconfirmed = true;
      throw const ClinicChatMediaOutcomeUnconfirmed();
    }
    if (body['success'] != true) {
      _outcomeUnconfirmed = true;
      throw const ClinicChatMediaOutcomeUnconfirmed();
    }
    final returned = ClinicChatMessage.fromJson(
      ClinicChatDecode.object(body['data']),
      expectedClinicId: before.clinicContextId!,
      expectedConversationId: conversationId,
    );
    try {
      final canonical = await read.getMessage(returned.id);
      final after = await _session(revision);
      if (!_same(before, after) ||
          canonical.conversationId != conversationId ||
          canonical.attachments.length != 1 ||
          canonical.id != returned.id ||
          canonical.kind != returned.kind) {
        throw const ClinicChatSessionUnavailable();
      }
      return canonical;
    } on Object {
      _outcomeUnconfirmed = true;
      throw const ClinicChatMediaOutcomeUnconfirmed();
    }
  }

  Future<Uint8List> download({
    required ClinicChatMessage message,
    required ClinicChatAttachment attachment,
  }) async {
    if (message.id <= 0 ||
        attachment.id <= 0 ||
        message.status == ClinicMessageStatus.deleted ||
        message.attachments.every((a) => a.id != attachment.id)) {
      throw const FormatException('Invalid attachment reference.');
    }
    final revision = read.sessionNavigationGuard.revision;
    final before = await _session(revision);
    final verified = await read.getMessage(message.id);
    if (verified.clinicId != before.clinicContextId ||
        verified.conversationId != message.conversationId ||
        verified.status == ClinicMessageStatus.deleted ||
        verified.attachments.every((a) => a.id != attachment.id)) {
      throw const ClinicChatSessionUnavailable();
    }
    final uri = read.apiClient.config.endpoint(
      'api/v1/chat/messages/${message.id}/attachments/${attachment.id}',
    );
    final response = await _client
        .get(
          uri,
          headers: {
            'Authorization': 'Bearer ${before.accessToken}',
            'Accept': 'application/octet-stream',
          },
        )
        .timeout(timeout);
    if (response.statusCode == 401) {
      read.apiClient.onAuthenticationRejected?.call();
    }
    if (response.statusCode != 200 ||
        response.bodyBytes.length > 25 * 1024 * 1024) {
      throw ApiException(
        kind: ApiFailureKind.httpStatus,
        message: 'Attachment unavailable.',
        uri: uri,
        statusCode: response.statusCode,
      );
    }
    final after = await _session(revision);
    if (!_same(before, after)) {
      throw const ClinicChatSessionUnavailable();
    }
    return Uint8List.fromList(response.bodyBytes);
  }
}
