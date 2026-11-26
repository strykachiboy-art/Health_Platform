import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_coordinator.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_remote_data_source.dart';

import 'device_management_models.dart';

class DeviceManagementOutcomeUnknown implements Exception {
  const DeviceManagementOutcomeUnknown();
}

/// P24E: authenticated user-owned list and explicit online-only mutations.
/// No token persistence, automatic retries, or speculative session revocation.
class DeviceManagementRemoteDataSource {
  const DeviceManagementRemoteDataSource({
    required this.registrationScope,
    this.registrationCoordinator,
  });

  final DeviceRegistrationRemoteDataSource registrationScope;
  final DeviceRegistrationCoordinator? registrationCoordinator;

  Future<void> _online() async {
    try {
      if (await registrationScope.connectivity.checkStatus() !=
          DeviceNetworkStatus.connected) {
        throw const DeviceRegistrationOffline();
      }
    } on Object {
      throw const DeviceRegistrationOffline();
    }
  }

  Future<ManagedDevicePage> list({int page = 1}) async {
    if (page < 1) throw ArgumentError.value(page, 'page');
    final before = await registrationScope.requireSession();
    await _online();
    await registrationScope.assertSameSession(before);
    final json = await registrationScope.apiClient.getJson(
      'api/v1/users/devices/?page=$page&per_page=20',
      bearerToken: before.tokenSession.accessToken,
    );
    await registrationScope.assertSameSession(before);
    return ManagedDevicePage.fromEnvelope(
      json,
      expectedUserId: before.tokenSession.userId,
      expectedPage: page,
      confirmedCurrentDeviceId: registrationCoordinator
          ?.confirmedDeviceIdForSession(before),
    );
  }

  Future<ManagedDevice> _detailFor(ManagedDevice device, DeviceRegistrationSession before) async {
    if (device.id <= 0 || device.userId != before.tokenSession.userId) {
      throw const DeviceRegistrationSessionUnavailable();
    }
    await _online();
    await registrationScope.assertSameSession(before);
    final raw = await registrationScope.apiClient.getJson(
      'api/v1/users/devices/${device.id}', bearerToken: before.tokenSession.accessToken,
    );
    await registrationScope.assertSameSession(before);
    return _deviceReceipt(raw, before, device.id);
  }

  static ManagedDevice _deviceReceipt(Map<String, dynamic> raw, DeviceRegistrationSession before, int id) {
    final data = raw['data'];
    if (raw['success'] != true || data is! Map<String, dynamic>) throw const FormatException('Invalid device receipt.');
    final result = ManagedDevice.fromJson(data, expectedUserId: before.tokenSession.userId);
    if (result.id != id) throw const FormatException('Wrong device receipt.');
    return result;
  }

  Future<ManagedDevice> detail(ManagedDevice device) async {
    final before = await registrationScope.requireSession();
    return _detailFor(device, before);
  }

  Future<ManagedDevice> rename(ManagedDevice device, String name, {required int expectedRevision}) async {
    final cleaned = name.trim();
    if (cleaned.isEmpty || cleaned.length > 100) throw const FormatException('Invalid device name.');
    return _updateDevice(device, expectedRevision, name: cleaned);
  }

  Future<ManagedDevice> activate(ManagedDevice device, {required int expectedRevision}) =>
      _updateDevice(device, expectedRevision);

  Future<ManagedDevice> _updateDevice(ManagedDevice device, int expectedRevision, {String? name}) async {
    final before = await registrationScope.requireSession(expectedRevision: expectedRevision);
    final fresh = await _detailFor(device, before);
    if (fresh.active != device.active || fresh.name != device.name ||
        fresh.platform != device.platform || (name == null && fresh.active)) {
      throw const FormatException('Device record changed; refresh before confirming.');
    }
    await registrationScope.assertSameSession(before);
    try {
      final raw = name == null
          ? await registrationScope.apiClient.postJson('api/v1/users/devices/${device.id}/activate', body: const <String, dynamic>{}, bearerToken: before.tokenSession.accessToken)
          : await registrationScope.apiClient.patchJson('api/v1/users/devices/${device.id}', body: {'device_name': name}, bearerToken: before.tokenSession.accessToken);
      await registrationScope.assertSameSession(before);
      final receipt = _deviceReceipt(raw, before, device.id);
      if (receipt.platform != fresh.platform ||
          (name == null ? (!receipt.active || receipt.name != fresh.name) : (receipt.name != name || receipt.active != fresh.active))) {
        throw const FormatException('Device change not confirmed.');
      }
      return receipt;
    } on Object {
      // Any dispatched request may have committed. The screen must refresh.
      throw const DeviceManagementOutcomeUnknown();
    }
  }

  Future<void> deactivate(ManagedDevice device) =>
      _mutate(device, delete: false);

  Future<void> delete(ManagedDevice device) => _mutate(device, delete: true);

  Future<void> _mutate(ManagedDevice device, {required bool delete}) async {
    final before = await registrationScope.requireSession();
    if (device.id <= 0 || device.userId != before.tokenSession.userId) {
      throw const DeviceRegistrationSessionUnavailable();
    }
    await _online();
    await registrationScope.assertSameSession(before);
    // Once dispatched, transport or malformed responses are UNCERTAIN.
    // Do not retry automatically. The user must refresh the server list.
    final Map<String, dynamic> response;
    try {
      response = delete
          ? await registrationScope.apiClient.deleteJson(
              'api/v1/users/devices/${device.id}',
              bearerToken: before.tokenSession.accessToken,
            )
          : await registrationScope.apiClient.postJson(
              'api/v1/users/devices/${device.id}/deactivate',
              bearerToken: before.tokenSession.accessToken,
              body: const <String, dynamic>{},
            );
    } on ApiException catch (error) {
      if (const <int>{
        400,
        401,
        403,
        404,
        409,
        422,
      }.contains(error.statusCode)) {
        rethrow;
      }
      throw const DeviceManagementOutcomeUnknown();
    } on Object {
      throw const DeviceManagementOutcomeUnknown();
    }
    try {
      if (response['success'] != true) {
        throw const FormatException('Device action not acknowledged.');
      }
      if (!delete) {
        final data = response['data'];
        if (data is! Map<String, dynamic> ||
            data['id'] != device.id ||
            data['user_id'] != before.tokenSession.userId ||
            data['is_active'] != false) {
          throw const FormatException('Device deactivation not confirmed.');
        }
      }
      await registrationScope.assertSameSession(before);
    } on Object {
      throw const DeviceManagementOutcomeUnknown();
    }
  }
}
