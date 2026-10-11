enum ApiFailureKind { timeout, connection, httpStatus, invalidResponse }

class ApiException implements Exception {
  const ApiException({
    required this.kind,
    required this.message,
    required this.uri,
    this.statusCode,
    this.cause,
    this.validationErrors = const <String, String>{},
    this.serverMessage,
  });

  final ApiFailureKind kind;
  final String message;
  final Uri uri;
  final int? statusCode;
  final Object? cause;

  /// Server field messages only; raw response bodies and submitted values
  /// are deliberately not retained here.
  final Map<String, String> validationErrors;
  final String? serverMessage;

  @override
  String toString() {
    final status = statusCode == null ? '' : ', statusCode: $statusCode';
    return 'ApiException(${kind.name}: $message$status)';
  }
}
