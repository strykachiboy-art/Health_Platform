/// Stateless parser for the backend's single-use password-reset link.
///
/// An emailed link is HTTPS and carries the credential ONLY in its fragment:
///   `https://approved-host/reset-password#token=<secret>&verification=additional`
/// This parser does not decide if a host is the app's verified domain or if a
/// token is valid. The backend alone can validate a one-time credential.
///
/// Until the deployed HTTPS domain is approved as an Android App Link, users
/// can securely paste the link (or its raw token) into the reset screen.
class PasswordResetCredential {
  const PasswordResetCredential({
    required this.token,
    required this.additionalVerificationRequired,
  });

  final String token;
  final bool additionalVerificationRequired;
}

class PasswordResetLinkParser {
  const PasswordResetLinkParser._();

  static final RegExp _tokenPattern = RegExp(r'^[A-Za-z0-9_-]{32,512}$');

  static bool _validToken(String value) {
    final match = _tokenPattern.firstMatch(value);
    return match != null && match.group(0) == value;
  }

  static PasswordResetCredential parse(String input) {
    final value = input.trim();
    if (value.isEmpty || value.length > 8192) {
      throw const FormatException('A valid reset credential is required.');
    }
    if (!value.contains('://')) {
      if (!_validToken(value)) {
        throw const FormatException('A valid reset credential is required.');
      }
      return PasswordResetCredential(
        token: value,
        additionalVerificationRequired: false,
      );
    }
    final uri = Uri.tryParse(value);
    if (uri == null ||
        uri.scheme != 'https' ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.path != '/reset-password' ||
        !uri.hasFragment) {
      throw const FormatException('Paste an HTTPS password-reset link.');
    }
    final parts = <String, String>{};
    for (final piece in uri.fragment.split('&')) {
      final split = piece.indexOf('=');
      if (split <= 0) {
        throw const FormatException('Malformed reset-link fragment.');
      }
      final key = Uri.decodeQueryComponent(piece.substring(0, split));
      final value = Uri.decodeQueryComponent(piece.substring(split + 1));
      if (!const {'token', 'verification'}.contains(key) ||
          parts.containsKey(key)) {
        throw const FormatException(
          'Unexpected or duplicate reset-link field.',
        );
      }
      parts[key] = value;
    }
    final token = parts['token'];
    if (token == null || !_validToken(token)) {
      throw const FormatException('A valid reset credential is required.');
    }
    if (parts.containsKey('verification') &&
        parts['verification'] != 'additional') {
      throw const FormatException('Unsupported reset verification state.');
    }
    return PasswordResetCredential(
      token: token,
      additionalVerificationRequired: parts['verification'] == 'additional',
    );
  }
}
