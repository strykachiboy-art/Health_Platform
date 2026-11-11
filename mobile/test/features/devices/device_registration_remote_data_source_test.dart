import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_models.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_remote_data_source.dart';

AuthSessionState deviceReady({
  String role = 'patient',
  int userId = 41,
  int? clinic,
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'test-access',
    refreshToken: 'test-refresh',
    userId: userId,
    role: role,
    clinicContextId: clinic,
  ),
);

class FakeConnection implements DeviceConnectivitySource {
  FakeConnection(this.state);
  DeviceNetworkStatus state;
  @override
  Future<DeviceNetworkStatus> checkStatus() async => state;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

ApiClient deviceApi(http.Client client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

ProviderDeviceToken deviceToken([
  String value = 'verified-provider-token-123456',
]) => ProviderDeviceToken(
  deviceToken: value,
  platform: RegisteredDevicePlatform.android,
  deviceName: 'Clinic Phone',
);

Map<String, dynamic> deviceResponse(
  ProviderDeviceToken token, {
  int user = 41,
  int id = 7,
}) => {
  'success': true,
  'data': {
    'id': id,
    'user_id': user,
    'device_token': token.deviceToken,
    'platform': token.platform.wireValue,
    'device_name': token.deviceName,
    'is_active': true,
  },
};

void main() {
  test(
    'register POST uses exact backend path/body, JWT and returns no token',
    () async {
      var writes = 0;
      final token = deviceToken();
      final client = MockClient((request) async {
        writes++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/users/devices/');
        expect(request.headers['Authorization'], 'Bearer test-access');
        expect(jsonDecode(request.body), {
          'device_token': token.deviceToken,
          'platform': 'android',
          'device_name': 'Clinic Phone',
        });
        return http.Response(jsonEncode(deviceResponse(token)), 201);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = DeviceRegistrationRemoteDataSource(
        apiClient: deviceApi(client),
        loadSession: () async => deviceReady(),
        sessionNavigationGuard: guard,
        connectivity: FakeConnection(DeviceNetworkStatus.connected),
      );
      final before = await source.requireSession();
      final receipt = await source.register(token, expectedSession: before);
      expect(receipt.userId, 41);
      expect(receipt.id, 7);
      expect(writes, 1);
      expect(receipt.toString(), isNot(contains(token.deviceToken)));
    },
  );

  test(
    'signed out and super-admin are rejected before network dispatch',
    () async {
      var writes = 0;
      final client = MockClient((request) async {
        writes++;
        return http.Response('{}', 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var state = deviceReady(role: 'super_admin', clinic: 3);
      final source = DeviceRegistrationRemoteDataSource(
        apiClient: deviceApi(client),
        loadSession: () async => state,
        sessionNavigationGuard: guard,
        connectivity: FakeConnection(DeviceNetworkStatus.connected),
      );
      await expectLater(
        source.requireSession(),
        throwsA(isA<DeviceRegistrationSessionUnavailable>()),
      );
      state = deviceReady();
      guard.markSignedOut();
      await expectLater(
        source.requireSession(),
        throwsA(isA<DeviceRegistrationSessionUnavailable>()),
      );
      expect(writes, 0);
    },
  );

  test('disconnected and unknown connectivity block POST', () async {
    var writes = 0;
    final client = MockClient((request) async {
      writes++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final network = FakeConnection(DeviceNetworkStatus.disconnected);
    final source = DeviceRegistrationRemoteDataSource(
      apiClient: deviceApi(client),
      loadSession: () async => deviceReady(),
      sessionNavigationGuard: guard,
      connectivity: network,
    );
    final before = await source.requireSession();
    await expectLater(
      source.register(deviceToken(), expectedSession: before),
      throwsA(isA<DeviceRegistrationOffline>()),
    );
    network.state = DeviceNetworkStatus.unknown;
    await expectLater(
      source.register(deviceToken(), expectedSession: before),
      throwsA(isA<DeviceRegistrationOffline>()),
    );
    expect(writes, 0);
  });

  test(
    'mismatched response owner and post-dispatch failures are uncertain',
    () async {
      final token = deviceToken();
      var writes = 0;
      final client = MockClient((request) async {
        writes++;
        if (writes == 1) {
          return http.Response(
            jsonEncode(deviceResponse(token, user: 999)),
            201,
          );
        }
        throw http.ClientException('simulated lost response');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = DeviceRegistrationRemoteDataSource(
        apiClient: deviceApi(client),
        loadSession: () async => deviceReady(),
        sessionNavigationGuard: guard,
        connectivity: FakeConnection(DeviceNetworkStatus.connected),
      );
      final before = await source.requireSession();
      await expectLater(
        source.register(token, expectedSession: before),
        throwsA(isA<DeviceRegistrationOutcomeUnknown>()),
      );
      await expectLater(
        source.register(token, expectedSession: before),
        throwsA(isA<DeviceRegistrationOutcomeUnknown>()),
      );
      expect(writes, 2);
    },
  );

  test(
    'in-flight account change hides a remote registration success',
    () async {
      final token = deviceToken();
      var state = deviceReady();
      final client = MockClient((request) async {
        state = deviceReady(userId: 99);
        return http.Response(jsonEncode(deviceResponse(token)), 201);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = DeviceRegistrationRemoteDataSource(
        apiClient: deviceApi(client),
        loadSession: () async => state,
        sessionNavigationGuard: guard,
        connectivity: FakeConnection(DeviceNetworkStatus.connected),
      );
      final before = await source.requireSession();
      await expectLater(
        source.register(token, expectedSession: before),
        throwsA(isA<DeviceRegistrationOutcomeUnknown>()),
      );
    },
  );

  test(
    'verified new token permits old-token deactivation only for owner',
    () async {
      final client = MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/users/devices/7/deactivate');
        expect(jsonDecode(request.body), <String, dynamic>{});
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {'id': 7, 'user_id': 41, 'is_active': false},
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = DeviceRegistrationRemoteDataSource(
        apiClient: deviceApi(client),
        loadSession: () async => deviceReady(),
        sessionNavigationGuard: guard,
        connectivity: FakeConnection(DeviceNetworkStatus.connected),
      );
      final before = await source.requireSession();
      await source.deactivatePrevious(7, expectedSession: before);
    },
  );
}
