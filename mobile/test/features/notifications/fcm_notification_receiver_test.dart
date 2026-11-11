import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/notifications/data/fcm_notification_receiver.dart';

void main() {
  test('FCM ID-only opens canonical notification route', () {
    expect(
      pathFromFcmData(<String, dynamic>{
        'kind': 'clinic_notification',
        'notification_id': '127',
      }),
      '/notifications/127',
    );
  });

  test('rejects forged, missing or noncanonical routing data', () {
    final rejects = <Map<String, dynamic>>[
      {},
      {'kind': 'clinic_notification', 'notification_id': '0'},
      {'kind': 'clinic_notification', 'notification_id': '01'},
      {'kind': 'clinic_notification', 'notification_id': '-5'},
      {'kind': 'clinic_notification', 'notification_id': '2147483648'},
      {'kind': 'clinic_notification', 'notification_id': 127},
      {'kind': 'clinic_notification', 'notification_id': '../account'},
      {'kind': 'clinic_notification', 'notification_id': '1?clinic=42'},
      {'kind': 'clinic_notification', 'notification_id': '1', 'clinic_id': '2'},
      {
        'kind': 'clinic_notification',
        'notification_id': '1',
        'url': '/patients/2',
      },
      {'kind': 'patient', 'notification_id': '1'},
    ];
    for (final data in rejects) {
      expect(pathFromFcmData(data), isNull);
    }
  });
}
