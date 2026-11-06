import 'dart:async';
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
import 'package:clinic_system_pro/features/auth/data/clinic_context_switch_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/profile/data/profile_remote_data_source.dart';
import 'package:clinic_system_pro/features/profile/presentation/account_clinic_screen.dart';

class _Storage implements SecureKeyValueStorage {
  final map = <String, String>{};
  @override
  Future<void> write({required String key, required String value}) async {
    map[key] = value;
  }

  @override
  Future<String?> read({required String key}) async => map[key];
  @override
  Future<void> delete({required String key}) async {
    map.remove(key);
  }
}

class _Connectivity implements DeviceConnectivitySource {
  @override
  Future<DeviceNetworkStatus> checkStatus() async =>
      DeviceNetworkStatus.disconnected;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

Future<void> _mount(
  WidgetTester tester, {
  required String role,
  required Future<http.Response> Function(http.Request) handler,
}) async {
  final guard = SessionNavigationGuard()..markSessionAvailable();
  addTearDown(guard.dispose);
  final client = MockClient(handler);
  addTearDown(client.close);
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  addTearDown(api.close);
  final auth = AuthRemoteDataSource(apiClient: api);
  final session = AuthTokenResponse(
    accessToken: 'aaa.bbb.ccc',
    refreshToken: 'ddd.eee.fff',
    userId: 42,
    role: role,
    clinicContextId: role == 'SUPER_ADMIN' ? null : 3,
  );
  Future<AuthSessionState> loadSession() async =>
      AuthSessionState.ready(session);
  final store = AuthSessionStore(storage: _Storage());
  final coordinator = AuthSessionCoordinator(
    sessionStore: store,
    remoteDataSource: auth,
  );
  final scheduler = OperationReplayScheduler(
    connectivitySource: _Connectivity(),
    runReplay: () async => throw StateError('No replay expected.'),
  );
  addTearDown(scheduler.dispose);
  final switcher = ClinicContextSwitchCoordinator(
    sessionCoordinator: coordinator,
    sessionNavigationGuard: guard,
    replayScheduler: scheduler,
  );

  await tester.pumpWidget(
    MaterialApp(
      home: AccountClinicScreen(
        loadSession: loadSession,
        profileDataSource: ProfileRemoteDataSource(
          apiClient: api,
          loadSession: loadSession,
          sessionNavigationGuard: guard,
        ),
        authRemoteDataSource: auth,
        clinicContextSwitcher: switcher,
        sessionNavigationGuard: guard,
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
    'S05 gate 1 profile is verified and clinic controls hidden for staff',
    (tester) async {
      await _mount(
        tester,
        role: 'DOCTOR',
        handler: (request) async {
          expect(request.url.path, '/api/v1/profile/me');
          return http.Response(
            jsonEncode({
              'success': true,
              'data': {
                'user': {
                  'id': 42,
                  'email': 'doctor@example.invalid',
                  'is_active': true,
                  'clinic_id': 3,
                },
                'staff': {
                  'first_name': 'Alex',
                  'last_name': 'Smith',
                  'clinic_id': 3,
                },
                'clinic': {'id': 3, 'name': 'Three Clinic'},
              },
            }),
            200,
          );
        },
      );
      expect(find.text('Email: doctor@example.invalid'), findsOneWidget);
      expect(find.text('Assigned clinic: Three Clinic'), findsOneWidget);
      expect(find.byKey(const ValueKey('p22-s05-select-clinic')), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );

  testWidgets('S05 gate 2 super-admin sees backend context, not /profile/me', (
    tester,
  ) async {
    var requests = 0;
    await _mount(
      tester,
      role: 'SUPER_ADMIN',
      handler: (request) async {
        requests++;
        expect(request.url.path, '/api/v1/auth/clinic-context');
        return http.Response(
          '{"success":true,"data":{"clinic_id":null,"clinic_name":null,"source":"system"}}',
          200,
        );
      },
    );
    expect(requests, 1);
    expect(find.text('Selected clinic: None'), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-s05-select-clinic')), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-s05-clear-clinic')), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-s05-profile-email')), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
