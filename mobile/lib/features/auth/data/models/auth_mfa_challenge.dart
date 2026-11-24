/// Pending Super Admin MFA challenge returned by the backend after successful
/// primary authentication. NOT a JWT and NEVER a persisted login session.
class AuthMfaChallenge implements Exception {
  const AuthMfaChallenge({
    required this.challengeToken,
    required this.expiresInSeconds,
  });

  final String challengeToken;
  final int expiresInSeconds;

  // Never embed the bearer-like challenge secret in logs or exception text.
  @override
  String toString() => 'Additional authentication required.';
}
