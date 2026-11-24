import '../../../core/networking/api_client.dart';
import 'models/auth_token_response.dart';

/// Current clinic context is read from a backend-authenticated GET.
class ClinicContextDetails {
  const ClinicContextDetails({
    required this.clinicId,
    required this.clinicName,
    required this.source,
  });

  final int? clinicId;
  final String? clinicName;
  final String source;

  factory ClinicContextDetails.fromEnvelope(Map<String, dynamic> value) {
    if (value['success'] != true) {
      throw const FormatException('Clinic context GET failed.');
    }
    final data = value['data'];
    if (data is! Map<String, dynamic>) {
      throw const FormatException('Clinic context data must be an object.');
    }
    final id = data['clinic_id'];
    final name = data['clinic_name'];
    final source = data['source'];
    if ((id != null && (id is! int || id <= 0)) ||
        (name != null && (name is! String || name.trim().isEmpty)) ||
        source is! String ||
        !const {'system', 'selected', 'assigned'}.contains(source) ||
        (id == null && name != null) ||
        (id != null && name == null)) {
      throw const FormatException('Invalid clinic context response.');
    }
    return ClinicContextDetails(
      clinicId: id as int?,
      clinicName: name as String?,
      source: source,
    );
  }
}

/// Remote boundary for backend authentication operations.
///
/// Token persistence and session lifecycle belong to other layers.
/// Trusted only after the backend has verified the bearer and its grants.
class TenantContextDetails {
  const TenantContextDetails({required this.tenantId, required this.clinicId});

  final String? tenantId;
  final int? clinicId;

  factory TenantContextDetails.fromEnvelope(Map<String, dynamic> response) {
    if (response['success'] != true ||
        response['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid tenant context response.');
    }
    final data = response['data'] as Map<String, dynamic>;
    if (data.keys.toSet().difference(const {
          'tenant_id',
          'clinic_id',
        }).isNotEmpty ||
        !data.containsKey('tenant_id') ||
        !data.containsKey('clinic_id')) {
      throw const FormatException('Unexpected tenant context fields.');
    }
    final tenant = data['tenant_id'];
    final clinic = data['clinic_id'];
    if ((tenant != null &&
            (tenant is! String ||
                !RegExp(
                  r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$',
                ).hasMatch(tenant))) ||
        (clinic != null && (clinic is! int || clinic <= 0)) ||
        (clinic != null && tenant == null)) {
      throw const FormatException('Untrusted tenant/clinic identifiers.');
    }
    return TenantContextDetails(
      tenantId: tenant as String?,
      clinicId: clinic as int?,
    );
  }
}

class MfaEnrollmentStatus {
  const MfaEnrollmentStatus({required this.enrollmentEnabled, required this.enrolled});
  final bool enrollmentEnabled;
  final bool enrolled;
}

class AuthRemoteDataSource {
  const AuthRemoteDataSource({required ApiClient apiClient})
    : this._(apiClient);

  const AuthRemoteDataSource._(this._apiClient);

  final ApiClient _apiClient;

  static const String _loginPath = 'api/v1/auth/login';
  static const String _googleMobileLoginPath = 'api/v1/auth/google/mobile';
  static const String _googleDesktopLoginPath = 'api/v1/auth/google/desktop';
  static const String _googleDesktopCodeLoginPath =
      'api/v1/auth/google/desktop/code';

  /// Flask performs the one-time code exchange; client secret never on Windows.
  Future<AuthTokenResponse> loginWithDesktopGoogleAuthorizationCode({
    required String code,
    required String codeVerifier,
    required String redirectUri,
    required String nonce,
  }) async {
    if (code.isEmpty ||
        code.length > 4096 ||
        !RegExp(r'^[A-Za-z0-9._~-]{43,128}$').hasMatch(codeVerifier) ||
        !RegExp(r'^http://127\.0\.0\.1:[0-9]{2,5}$').hasMatch(redirectUri) ||
        !RegExp(r'^[A-Za-z0-9_-]{43,128}$').hasMatch(nonce)) {
      throw ArgumentError('Invalid desktop Google code handoff.');
    }
    final result = await _apiClient.postJson(
      _googleDesktopCodeLoginPath,
      body: <String, dynamic>{
        'code': code,
        'code_verifier': codeVerifier,
        'redirect_uri': redirectUri,
        'nonce': nonce,
      },
    );
    return AuthTokenResponse.fromEnvelope(result);
  }

  /// Desktop Google ID token verified by Flask against its own allowlisted
  /// audience and expected nonce. CSP tokens only come from Flask.
  Future<AuthTokenResponse> loginWithDesktopGoogleIdToken({
    required String idToken,
    required String nonce,
  }) async {
    if (idToken.isEmpty || nonce.isEmpty) {
      throw ArgumentError('Google desktop identity is incomplete.');
    }
    final response = await _apiClient.postJson(
      _googleDesktopLoginPath,
      body: {'id_token': idToken, 'nonce': nonce},
    );
    return AuthTokenResponse.fromEnvelope(response);
  }

  static const String _refreshPath = 'api/v1/auth/refresh';
  static const String _logoutPath = 'api/v1/auth/logout';

  Future<AuthTokenResponse> login({
    required String email,
    required String password,
  }) async {
    final response = await _apiClient.postJson(
      _loginPath,
      body: {'email': email, 'password': password},
    );

    return AuthTokenResponse.fromEnvelope(response);
  }

  static const String _passwordResetRequestPath =
      'api/v1/auth/password-reset/request';
  static const String _passwordResetConfirmPath =
      'api/v1/auth/password-reset/confirm';

  /// The backend returns the same public response for existing and unknown
  /// accounts. Never disclose account eligibility to Flutter callers.
  Future<void> requestPasswordReset({required String email}) async {
    if (email.isEmpty ||
        email.length > 254 ||
        email.trim() != email ||
        !email.contains('@')) {
      throw ArgumentError('A valid email address is required.');
    }
    final response = await _apiClient.postJson(
      _passwordResetRequestPath,
      body: {'email': email},
    );
    if (response['success'] != true) {
      throw const FormatException('Password reset request was not confirmed.');
    }
  }

  /// Single attempt only. Backend atomically validates/consumes the token,
  /// applies any recovery factor, and revokes existing account sessions.
  /// Callers must not automatically retry ambiguous outcomes.
  Future<void> confirmPasswordReset({
    required String token,
    required String newPassword,
    required String confirmPassword,
    String? recoveryFactor,
  }) async {
    if (token.length < 32 || token.length > 512 || token.trim() != token) {
      throw ArgumentError('A valid reset credential is required.');
    }
    if (newPassword.length < 8 ||
        newPassword.length > 128 ||
        newPassword.trim().isEmpty ||
        newPassword != confirmPassword) {
      throw ArgumentError('Matching valid new passwords are required.');
    }
    if (recoveryFactor != null &&
        (recoveryFactor.isEmpty ||
            recoveryFactor.length > 512 ||
            recoveryFactor.trim() != recoveryFactor)) {
      throw ArgumentError('Invalid additional recovery verification.');
    }
    final response = await _apiClient.postJson(
      _passwordResetConfirmPath,
      body: <String, dynamic>{
        'token': token,
        'new_password': newPassword,
        'confirm_password': confirmPassword,
        'recovery_factor': ?recoveryFactor,
      },
    );
    if (response['success'] != true ||
        response['data'] is! Map<String, dynamic> ||
        (response['data']
                as Map<String, dynamic>)['reauthentication_required'] !=
            true) {
      throw const FormatException('Password reset confirmation was invalid.');
    }
  }

  /// Sends a Google ID token to the backend for verification.
  Future<AuthTokenResponse> loginWithGoogleIdToken({
    required String idToken,
  }) async {
    _requireGoogleIdToken(idToken);

    final response = await _apiClient.postJson(
      _googleMobileLoginPath,
      body: {'id_token': idToken},
    );

    return AuthTokenResponse.fromEnvelope(response);
  }

  /// A pending challenge is not an authenticated session. This call returns
  /// actual JWTs only after the backend consumes one valid MFA factor.
  Future<AuthTokenResponse> completeMfa({
    required String challengeToken,
    required String factor,
  }) async {
    if (challengeToken.length < 40 || challengeToken.length > 256 ||
        !RegExp(r'^[A-Za-z0-9_-]+$').hasMatch(challengeToken) ||
        !RegExp(r'^(?:[0-9]{6}|[A-Z2-7]{32})$').hasMatch(factor)) {
      throw ArgumentError('Invalid MFA verification input.');
    }
    final result = await _apiClient.postJson(
      'api/v1/auth/mfa/complete',
      body: {'challenge_token': challengeToken, 'factor': factor},
    );
    return AuthTokenResponse.fromEnvelope(result);
  }

  Future<MfaEnrollmentStatus> getMfaEnrollmentStatus({
    required String accessToken,
  }) async {
    _requireToken(accessToken, 'accessToken');
    final result = await _apiClient.getJson(
      'api/v1/auth/mfa/enrollment/status',
      bearerToken: accessToken,
    );
    final data = result['data'];
    if (result['success'] != true || data is! Map<String, dynamic> ||
        data['enrollment_enabled'] is! bool ||
        (data['enrolled'] != null && data['enrolled'] is! bool) ||
        (data['enrollment_enabled'] == true && data['enrolled'] == null)) {
      throw const FormatException('Invalid MFA enrollment status.');
    }
    return MfaEnrollmentStatus(
      enrollmentEnabled: data['enrollment_enabled'] as bool,
      enrolled: data['enrolled'] == true,
    );
  }

  Future<String> beginMfaEnrollment({
    required String accessToken,
    required String currentPassword,
  }) async {
    _requireToken(accessToken, 'accessToken');
    if (currentPassword.isEmpty || currentPassword.length > 128) {
      throw ArgumentError('Current password is required.');
    }
    final response = await _apiClient.postJson(
      'api/v1/auth/mfa/enrollment/begin',
      bearerToken: accessToken,
      body: {'current_password': currentPassword},
    );
    final data = response['data'];
    if (response['success'] != true || data is! Map<String, dynamic>) {
      throw const FormatException('Invalid MFA setup response.');
    }
    final secret = data['setup_secret'];
    if (secret is! String ||
        !RegExp(r'^[A-Z2-7]{52}$').hasMatch(secret) ||
        data['expires_in'] != 600) {
      throw const FormatException('Invalid MFA setup response.');
    }
    return secret;
  }

  /// One-time recovery codes remain in the caller's memory only.
  Future<List<String>> confirmMfaEnrollment({
    required String accessToken,
    required String code,
  }) async {
    _requireToken(accessToken, 'accessToken');
    if (!RegExp(r'^[0-9]{6}$').hasMatch(code)) {
      throw ArgumentError('Invalid authenticator confirmation.');
    }
    final response = await _apiClient.postJson(
      'api/v1/auth/mfa/enrollment/confirm',
      bearerToken: accessToken,
      body: {'code': code},
    );
    final data = response['data'];
    final codes = data is Map<String, dynamic> ? data['recovery_codes'] : null;
    if (response['success'] != true || data is! Map<String, dynamic> ||
        data['reauthentication_required'] != true ||
        codes is! List || codes.length != 10 ||
        !codes.every((value) => value is String &&
            RegExp(r'^[A-Z2-7]{32}$').hasMatch(value)) ||
        codes.toSet().length != codes.length) {
      throw const FormatException('Invalid MFA recovery code response.');
    }
    return List<String>.unmodifiable(codes.cast<String>());
  }

  static const String _tenantContextPath = 'api/v1/auth/tenant-context';

  Future<TenantContextDetails> getTenantContext({
    required String accessToken,
  }) async {
    _requireToken(accessToken, 'accessToken');
    final response = await _apiClient.getJson(
      _tenantContextPath,
      bearerToken: accessToken,
    );
    return TenantContextDetails.fromEnvelope(response);
  }

  Future<AuthTokenResponse> selectTenantContext({
    required String accessToken,
    required String organizationId,
  }) async {
    _requireToken(accessToken, 'accessToken');
    if (!RegExp(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')
        .hasMatch(organizationId)) {
      throw ArgumentError('organizationId must be a canonical UUID.');
    }
    final response = await _apiClient.postJson(
      _tenantContextPath,
      body: {'organization_id': organizationId},
      bearerToken: accessToken,
    );
    return AuthTokenResponse.fromEnvelope(response);
  }

  Future<AuthTokenResponse> selectMemberClinicContext({
    required String accessToken,
    required int clinicId,
  }) async {
    _requireToken(accessToken, 'accessToken');
    if (clinicId <= 0) throw ArgumentError('clinicId must be positive.');
    final response = await _apiClient.postJson(
      '$_tenantContextPath/clinic',
      body: {'clinic_id': clinicId},
      bearerToken: accessToken,
    );
    return AuthTokenResponse.fromEnvelope(response);
  }

  Future<AuthTokenResponse> clearTenantContext({
    required String accessToken,
  }) async {
    _requireToken(accessToken, 'accessToken');
    final response = await _apiClient.deleteJson(
      _tenantContextPath,
      bearerToken: accessToken,
    );
    return AuthTokenResponse.fromEnvelope(response);
  }

  static const String _clinicContextPath = 'api/v1/auth/clinic-context';

  Future<ClinicContextDetails> getClinicContext({
    required String accessToken,
  }) async {
    _requireToken(accessToken, 'accessToken');
    final result = await _apiClient.getJson(
      _clinicContextPath,
      bearerToken: accessToken,
    );
    return ClinicContextDetails.fromEnvelope(result);
  }

  /// These endpoints are server-audited and issue a complete replacement pair.
  /// A caller must not automatically retry after an ambiguous response.
  Future<AuthTokenResponse> selectClinicContext({
    required String accessToken,
    required int clinicId,
  }) async {
    _requireToken(accessToken, 'accessToken');
    if (clinicId <= 0) {
      throw ArgumentError.value(clinicId, 'clinicId', 'Must be positive.');
    }
    final result = await _apiClient.postJson(
      _clinicContextPath,
      body: {'clinic_id': clinicId},
      bearerToken: accessToken,
    );
    return AuthTokenResponse.fromEnvelope(result);
  }

  Future<AuthTokenResponse> clearClinicContext({
    required String accessToken,
  }) async {
    _requireToken(accessToken, 'accessToken');
    final result = await _apiClient.deleteJson(
      _clinicContextPath,
      bearerToken: accessToken,
    );
    return AuthTokenResponse.fromEnvelope(result);
  }

  /// Exchanges the current refresh token for a replacement token pair.
  ///
  /// The backend rotates refresh tokens. This method deliberately does not
  /// retry a request whose outcome is uncertain.
  Future<AuthTokenResponse> refresh({required String refreshToken}) async {
    _requireToken(refreshToken, 'refreshToken');

    final response = await _apiClient.postJson(
      _refreshPath,
      body: const <String, dynamic>{},
      bearerToken: refreshToken,
    );

    return AuthTokenResponse.fromEnvelope(response);
  }

  /// Logs out using the access token and its associated refresh token.
  Future<void> logout({
    required String accessToken,
    required String refreshToken,
  }) async {
    _requireToken(accessToken, 'accessToken');
    _requireToken(refreshToken, 'refreshToken');

    final response = await _apiClient.postJson(
      _logoutPath,
      body: {'refresh_token': refreshToken},
      bearerToken: accessToken,
    );

    if (response['success'] != true) {
      throw const FormatException('Logout response did not indicate success.');
    }
  }

  void _requireGoogleIdToken(String token) {
    if (token.isEmpty || token.trim() != token || token.length > 8192) {
      throw ArgumentError(
        'idToken must be non-empty, trimmed, and at most 8192 characters.',
      );
    }
  }

  void _requireToken(String token, String field) {
    if (token.trim().isEmpty || token.trim() != token) {
      throw ArgumentError.value(
        token,
        field,
        'Must be non-empty without surrounding whitespace.',
      );
    }
  }
}
