import 'dart:async';

import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:go_router/go_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/notifications/data/notification_deep_link_contract.dart';

/// Background messages are intentionally never used to read PHI, store tokens,
/// call clinic APIs, or navigate. Android displays the generic notification.
@pragma('vm:entry-point')
Future<void> clinicFcmBackgroundHandler(RemoteMessage _) async {}

/// P24D payload contract: exactly an untrusted ID plus an inert kind tag.
/// No clinic/user IDs, arbitrary URLs, records, or content from FCM are used.
String? pathFromFcmData(Map<String, dynamic> data) {
  if (data.length != 2 || data['kind'] != 'clinic_notification') {
    return null;
  }
  final value = data['notification_id'];
  if (value is! String) return null;
  final link = NotificationDeepLink.parse('/notifications/$value');
  return link == null
      ? null
      : NotificationDeepLink.pathForId(link.notificationId);
}

/// Route opens are never stored pending sign-in. After login the user can use
/// their authenticated inbox; no stale push grants a cross-session navigation.
class FcmNotificationReceiver {
  FcmNotificationReceiver(this._messaging);

  final FirebaseMessaging _messaging;
  StreamSubscription<RemoteMessage>? _opened;
  StreamSubscription<RemoteMessage>? _foreground;
  int _generation = 0;

  void start({
    required GoRouter router,
    required SessionNavigationGuard? guard,
  }) {
    _generation++;
    final generation = _generation;
    void handle(RemoteMessage message) {
      if (_generation != generation ||
          guard?.canAccessProtectedRoutes != true) {
        return;
      }
      final path = pathFromFcmData(message.data);
      if (path == null) return;
      // Existing P24C direct route requires a fresh, scoped backend GET.
      router.go(path);
    }

    _opened = FirebaseMessaging.onMessageOpenedApp.listen(handle);
    // Foreground messages do not self-navigate and do not display PHI.
    _foreground = FirebaseMessaging.onMessage.listen((_) {});
    unawaited(
      _messaging
          .getInitialMessage()
          .then((message) {
            if (message != null) handle(message);
          })
          .catchError((Object _) {}),
    );
  }

  Future<void> dispose() async {
    _generation++;
    await _opened?.cancel();
    await _foreground?.cancel();
  }
}
