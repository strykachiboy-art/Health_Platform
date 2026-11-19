import 'google_desktop_identity.dart';

Future<DesktopGoogleIdentity?> acquireDesktopGoogleIdentity({
  required String clientId,
}) => throw UnsupportedError('Desktop Google OAuth requires Windows.');
