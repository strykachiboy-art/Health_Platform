import 'auth_mfa_challenge.dart';

/// Validated token payload returned by the backend authentication API.
///
/// This model represents a response only. It does not persist tokens,
/// manage session expiry, or log sensitive authentication data.
class AuthTokenResponse {
  const AuthTokenResponse({
    required this.accessToken,
    required this.refreshToken,
    required this.userId,
    required this.role,
    required this.clinicContextId,
    this.tenantId,
  });

  final String accessToken;
  final String refreshToken;
  final int userId;
  final String role;
  final int? clinicContextId;

  /// Server-approved hospital/organization UUID; never a local authorization grant.
  final String? tenantId;

  /// Parses the complete `{success, data}` backend response envelope.
  factory AuthTokenResponse.fromEnvelope(Map<String, dynamic> envelope) {
    if (envelope['success'] != true) {
      throw const FormatException(
        'Authentication response did not indicate success.',
      );
    }

    final Object? payload = envelope['data'];

    if (payload is! Map<String, dynamic>) {
      throw const FormatException(
        'Authentication response data must be a JSON object.',
      );
    }

    if (payload['mfa_required'] == true) {
      final token = payload['challenge_token'];
      final ttl = payload['challenge_expires_in'];
      if (payload.containsKey('access_token') ||
          payload.containsKey('refresh_token') ||
          token is! String ||
          token.length < 40 ||
          token.length > 256 ||
          !RegExp(r'^[A-Za-z0-9_-]{40,256}$').hasMatch(token) ||
          ttl is! int ||
          ttl < 60 ||
          ttl > 600 ||
          payload['method'] != 'authenticator_or_recovery_code') {
        throw const FormatException('Invalid privileged MFA challenge.');
      }
      throw AuthMfaChallenge(
        challengeToken: token,
        expiresInSeconds: ttl,
      );
    }
    return AuthTokenResponse.fromJson(payload);
  }

  /// Parses the backend's token payload.
  factory AuthTokenResponse.fromJson(Map<String, dynamic> json) {
    final accessToken = _requiredNonEmptyString(json, 'access_token');
    final refreshToken = _requiredNonEmptyString(json, 'refresh_token');
    final role = _requiredNonEmptyString(json, 'role');

    final Object? userIdValue = json['user_id'];
    if (userIdValue is! int || userIdValue <= 0) {
      throw const FormatException(
        'Authentication response user_id must be a positive integer.',
      );
    }

    final Object? clinicContextValue = json['clinic_context_id'];
    if (clinicContextValue != null && clinicContextValue is! int) {
      throw const FormatException(
        'Authentication response clinic_context_id must be an integer or null.',
      );
    }

    final Object? rawTenantId = json['tenant_id'];
    if (rawTenantId != null &&
        (rawTenantId is! String ||
            !RegExp(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')
                .hasMatch(rawTenantId))) {
      throw const FormatException(
        'Authentication response tenant_id must be a UUID or null.',
      );
    }

    return AuthTokenResponse(
      accessToken: accessToken,
      refreshToken: refreshToken,
      userId: userIdValue,
      role: role,
      clinicContextId: clinicContextValue as int?,
      tenantId: rawTenantId as String?,
    );
  }

  static String _requiredNonEmptyString(
    Map<String, dynamic> json,
    String field,
  ) {
    final Object? value = json[field];

    if (value is! String || value.isEmpty || value.trim() != value) {
      throw FormatException(
        'Authentication response field "$field" must be a non-empty string.',
      );
    }

    return value;
  }
}
