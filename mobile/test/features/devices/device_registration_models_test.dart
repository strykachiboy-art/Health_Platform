import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_models.dart';

void main() {
  test('accepts only backend-supported provider platforms', () {
    expect(RegisteredDevicePlatform.android.wireValue, 'android');
    expect(RegisteredDevicePlatform.ios.wireValue, 'ios');
    expect(RegisteredDevicePlatform.web.wireValue, 'web');
    final token = ProviderDeviceToken(
      deviceToken: 'genuine-provider-value-123456',
      platform: RegisteredDevicePlatform.android,
      deviceName: '  Android phone  ',
    );
    expect(token.deviceName, 'Android phone');
    expect(token.toString(), 'ProviderDeviceToken(redacted)');
    expect(token.toString(), isNot(contains('genuine-provider-value')));
  });

  test('rejects guessed, too-short, oversized or malformed tokens', () {
    for (final value in <String>[
      '',
      'short',
      ' token-123456789',
      'token-123456789\n',
      'x' * 501,
    ]) {
      expect(
        () => ProviderDeviceToken(
          deviceToken: value,
          platform: RegisteredDevicePlatform.web,
        ),
        throwsFormatException,
      );
    }
    expect(
      () => ProviderDeviceToken(
        deviceToken: 'valid-token-12345',
        platform: RegisteredDevicePlatform.android,
        deviceName: 'x' * 101,
      ),
      throwsFormatException,
    );
  });

  test('receipt validates owner, platform, token and strips the token', () {
    final provider = ProviderDeviceToken(
      deviceToken: 'fcm-provider-value-123456789',
      platform: RegisteredDevicePlatform.ios,
      deviceName: 'iPhone',
    );
    final data = <String, dynamic>{
      'success': true,
      'data': <String, dynamic>{
        'id': 7,
        'user_id': 41,
        'device_token': provider.deviceToken,
        'platform': 'ios',
        'device_name': 'iPhone',
        'is_active': true,
      },
    };
    final receipt = DeviceRegistrationReceipt.fromEnvelope(
      data,
      expectedUserId: 41,
      expectedToken: provider,
    );
    expect(receipt.id, 7);
    expect(receipt.userId, 41);
    expect(receipt.platform, RegisteredDevicePlatform.ios);
    expect(receipt.toString(), isNot(contains(provider.deviceToken)));
    for (final wrong in [
      <String, dynamic>{
        ...(data['data'] as Map<String, dynamic>),
        'user_id': 99,
      },
      <String, dynamic>{
        ...(data['data'] as Map<String, dynamic>),
        'platform': 'android',
      },
      <String, dynamic>{
        ...(data['data'] as Map<String, dynamic>),
        'device_token': 'other-token',
      },
      <String, dynamic>{
        ...(data['data'] as Map<String, dynamic>),
        'is_active': false,
      },
    ]) {
      expect(
        () => DeviceRegistrationReceipt.fromEnvelope(
          <String, dynamic>{'success': true, 'data': wrong},
          expectedUserId: 41,
          expectedToken: provider,
        ),
        throwsFormatException,
      );
    }
  });
}
