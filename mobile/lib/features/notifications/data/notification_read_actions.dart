import 'notification_models.dart';
import 'notification_remote_data_source.dart';

/// Router-lifetime action guard. Never repeats a POST with an unknown outcome.
/// Keys are bound to user, clinic and guard revision, not widget lifetime.
class NotificationReadActions {
  NotificationReadActions(this.source);

  final NotificationRemoteDataSource source;
  final Set<String> _attempted = <String>{};

  Future<ClinicNotification> markOne(int notificationId) async {
    if (notificationId < 1) throw ArgumentError('Invalid notification ID.');
    final identity = await source.requireSession();
    final key = '${identity.actionScope}/read/$notificationId';
    if (!_attempted.add(key)) {
      throw const NotificationWriteOutcomeUnconfirmed();
    }
    return source.markRead(notificationId, expectedScope: identity.actionScope);
  }

  Future<int> markAll() async {
    final identity = await source.requireSession();
    final key = '${identity.actionScope}/read-all';
    if (!_attempted.add(key)) {
      throw const NotificationWriteOutcomeUnconfirmed();
    }
    final count = await source.markAllRead(expectedScope: identity.actionScope);
    // A verified successful read-all may be repeated after new notifications.
    // An unconfirmed call remains permanently locked for this session scope.
    _attempted.remove(key);
    return count;
  }
}
