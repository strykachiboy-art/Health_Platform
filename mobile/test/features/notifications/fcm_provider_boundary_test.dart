import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_models.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';

void main() {
  test('only authorized selected-clinic roles see configured device entry', () {
    bool hasEntry(String role, int? clinic, {bool configured = false}) =>
        homeDestinationsFor(
          role: role,
          clinicContextId: clinic,
          deviceRegistrationConfigured: configured,
        ).any((destination) => destination.id == 'device_registration');
    expect(hasEntry('patient', 3, configured: true), isTrue);
    expect(hasEntry('doctor', 3, configured: true), isTrue);
    expect(hasEntry('patient', null, configured: true), isFalse);
    expect(hasEntry('super_admin', 3, configured: true), isFalse);
    expect(hasEntry('patient', 3), isFalse);
  });

  test(
    'backend registration wire contract still accepts opaque Android FID',
    () {
      final value = ProviderDeviceToken(
        deviceToken: 'fid_ABCDEFGHIJKLMNOPQRSTUVWXYZ12345',
        platform: RegisteredDevicePlatform.android,
        deviceName: 'Android device',
      );
      expect(value.platform.wireValue, 'android');
      expect(value.deviceName, 'Android device');
      expect(value.toString(), isNot(contains('ABCDEFGHIJKLMNOPQRSTUVWXYZ')));
    },
  );
}
