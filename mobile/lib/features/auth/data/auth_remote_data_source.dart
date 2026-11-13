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
class AuthRemoteDataSource {
  const AuthRemoteDataSource({required ApiClient apiClient})
    : this._(apiClient);

  const AuthRemoteDataSource._(this._apiClient);

  final ApiClient _apiClient;

  static const String _loginPath = 'api/v1/auth/login';
  static const String _googleMobileLoginPath = 'api/v1/auth/google/mobile';
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
