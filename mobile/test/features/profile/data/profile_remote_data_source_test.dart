import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/profile/data/profile_remote_data_source.dart';

final _config = BackendConfig(
  baseUrl: 'http://127.0.0.1:5000',
  allowInsecureHttp: true,
);

AuthSessionState _session({
  int userId = 42,
  int? clinic = 3,
  String role = 'DOCTOR',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'test.access.token',
    refreshToken: 'test.refresh.token',
    userId: userId,
    role: role,
    clinicContextId: clinic,
  ),
);

Map<String, dynamic> _envelope({int userId = 42, int clinic = 3}) => {
  'success': true,
  'data': {
    'user': {
      'id': userId,
      'email': 'doctor@example.invalid',
      'is_active': true,
      'clinic_id': clinic,
    },
    'staff': {'first_name': 'Alex', 'last_name': 'Smith', 'clinic_id': clinic},
    'clinic': {'id': clinic, 'name': 'Clinic $clinic'},
  },
};

void main() {
  test(
    'S05 gate 1: authenticated profile validates role, identity and clinic',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/profile/me');
        expect(request.headers['authorization'], 'Bearer test.access.token');
        return http.Response(jsonEncode(_envelope()), 200);
      });
      addTearDown(client.close);
      final api = ApiClient(config: _config, client: client);
      addTearDown(api.close);
      final source = ProfileRemoteDataSource(
        apiClient: api,
        loadSession: () async => _session(),
        sessionNavigationGuard: guard,
      );
      final profile = await source.fetchMyProfile();
      expect(profile.userId, 42);
      expect(profile.staffName, 'Alex Smith');
      expect(profile.assignedClinicId, 3);
      expect(profile.assignedClinicName, 'Clinic 3');
      expect(requests, 1);
    },
  );

  test('S05 gate 1: forged cross-user response is rejected', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_envelope(userId: 99)), 200),
    );
    addTearDown(client.close);
    final api = ApiClient(config: _config, client: client);
    addTearDown(api.close);
    final source = ProfileRemoteDataSource(
      apiClient: api,
      loadSession: () async => _session(),
    );
    await expectLater(source.fetchMyProfile(), throwsFormatException);
  });

  test('S05 gate 1: forged cross-clinic response is rejected', () async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_envelope(clinic: 17)), 200),
    );
    addTearDown(client.close);
    final api = ApiClient(config: _config, client: client);
    addTearDown(api.close);
    final source = ProfileRemoteDataSource(
      apiClient: api,
      loadSession: () async => _session(),
    );
    await expectLater(source.fetchMyProfile(), throwsFormatException);
  });

  test(
    'S05 gate 1: super-admin is not sent to forbidden profile endpoint',
    () async {
      var requests = 0;
      final client = MockClient((_) async {
        requests++;
        throw StateError('Super-admin must not request /profile/me.');
      });
      addTearDown(client.close);
      final api = ApiClient(config: _config, client: client);
      addTearDown(api.close);
      final source = ProfileRemoteDataSource(
        apiClient: api,
        loadSession: () async => _session(role: 'SUPER_ADMIN'),
      );
      await expectLater(source.fetchMyProfile(), throwsStateError);
      expect(requests, 0);
    },
  );

  test('S05 gate 1: in-flight completion is suppressed after logout', () async {
    final pending = Completer<http.Response>();
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final api = ApiClient(config: _config, client: client);
    addTearDown(api.close);
    final source = ProfileRemoteDataSource(
      apiClient: api,
      loadSession: () async => _session(),
      sessionNavigationGuard: guard,
    );
    final result = source.fetchMyProfile();
    await Future<void>.delayed(Duration.zero);
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_envelope()), 200));
    await expectLater(result, throwsStateError);
  });
}
