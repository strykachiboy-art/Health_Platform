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
import 'package:clinic_system_pro/features/devices/data/device_management_models.dart';
import 'package:clinic_system_pro/features/devices/data/device_management_remote_data_source.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_remote_data_source.dart';

class P24EConnectivity implements DeviceConnectivitySource {
  P24EConnectivity(this.value);
  DeviceNetworkStatus value;
  @override
  Future<DeviceNetworkStatus> checkStatus() async => value;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

AuthSessionState p24eReady({String role = 'patient', int userId = 41}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'p24e-access',
        refreshToken: 'p24e-refresh',
        userId: userId,
        role: role,
        clinicContextId: null,
      ),
    );

Map<String, dynamic> p24eDevice({int userId = 41, bool active = true}) => {
  'id': 7,
  'user_id': userId,
  'device_token': 'sensitive-fid-never-display-7654321',
  'device_name': 'Phone',
  'platform': 'android',
  'is_active': active,
  'created_at': '2026-11-09T10:10:10Z',
  'updated_at': '2026-11-10T10:10:10Z',
  'last_seen_at': '2026-11-10T11:10:10Z',
};

Map<String, dynamic> p24eList({int userId = 41, bool active = true}) => {
  'success': true,
  'data': {
    'items': [p24eDevice(userId: userId, active: active)],
    'page': 1,
    'per_page': 20,
    'total': 1,
    'pages': 1,
    'has_next': false,
    'has_prev': false,
  },
};

DeviceManagementRemoteDataSource p24eSource(
  http.Client client,
  SessionNavigationGuard guard,
  P24EConnectivity network, {
  Future<AuthSessionState> Function()? session,
}) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  return DeviceManagementRemoteDataSource(
    registrationScope: DeviceRegistrationRemoteDataSource(
      apiClient: api,
      loadSession: session ?? () async => p24eReady(),
      sessionNavigationGuard: guard,
      connectivity: network,
    ),
  );
}

void main() {
  test('GET keeps owner devices only, drops push identifier', () async {
    var reads = 0;
    final client = MockClient((request) async {
      reads++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/users/devices/');
      expect(request.url.queryParameters['page'], '1');
      expect(request.headers['Authorization'], 'Bearer p24e-access');
      return http.Response(jsonEncode(p24eList()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = p24eSource(
      client,
      guard,
      P24EConnectivity(DeviceNetworkStatus.connected),
    );
    final page = await source.list();
    expect(page.items.single.name, 'Phone');
    expect(page.items.single.id, 7);
    expect(page.confirmedCurrentDeviceId, isNull);
    expect(page.items.single.toString(), isNot(contains('sensitive-fid')));
    expect(page.toString(), isNot(contains('sensitive-fid')));
    expect(reads, 1);
  });

  test('foreign owner and malformed page are refused', () {
    expect(
      () => ManagedDevicePage.fromEnvelope(
        p24eList(userId: 999),
        expectedUserId: 41,
        expectedPage: 1,
      ),
      throwsFormatException,
    );
    expect(
      () => ManagedDevicePage.fromEnvelope(
        p24eList(),
        expectedUserId: 41,
        expectedPage: 2,
      ),
      throwsFormatException,
    );
  });

  test('super admin, signed out and offline do not contact backend', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final network = P24EConnectivity(DeviceNetworkStatus.connected);
    var session = p24eReady(role: 'super_admin');
    final source = p24eSource(
      client,
      guard,
      network,
      session: () async => session,
    );
    await expectLater(
      source.list(),
      throwsA(isA<DeviceRegistrationSessionUnavailable>()),
    );
    session = p24eReady();
    guard.markSignedOut();
    await expectLater(
      source.list(),
      throwsA(isA<DeviceRegistrationSessionUnavailable>()),
    );
    guard.markSessionAvailable();
    network.value = DeviceNetworkStatus.disconnected;
    await expectLater(source.list(), throwsA(isA<DeviceRegistrationOffline>()));
    expect(calls, 0);
  });

  test('deactivate and delete use exact owner endpoints once', () async {
    var post = 0;
    var del = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        post++;
        expect(request.url.path, '/api/v1/users/devices/7/deactivate');
        expect(jsonDecode(request.body), <String, dynamic>{});
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {'id': 7, 'user_id': 41, 'is_active': false},
          }),
          200,
        );
      }
      del++;
      expect(request.method, 'DELETE');
      expect(request.url.path, '/api/v1/users/devices/7');
      return http.Response(
        jsonEncode({'success': true, 'message': 'Device deleted successfully'}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = p24eSource(
      client,
      guard,
      P24EConnectivity(DeviceNetworkStatus.connected),
    );
    final row = ManagedDevice.fromJson(p24eDevice(), expectedUserId: 41);
    await source.deactivate(row);
    await source.delete(row);
    expect(post, 1);
    expect(del, 1);
  });

  test(
    'lost deactivate response is uncertain with no automatic retry',
    () async {
      var writes = 0;
      final client = MockClient((request) async {
        writes++;
        throw http.ClientException('lost response');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = p24eSource(
        client,
        guard,
        P24EConnectivity(DeviceNetworkStatus.connected),
      );
      final row = ManagedDevice.fromJson(p24eDevice(), expectedUserId: 41);
      await expectLater(
        source.deactivate(row),
        throwsA(isA<DeviceManagementOutcomeUnknown>()),
      );
      expect(writes, 1);
    },
  );

  test(
    'a session change during GET never returns the old device page',
    () async {
      var session = p24eReady();
      final client = MockClient((request) async {
        session = p24eReady(userId: 99);
        return http.Response(jsonEncode(p24eList()), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = p24eSource(
        client,
        guard,
        P24EConnectivity(DeviceNetworkStatus.connected),
        session: () async => session,
      );
      await expectLater(
        source.list(),
        throwsA(isA<DeviceRegistrationSessionUnavailable>()),
      );
    },
  );
}
