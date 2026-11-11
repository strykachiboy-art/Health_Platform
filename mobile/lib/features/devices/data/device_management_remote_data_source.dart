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
