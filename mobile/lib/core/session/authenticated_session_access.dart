typedef AuthenticatedAccessTokenLoader = Future<String> Function();

enum AuthenticatedSessionAccessFailure { signedOut, recoveryRequired }

class AuthenticatedSessionUnavailableException implements Exception {
  const AuthenticatedSessionUnavailableException(this.failure);

  final AuthenticatedSessionAccessFailure failure;

  @override
  String toString() => 'AuthenticatedSessionUnavailableException($failure)';
}
