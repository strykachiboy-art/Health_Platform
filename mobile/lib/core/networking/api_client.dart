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

  /// Performs an authenticated JSON PATCH request and expects an object response.
  Future<Map<String, dynamic>> patchJson(
    String path, {
    required Map<String, dynamic> body,
    String? bearerToken,
  }) {
    return _requestJson(
      method: 'PATCH',
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
      throw ArgumentError.value(
        bearerToken,
        'bearerToken',
        'Must be non-empty when provided.',
      );
    }

    if (!const <String>{'GET', 'POST', 'PATCH'}.contains(method) ||
        (method == 'GET' && body != null) ||
        ((method == 'POST' || method == 'PATCH') && body == null)) {
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

  /// Closes only the internally owned HTTP client.
  void close() {
    if (_ownsClient) {
      _client.close();
    }
  }
}
