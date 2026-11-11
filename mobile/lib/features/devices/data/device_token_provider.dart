import 'device_registration_models.dart';

/// Provider-owned token acquisition boundary for Android/iOS/web.
/// P24D will supply the chosen push SDK implementation and permission flow.
/// P24B must never generate a fake identifier or use an access token instead.
abstract interface class DeviceTokenProvider {
  Future<ProviderDeviceToken?> currentToken();
}

/// Safe default until a verified push provider is integrated in P24D.
class UnconfiguredDeviceTokenProvider implements DeviceTokenProvider {
  const UnconfiguredDeviceTokenProvider();

  @override
  Future<ProviderDeviceToken?> currentToken() async => null;
}
