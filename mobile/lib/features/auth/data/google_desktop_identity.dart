/// One-time desktop Google ID token and its OIDC nonce. Never persist this object.
class DesktopGoogleIdentity {
  const DesktopGoogleIdentity({
    this.idToken = '',
    this.authorizationCode,
    this.codeVerifier,
    this.redirectUri,
    required this.nonce,
  });

  /// Legacy verified-ID-token handoff for existing unit tests and SDK clients.
  final String idToken;

  /// One-time authorization code; exchanged on Flask, never on Windows.
  final String? authorizationCode;
  final String? codeVerifier;
  final String? redirectUri;
  final String nonce;
}
