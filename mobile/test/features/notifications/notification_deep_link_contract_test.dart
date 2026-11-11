import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/notifications/data/notification_deep_link_contract.dart';

void main() {
  test('canonical notification IDs produce no sensitive URL fields', () {
    for (final id in <int>[1, 7, 2147483647]) {
      final path = NotificationDeepLink.pathForId(id);
      expect(path, '/notifications/$id');
      expect(NotificationDeepLink.parse(path)?.notificationId, id);
      expect(path.contains('?'), isFalse);
      expect(path.contains('#'), isFalse);
    }
  });

  test('invalid, ambiguous, and external link destinations fail closed', () {
    for (final raw in <String>[
      '',
      '/',
      '/notifications',
      '/notifications/',
      '/notifications/0',
      '/notifications/01',
      '/notifications/-1',
      '/notifications/2147483648',
      '/notifications/1234567890123456789',
      '/notifications/7/',
      '/notifications/7?clinic_id=3',
      '/notifications/7?access_token=secret',
      '/notifications/7#detail',
      '/notifications/%37',
      '/notifications/7%2F8',
      '/notifications/7/../8',
      '/notifications/7/read',
      '/notifications/7/../../patients',
      '/patients/7',
      '/appointments/7',
      'https://hospital.example/notifications/7',
      'clinic-system-pro://notifications/7',
      '//evil.example/notifications/7',
      ' /notifications/7',
      '/notifications/7 ',
      '/notifications/٧',
      '/NOTIFICATIONS/7',
    ]) {
      expect(NotificationDeepLink.parse(raw), isNull, reason: raw);
    }
    for (final id in <int>[0, -1, 2147483648]) {
      expect(() => NotificationDeepLink.pathForId(id), throwsArgumentError);
    }
  });
}
