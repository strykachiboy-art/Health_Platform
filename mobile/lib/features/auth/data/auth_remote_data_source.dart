import '../../../core/networking/api_client.dart';
import 'models/auth_token_response.dart';

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
