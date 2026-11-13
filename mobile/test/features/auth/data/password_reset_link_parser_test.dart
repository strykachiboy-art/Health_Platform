import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/auth/data/password_reset_link_parser.dart';

const _token = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA';

void main() {
  test('backend HTTPS fragment credential with additional verification', () {
    final parsed = PasswordResetLinkParser.parse(
      'https://account.example.test/reset-password#token=$_token&verification=additional',
    );
    expect(parsed.token, _token);
    expect(parsed.additionalVerificationRequired, true);
  });

  test('accepts a raw valid token without inventing verification', () {
    final parsed = PasswordResetLinkParser.parse(_token);
    expect(parsed.token, _token);
    expect(parsed.additionalVerificationRequired, false);
  });

  test('rejects HTTP, query credentials, userinfo and untrusted paths', () {
    for (final input in <String>[
      'http://account.example.test/reset-password#token=$_token',
      'https://user@account.example.test/reset-password#token=$_token',
      'https://account.example.test/reset-password?token=$_token',
      'https://account.example.test/other#token=$_token',
      'https://account.example.test/reset-password',
      'https://account.example.test/reset-password#verification=additional',
      'https://account.example.test/reset-password#token=bad',
    ]) {
      expect(() => PasswordResetLinkParser.parse(input), throwsFormatException);
    }
  });

  test('rejects duplicated fields or unexpected fragment parameters', () {
    for (final suffix in <String>[
      'token=$_token&token=$_token',
      'token=$_token&verification=unknown',
      'token=$_token&role=super_admin',
      'token=$_token&verification=additional&verification=additional',
    ]) {
      expect(
        () => PasswordResetLinkParser.parse(
          'https://account.example.test/reset-password#$suffix',
        ),
        throwsFormatException,
      );
    }
  });

  test('invalid raw token does not pass validation', () {
    for (final input in <String>['', 'x', 'token with spaces', '%%%']) {
      expect(() => PasswordResetLinkParser.parse(input), throwsFormatException);
    }
  });
}
