import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/backend_config.dart';
import 'api_exception.dart';

enum ApiCommunicationStatus {
  notChecked,
  responseReceived,
  responseNotReceived,
}

/// HTTP boundary for the existing Clinic System Pro backend.
class ApiClient {
  ApiClient({
    required this.config,
    http.Client? client,
    this.requestTimeout = const Duration(seconds: 10),
  }) : _client = client ?? http.Client(),
       _ownsClient = client == null;

  final BackendConfig config;
  final http.Client _client;
  final bool _ownsClient;
  final Duration requestTimeout;

  int _requestSequence = 0;

  ApiCommunicationStatus _communicationStatus =
      ApiCommunicationStatus.notChecked;

  final StreamController<ApiCommunicationStatus> _communicationStatusChanges =
      StreamController<ApiCommunicationStatus>.broadcast(sync: true);

  /// Latest observed transport result; this is not a backend health check.
  ApiCommunicationStatus get communicationStatus => _communicationStatus;

  /// Emits whether each completed request received an HTTP response.
  Stream<ApiCommunicationStatus> get communicationStatusChanges =>
      _communicationStatusChanges.stream;

  void _recordCommunicationStatus(
    ApiCommunicationStatus status,
    int requestId,
  ) {
    // An older in-flight request must not overwrite the newest request's
    // transport state, even if the newest request is still pending.
    if (requestId != _requestSequence) return;

    _communicationStatus = status;
    if (!_communicationStatusChanges.isClosed) {
      _communicationStatusChanges.add(status);
    }
  }

  /// Performs a GET request and requires a JSON object response.
  Future<Map<String, dynamic>> getJson(String path, {String? bearerToken}) {
    return _requestJson(method: 'GET', path: path, bearerToken: bearerToken);
  }

  /// Performs a JSON POST request and requires a JSON object response.
  ///
  /// If [bearerToken] is supplied, it is sent as an Authorization Bearer
  /// token. Token acquisition, persistence, and refresh belong to the auth
  /// session layer, not to this transport class.
  ///
  /// [idempotencyKey] must be reused for repeated attempts of the same
  /// logical operation and supplied only for endpoints whose backend
  /// contract supports idempotency. It does not make unsupported endpoints
  /// safe to retry.
  Future<Map<String, dynamic>> postJson(
    String path, {
    required Map<String, dynamic> body,
    String? bearerToken,
    String? idempotencyKey,
  }) {
    return _requestJson(
      method: 'POST',
      path: path,
      body: body,
      bearerToken: bearerToken,
      idempotencyKey: idempotencyKey,
    );
  }

  /// Performs a JSON PATCH request and requires a JSON object response.
  ///
  /// [idempotencyKey] is only appropriate for endpoints whose backend
  /// contract supports idempotency. Reuse it for repeated attempts of the
  /// same logical operation; it does not make unsupported endpoints safe
  /// to retry.
  Future<Map<String, dynamic>> patchJson(
    String path, {
    required Map<String, dynamic> body,
    String? bearerToken,
    String? idempotencyKey,
  }) {
    return _requestJson(
      method: 'PATCH',
      path: path,
      body: body,
      bearerToken: bearerToken,
      idempotencyKey: idempotencyKey,
    );
  }

  static String? _normalizeIdempotencyKey(String? value) {
    if (value == null) return null;

    final normalized = value.trim();
    if (normalized.isEmpty) {
      throw ArgumentError.value(
        value,
        'idempotencyKey',
        'Must be non-empty when provided.',
      );
    }
    if (normalized.length > 255) {
      throw ArgumentError.value(
        value,
        'idempotencyKey',
        'Must not exceed 255 characters.',
      );
    }
    return normalized;
  }

  Future<Map<String, dynamic>> _requestJson({
    required String method,
    required String path,
    Map<String, dynamic>? body,
    String? bearerToken,
    String? idempotencyKey,
  }) async {
    if (bearerToken != null && bearerToken.trim().isEmpty) {
      throw ArgumentError.value(
        bearerToken,
        'bearerToken',
        'Must be non-empty when provided.',
      );
    }

    final normalizedIdempotencyKey = _normalizeIdempotencyKey(idempotencyKey);

    if (!const <String>{'GET', 'POST', 'PATCH'}.contains(method) ||
        (method == 'GET' && body != null) ||
        (method != 'GET' && body == null)) {
      throw StateError('Invalid internal API request configuration.');
    }

    final uri = config.endpoint(path);
    final headers = <String, String>{'Accept': 'application/json'};

    if (body != null) {
      headers['Content-Type'] = 'application/json';
    }

    if (bearerToken != null) {
      headers['Authorization'] = 'Bearer $bearerToken';
    }

    if (normalizedIdempotencyKey != null) {
      headers['Idempotency-Key'] = normalizedIdempotencyKey;
    }

    final requestId = ++_requestSequence;

    late final http.Response response;

    try {
      if (method == 'GET') {
        response = await _client
            .get(uri, headers: headers)
            .timeout(requestTimeout);
      } else if (method == 'POST') {
        response = await _client
            .post(uri, headers: headers, body: jsonEncode(body))
            .timeout(requestTimeout);
      } else {
        response = await _client
            .patch(uri, headers: headers, body: jsonEncode(body))
            .timeout(requestTimeout);
      }
    } on TimeoutException catch (error) {
      _recordCommunicationStatus(
        ApiCommunicationStatus.responseNotReceived,
        requestId,
      );
      throw ApiException(
        kind: ApiFailureKind.timeout,
        message: 'The backend request timed out.',
        uri: uri,
        cause: error,
      );
    } on http.ClientException catch (error) {
      _recordCommunicationStatus(
        ApiCommunicationStatus.responseNotReceived,
        requestId,
      );
      throw ApiException(
        kind: ApiFailureKind.connection,
        message: 'Unable to connect to the backend.',
        uri: uri,
        cause: error,
      );
    }

    // Any HTTP response proves a response arrived, not that the operation
    // succeeded. Status and payload validation remain separate below.
    _recordCommunicationStatus(
      ApiCommunicationStatus.responseReceived,
      requestId,
    );

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(
        kind: ApiFailureKind.httpStatus,
        message: 'The backend returned HTTP ${response.statusCode}.',
        uri: uri,
        statusCode: response.statusCode,
        validationErrors: _parseValidationErrors(response.body),
        serverMessage: _parseServerMessage(response.body),
        serverCode: _parseServerCode(response.body),
      );
    }

    final Object? decoded;

    try {
      decoded = jsonDecode(response.body);
    } on FormatException catch (error) {
      throw ApiException(
        kind: ApiFailureKind.invalidResponse,
        message: 'The backend returned invalid JSON.',
        uri: uri,
        cause: error,
      );
    }

    if (decoded is! Map<String, dynamic>) {
      throw ApiException(
        kind: ApiFailureKind.invalidResponse,
        message: 'The backend response must be a JSON object.',
        uri: uri,
      );
    }

    return decoded;
  }

  Map<String, String> _parseValidationErrors(String body) {
    if (body.isEmpty) return const <String, String>{};

    try {
      final decoded = jsonDecode(body);
      if (decoded is! Map<String, dynamic>) {
        return const <String, String>{};
      }

      final details = decoded['details'];
      if (details is! List) return const <String, String>{};

      final errors = <String, String>{};
      for (final item in details) {
        if (item is! Map<String, dynamic>) continue;
        final location = item['loc'];
        final message = item['msg'];
        if (location is! List || message is! String || message.trim().isEmpty) {
          continue;
        }
        final path = location
            .whereType<String>()
            .where((part) => part != 'body' && part != 'query')
            .toList(growable: false);
        if (path.isEmpty) continue;
        final field = path.last;
        final safeMessage = message.trim();
        errors.update(
          field,
          (previous) => '$previous; $safeMessage',
          ifAbsent: () => safeMessage,
        );
      }
      return Map<String, String>.unmodifiable(errors);
    } on FormatException {
      return const <String, String>{};
    }
  }

  String? _parseServerMessage(String body) {
    if (body.isEmpty) return null;
    try {
      final decoded = jsonDecode(body);
      if (decoded is! Map<String, dynamic>) return null;
      final value = decoded['error'] ?? decoded['message'];
      if (value is! String || value.trim().isEmpty) return null;
      return value.trim();
    } on FormatException {
      return null;
    }
  }

  String? _parseServerCode(String body) {
    if (body.isEmpty) return null;

    try {
      final decoded = jsonDecode(body);
      if (decoded is! Map<String, dynamic>) return null;

      final value = decoded['code'];
      if (value is! String) return null;

      final normalized = value.trim();
      if (!RegExp(r'^[A-Z][A-Z0-9_]{0,63}$').hasMatch(normalized)) {
        return null;
      }

      return normalized;
    } on FormatException {
      return null;
    }
  }

  /// Closes only the internally owned HTTP client.
  void close() {
    if (_ownsClient) {
      _client.close();
    }
    if (!_communicationStatusChanges.isClosed) {
      unawaited(_communicationStatusChanges.close());
    }
  }
}
