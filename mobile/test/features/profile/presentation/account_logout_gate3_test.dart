import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_scheduler.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_store.dart';
import 'package:clinic_system_pro/features/auth/data/authenticated_sign_out_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/clinic_context_switch_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/profile/data/profile_remote_data_source.dart';
import 'package:clinic_system_pro/features/profile/presentation/account_clinic_screen.dart';

class _Storage implements SecureKeyValueStorage {
  final values = <String, String>{};
  @override
  Future<String?> read({required String key}) async => values[key];
  @override
  Future<void> write({required String key, required String value}) async {
    values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    values.remove(key);
  }
}

class _Disconnected implements DeviceConnectivitySource {
  @override
  Future<DeviceNetworkStatus> checkStatus() async =>
      DeviceNetworkStatus.disconnected;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

void main() {
  testWidgets('S05 gate 3: account sign-out closes the clinical workspace', (
    tester,
  ) async {
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/home', '/account', '/patients'],
    )..markSessionAvailable();
    addTearDown(guard.dispose);
    final mock = MockClient((request) async {
      if (request.url.path == '/api/v1/profile/me') {
        return http.Response(
          jsonEncode(<String, Object?>{
            'success': true,
            'data': <String, Object?>{
              'user': <String, Object?>{
                'id': 42,
                'email': 'doctor@example.invalid',
                'is_active': true,
                'clinic_id': 3,
              },
              'staff': null,
              'clinic': <String, Object?>{'id': 3, 'name': 'Clinic Three'},
            },
          }),
          200,
        );
      }
      if (request.url.path == '/api/v1/auth/logout') {
        return http.Response('{"success":true}', 200);
      }
      fail('Unexpected endpoint: ${request.url.path}');
    });
    addTearDown(mock.close);
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: mock,
    );
    addTearDown(api.close);
    final store = AuthSessionStore(storage: _Storage());
    await store.save(
      const AuthTokenResponse(
        accessToken: 'aaa.bbb.ccc',
        refreshToken: 'ddd.eee.fff',
        userId: 42,
        role: 'DOCTOR',
        clinicContextId: 3,
      ),
    );
    final remote = AuthRemoteDataSource(apiClient: api);
    final coordinator = AuthSessionCoordinator(
      sessionStore: store,
      remoteDataSource: remote,
    );
    final scheduler = OperationReplayScheduler(
      connectivitySource: _Disconnected(),
      runReplay: () async => throw StateError('No replay expected.'),
    );
    addTearDown(scheduler.dispose);
    Future<AuthSessionState> loadSession() => coordinator.restoreSession();
    await tester.pumpWidget(
      MaterialApp(
        home: AccountClinicScreen(
          loadSession: loadSession,
          profileDataSource: ProfileRemoteDataSource(
            apiClient: api,
            loadSession: loadSession,
            sessionNavigationGuard: guard,
          ),
          authRemoteDataSource: remote,
          clinicContextSwitcher: ClinicContextSwitchCoordinator(
            sessionCoordinator: coordinator,
            sessionNavigationGuard: guard,
            replayScheduler: scheduler,
          ),
          signOutCoordinator: AuthenticatedSignOutCoordinator(
            sessionCoordinator: coordinator,
            sessionNavigationGuard: guard,
            replayScheduler: scheduler,
          ),
          sessionNavigationGuard: guard,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('p22-s05-account-sign-out')),
      findsOneWidget,
    );
    await tester.tap(find.byKey(const ValueKey('p22-s05-account-sign-out')));
    await tester.pumpAndSettle();
    expect(guard.status, SessionNavigationStatus.signedOut);
    expect(await store.read(), isNull);
    expect(find.byKey(const ValueKey('p22-s05-profile-email')), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
