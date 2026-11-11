/// P24C: an incoming link is an untrusted notification ID, never an access
/// grant. No external hosts, query parameters, clinic IDs, user IDs, tokens,
/// notification content, or arbitrary reference destinations are accepted.
///
/// Platform app-link/domain association is intentionally not configured here;
/// P24D must select a verified notification transport and trusted origin.
class NotificationDeepLink {
  const NotificationDeepLink._(this.notificationId);

  final int notificationId;

  static final RegExp _pathPattern = RegExp(
    r'^/notifications/([1-9][0-9]{0,9})$',
  );

  /// Accepts one canonical, relative route only. Every accepted route still
  /// requires fresh authentication and a backend-authorized notification GET.
  static NotificationDeepLink? parse(String raw) {
    final match = _pathPattern.firstMatch(raw);
    if (match == null) {
      return null;
    }
    final id = int.tryParse(match.group(1)!);
    if (id == null || id > 2147483647) {
      return null;
    }
    return NotificationDeepLink._(id);
  }

  static String pathForId(int notificationId) {
    if (notificationId < 1 || notificationId > 2147483647) {
      throw ArgumentError.value(
        notificationId,
        'notificationId',
        'Notification ID must be a positive 32-bit integer.',
      );
    }
    return '/notifications/$notificationId';
  }
}
