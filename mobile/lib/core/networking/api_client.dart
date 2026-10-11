import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/backend_config.dart';
import 'api_exception.dart';

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

  /// Performs a GET request and requires a JSON object response.
  Future<Map<String, dynamic>> getJson(String path, {String? bearerToken}) {
    return _requestJson(method: 'GET', path: path, bearerToken: bearerToken);
  }

  /// Performs a JSON POST request and requires a JSON object response.
  ///
  /// If [bearerToken] is supplied, it is sent as an Authorization Bearer
  /// token. Token acquisition, persistence, and refresh belong to the auth
  /// session layer, not to this transport class.
  Future<Map<String, dynamic>> postJson(
    String path, {
    required Map<String, dynamic> body,
    String? bearerToken,
  }) {
    return _requestJson(
      method: 'POST',
      path: path,
      body: body,
      bearerToken: bearerToken,
    );
  }

  Future<Map<String, dynamic>> _requestJson({
    required String method,
    required String path,
    Map<String, dynamic>? body,
    String? bearerToken,
  }) async {
    if (bearerToken != null && bearerToken.trim().isEmpty) {
      throw ArgumentError('bearerToken must be non-empty when provided.');
    }

    if ((method == 'GET' && body != null) ||
        (method == 'POST' && body == null)) {
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

    late final http.Response response;

    try {
      if (method == 'GET') {
        response = await _client
            .get(uri, headers: headers)
            .timeout(requestTimeout);
      } else {
        response = await _client
            .post(uri, headers: headers, body: jsonEncode(body))
            .timeout(requestTimeout);
      }
    } on TimeoutException catch (error) {
      throw ApiException(
        kind: ApiFailureKind.timeout,
        message: 'The backend request timed out.',
        uri: uri,
        cause: error,
      );
    } on http.ClientException catch (error) {
      throw ApiException(
        kind: ApiFailureKind.connection,
        message: 'Unable to connect to the backend.',
        uri: uri,
        cause: error,
      );
    }

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(
        kind: ApiFailureKind.httpStatus,
        message: 'The backend returned HTTP ${response.statusCode}.',
        uri: uri,
        statusCode: response.statusCode,
        validationErrors: _parseValidationErrors(response.body),
        serverMessage: _parseServerMessage(response.body),
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

  /// Closes only the internally owned HTTP client.
  void close() {
    if (_ownsClient) {
      _client.close();
    }
  }
}
