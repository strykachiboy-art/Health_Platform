import 'google_desktop_identity.dart';
import 'google_desktop_oauth_stub.dart'
    if (dart.library.io) 'google_desktop_oauth_io.dart'
    as platform;

/// Only used on Windows. On other platforms the native SDK stays authoritative.
Future<DesktopGoogleIdentity?> acquireDesktopGoogleIdentity({
  required String clientId,
}) => platform.acquireDesktopGoogleIdentity(clientId: clientId);
