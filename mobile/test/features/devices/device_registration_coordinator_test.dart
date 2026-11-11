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
import 'package:clinic_system_pro/features/devices/data/device_registration_coordinator.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_models.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_remote_data_source.dart';
import 'package:clinic_system_pro/features/devices/data/device_token_provider.dart';

class TestTokenProvider implements DeviceTokenProvider {
  TestTokenProvider(this.token);
  ProviderDeviceToken? token;
  @override
  Future<ProviderDeviceToken?> currentToken() async => token;
}

class OnlineConnection implements DeviceConnectivitySource {
  @override
  Future<DeviceNetworkStatus> checkStatus() async =>
      DeviceNetworkStatus.connected;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

AuthSessionState currentSession([int user = 41]) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'session-credential',
    refreshToken: 'session-refresh',
    role: 'patient',
    userId: user,
    clinicContextId: null,
  ),
);

ProviderDeviceToken token(String value) => ProviderDeviceToken(
  deviceToken: value,
  platform: RegisteredDevicePlatform.android,
  deviceName: 'Test phone',
);

ApiClient api(http.Client client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

DeviceRegistrationRemoteDataSource sourceFor(
  http.Client client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loadSession,
}) => DeviceRegistrationRemoteDataSource(
  apiClient: api(client),
  loadSession: loadSession ?? () async => currentSession(),
  sessionNavigationGuard: guard,
  connectivity: OnlineConnection(),
);

void main() {
  test('registers once, deduplicates same provider token, rotates and deactivates old', () async {
    var created = 0, deactivated = 0;
    final provider = TestTokenProvider(token('valid-provider-token-aaaaaa'));
    final client = MockClient((request) async {
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      if (request.url.path.endsWith('/deactivate')) {
        deactivated++;
        expect(request.url.path, '/api/v1/users/devices/1/deactivate');
        expect(body, <String, dynamic>{});
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {'id': 1, 'user_id': 41, 'is_active': false},
          }),
          200,
        );
      }
      created++;
      final newId = created;
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'id': newId,
            'user_id': 41,
            'device_token': body['device_token'],
            'platform': 'android',
            'device_name': 'Test phone',
            'is_active': true,
          },
        }),
        201,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final coordinator = DeviceRegistrationCoordinator(
      source: sourceFor(client, guard),
      provider: provider,
    );
    final first = await coordinator.registerLatestToken();
    expect(first.receipt.id, 1);
    final same = await coordinator.registerLatestToken();
    expect(same.receipt.id, 1);
    expect(created, 1);
    provider.token = token('valid-provider-token-bbbbbb');
    final rotated = await coordinator.registerLatestToken();
    expect(rotated.receipt.id, 2);
    expect(rotated.previousDeviceCleanupConfirmed, true);
    expect(created, 2);
    expect(deactivated, 1);
  });

  test('not-configured provider cannot make a registration request', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final coordinator = DeviceRegistrationCoordinator(
      source: sourceFor(client, guard),
      provider: const UnconfiguredDeviceTokenProvider(),
    );
    await expectLater(
      coordinator.registerLatestToken(),
      throwsA(isA<DeviceTokenProviderUnavailable>()),
    );
    expect(calls, 0);
  });

  test(
    'ambiguous POST cannot be sent twice for the same session/token',
    () async {
      var writes = 0;
      final client = MockClient((request) async {
        writes++;
        throw http.ClientException('lost registration response');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final coordinator = DeviceRegistrationCoordinator(
        source: sourceFor(client, guard),
        provider: TestTokenProvider(token('valid-provider-token-uncertain')),
      );
      await expectLater(
        coordinator.registerLatestToken(),
        throwsA(isA<DeviceRegistrationOutcomeUnknown>()),
      );
      await expectLater(
        coordinator.registerLatestToken(),
        throwsA(isA<DeviceRegistrationOutcomeUnknown>()),
      );
      expect(writes, 1);
    },
  );

  test('rotated registration remains visible only with unconfirmed cleanup warning', () async {
    var nextId = 0;
    final provider = TestTokenProvider(token('valid-provider-token-first'));
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/deactivate')) {
        throw http.ClientException('cleanup outcome unknown');
      }
      nextId++;
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'id': nextId,
            'user_id': 41,
            'device_token': body['device_token'],
            'platform': 'android',
            'device_name': 'Test phone',
            'is_active': true,
          },
        }),
        201,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final coordinator = DeviceRegistrationCoordinator(
      source: sourceFor(client, guard),
      provider: provider,
    );
    expect((await coordinator.registerLatestToken()).receipt.id, 1);
    provider.token = token('valid-provider-token-next');
    final result = await coordinator.registerLatestToken();
    expect(result.receipt.id, 2);
    expect(result.previousDeviceCleanupConfirmed, false);
  });
}
