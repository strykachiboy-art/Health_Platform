/// P24B backend device registration contract: only genuine platform push
/// tokens are accepted. No token is written to disk or displayed in the UI.
enum RegisteredDevicePlatform { android, ios, web }

extension RegisteredDevicePlatformWire on RegisteredDevicePlatform {
  String get wireValue => switch (this) {
    RegisteredDevicePlatform.android => 'android',
    RegisteredDevicePlatform.ios => 'ios',
    RegisteredDevicePlatform.web => 'web',
  };
}

class ProviderDeviceToken {
  ProviderDeviceToken({
    required this.deviceToken,
    required this.platform,
    String? deviceName,
  }) : deviceName = _normalizeName(deviceName) {
    if (deviceToken.length < 10 ||
        deviceToken.length > 500 ||
        deviceToken.trim() != deviceToken ||
        deviceToken.contains('\n') ||
        deviceToken.contains('\r')) {
      throw const FormatException('Invalid provider device token.');
    }
  }

  /// Secret push-provider value; never log, display, persist or use in a URL.
  final String deviceToken;
  final RegisteredDevicePlatform platform;
  final String? deviceName;

  static String? _normalizeName(String? value) {
    if (value == null) {
      return null;
    }
    final normalized = value.trim();
    if (normalized.length > 100 ||
        normalized.contains('\n') ||
        normalized.contains('\r')) {
      throw const FormatException('Invalid device name.');
    }
    return normalized.isEmpty ? null : normalized;
  }

  @override
  String toString() => 'ProviderDeviceToken(redacted)';
}

/// Intentionally omits the backend's device_token response field. The data
/// source verifies token equality transiently and drops it immediately.
class DeviceRegistrationReceipt {
  const DeviceRegistrationReceipt({
    required this.id,
    required this.userId,
    required this.platform,
    required this.deviceName,
    required this.isActive,
  });

  final int id;
  final int userId;
  final RegisteredDevicePlatform platform;
  final String? deviceName;
  final bool isActive;

  static DeviceRegistrationReceipt fromEnvelope(
    Map<String, dynamic> envelope, {
    required int expectedUserId,
    required ProviderDeviceToken expectedToken,
  }) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid device registration envelope.');
    }
    final data = envelope['data'] as Map<String, dynamic>;
    final id = data['id'];
    final userId = data['user_id'];
    final isActive = data['is_active'];
    final deviceName = data['device_name'];
    if (id is! int ||
        id < 1 ||
        userId is! int ||
        userId != expectedUserId ||
        data['device_token'] != expectedToken.deviceToken ||
        data['platform'] != expectedToken.platform.wireValue ||
        isActive != true ||
        (deviceName != null && deviceName is! String) ||
        deviceName != expectedToken.deviceName) {
      throw const FormatException('Device registration response mismatch.');
    }
    return DeviceRegistrationReceipt(
      id: id,
      userId: userId,
      platform: expectedToken.platform,
      deviceName: deviceName as String?,
      isActive: true,
    );
  }
}
