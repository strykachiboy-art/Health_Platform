import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:cryptography/cryptography.dart';

import 'google_desktop_identity.dart';

const _googleAuthEndpoint = 'https://accounts.google.com/o/oauth2/v2/auth';
const _loopbackWindow = Duration(minutes: 2);

String _randomSecret(int byteCount) {
  final random = Random.secure();
  return base64UrlEncode(
    List<int>.generate(byteCount, (_) => random.nextInt(256)),
  ).replaceAll('=', '');
}

Future<String> _pkceChallenge(String verifier) async {
  final digest = await Sha256().hash(utf8.encode(verifier));
  return base64UrlEncode(digest.bytes).replaceAll('=', '');
}

/// Google installed-app OAuth: system browser, local loopback, state and S256.
/// Code and Google tokens remain in memory; only backend-issued CSP JWTs are
/// persisted by the existing AuthSessionCoordinator.
Future<DesktopGoogleIdentity?> acquireDesktopGoogleIdentity({
  required String clientId,
}) async {
  if (!Platform.isWindows) {
    throw UnsupportedError('Desktop Google OAuth is Windows-only.');
  }
  if (!RegExp(r'^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$')
      .hasMatch(clientId)) {
    throw StateError('Windows Google OAuth client ID is invalid.');
  }

  final state = _randomSecret(32);
  final nonce = _randomSecret(32);
  final verifier = _randomSecret(64);
  final challenge = await _pkceChallenge(verifier);
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  final redirectUri = 'http://127.0.0.1:${server.port}';
  final completion = Completer<Uri>();
  StreamSubscription<HttpRequest>? subscription;
  try {
    subscription = server.listen((request) async {
      final allowed =
          request.method == 'GET' &&
          request.uri.path == '/' &&
          request.uri.queryParameters['state'] == state &&
          request.headers.host == '127.0.0.1' &&
          request.headers.port == server.port &&
          request.connectionInfo?.remoteAddress.isLoopback == true;
      request.response.headers.set('Cache-Control', 'no-store');
      request.response.headers.set('X-Content-Type-Options', 'nosniff');
      request.response.headers.contentType = ContentType.html;
      if (!allowed) {
        request.response.statusCode = HttpStatus.badRequest;
        request.response.write('Invalid authentication callback.');
      } else {
        request.response.write(
          '<!doctype html><title>Clinic System Pro</title>'
          '<p>You can return to Clinic System Pro.</p>',
        );
        if (!completion.isCompleted) completion.complete(request.uri);
      }
      await request.response.close();
    });
    final authUri = Uri.parse(_googleAuthEndpoint).replace(
      queryParameters: <String, String>{
        'client_id': clientId,
        'redirect_uri': redirectUri,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'nonce': nonce,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
        'prompt': 'select_account',
      },
    );
    // Use the Windows HTTPS protocol handler rather than File Explorer.
    // The validated OAuth URI is passed directly, never through a shell.
    await Process.start(
      'rundll32.exe',
      <String>['url.dll,FileProtocolHandler', authUri.toString()],
      mode: ProcessStartMode.detached,
      runInShell: false,
    );
    final callback = await completion.future.timeout(_loopbackWindow);
    if (callback.queryParameters['error'] == 'access_denied') return null;
    if (callback.queryParameters.containsKey('error')) {
      throw StateError('Google authorization was not completed.');
    }
    final code = callback.queryParameters['code'];
    if (code == null || code.isEmpty || code.length > 4096) {
      throw StateError('Google returned no valid authorization code.');
    }
    // Flask exchanges the code using a server-only desktop OAuth credential.
    // Keep verifier, nonce and authorization code in memory only.
    return DesktopGoogleIdentity(
      authorizationCode: code,
      codeVerifier: verifier,
      redirectUri: redirectUri,
      nonce: nonce,
    );
  } on TimeoutException {
    throw StateError('Google sign-in timed out. Try again.');
  } finally {
    await subscription?.cancel();
    await server.close(force: true);
  }
}
