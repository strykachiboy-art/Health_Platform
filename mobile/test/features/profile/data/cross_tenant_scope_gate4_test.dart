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
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_session_scope.dart';
import 'package:clinic_system_pro/features/profile/data/profile_remote_data_source.dart';

AuthTokenResponse _session(int clinic) => AuthTokenResponse(
  accessToken: 'token-$clinic',
  refreshToken: 'refresh-$clinic',
  userId: 42,
  role: 'DOCTOR',
  clinicContextId: clinic,
);

ApiClient _api(MockClient client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

Map<String, dynamic> _profile(int clinic) => <String, dynamic>{
  'success': true,
  'data': <String, dynamic>{
    'user': <String, dynamic>{
      'id': 42,
      'email': 'doctor@example.invalid',
      'is_active': true,
      'clinic_id': clinic,
    },
    'staff': null,
    'clinic': <String, dynamic>{'id': clinic, 'name': 'Clinic $clinic'},
  },
};

void main() {
  test(
    'S05 gate 4: rejected foreign clinic profile cannot be displayed',
    () async {
      final mock = MockClient(
        (_) async => http.Response(jsonEncode(_profile(8)), 200),
      );
      addTearDown(mock.close);
      final api = _api(mock);
      addTearDown(api.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      Future<AuthSessionState> load() async =>
          AuthSessionState.ready(_session(3));
      final source = ProfileRemoteDataSource(
        apiClient: api,
        loadSession: load,
        sessionNavigationGuard: guard,
      );
      await expectLater(
        source.fetchMyProfile(),
        throwsA(isA<FormatException>()),
      );
    },
  );

  test(
    'S05 gate 4: old-clinic profile reply rejected after revision change',
    () async {
      final pending = Completer<http.Response>();
      final started = Completer<void>();
      final mock = MockClient((request) {
        expect(request.url.path, '/api/v1/profile/me');
        started.complete();
        return pending.future;
      });
      addTearDown(mock.close);
      final api = _api(mock);
      addTearDown(api.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var activeClinic = 3;
      Future<AuthSessionState> load() async =>
          AuthSessionState.ready(_session(activeClinic));
      final source = ProfileRemoteDataSource(
        apiClient: api,
        loadSession: load,
        sessionNavigationGuard: guard,
      );
      final read = source.fetchMyProfile();
      await started.future;
      guard.invalidate();
      activeClinic = 4;
      guard.markSessionAvailable();
      pending.complete(http.Response(jsonEncode(_profile(3)), 200));
      await expectLater(read, throwsA(isA<StateError>()));
    },
  );

  test(
    'S05 gate 4: late old-clinic dashboard cannot cross into new session',
    () async {
      final pending = Completer<http.Response>();
      final started = Completer<void>();
      final mock = MockClient((request) {
        expect(request.url.path, '/api/v1/dashboard');
        started.complete();
        return pending.future;
      });
      addTearDown(mock.close);
      final api = _api(mock);
      addTearDown(api.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var activeClinic = 3;
      Future<AuthSessionState> load() async =>
          AuthSessionState.ready(_session(activeClinic));
      final source = DashboardRemoteDataSource(
        apiClient: api,
        loadSession: load,
        sessionNavigationGuard: guard,
      );
      final read = source.fetchDashboard();
      await started.future;
      guard.invalidate();
      activeClinic = 4;
      guard.markSessionAvailable();
      pending.complete(http.Response('{"success":true,"data":{}}', 200));
      await expectLater(
        read,
        throwsA(isA<DashboardSessionMismatchException>()),
      );
    },
  );

  test('S05 gate 4: emergency grant cannot survive a clinic transition', () {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final emergency = EmergencyAccessContext(
      sessionNavigationGuard: guard,
      nowUtc: () => DateTime.utc(2026, 11, 6, 12),
    );
    addTearDown(emergency.dispose);
    emergency.activate(
      EmergencyAccessGrant.fromJson(<String, dynamic>{
        'id': 19,
        'clinic_id': 3,
        'patient_id': 7,
        'requester_user_id': 42,
        'requester_role': 'DOCTOR',
        'reason': 'Emergency treatment',
        'purpose': 'Urgent chart review',
        'scope': <String>['patient:read'],
        'status': 'active',
        'requested_at': '2026-11-06T11:00:00+00:00',
        'granted_at': '2026-11-06T11:05:00+00:00',
        'expires_at': '2026-11-06T13:00:00+00:00',
        'revoked_at': null,
        'reviewed_at': '2026-11-06T11:05:00+00:00',
        'reviewed_by_user_id': 99,
        'review_notes': 'Reviewed',
      }),
    );
    expect(emergency.activeEmergencyAccessId, 19);
    guard.invalidate();
    expect(emergency.activeEmergencyAccessId, isNull);
    guard.markSessionAvailable();
    expect(emergency.activeEmergencyAccessId, isNull);
  });

  test(
    'S05 gate 4: clinical stamp changes with navigation and clinic',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var activeClinic = 3;
      Future<AuthSessionState> load() async =>
          AuthSessionState.ready(_session(activeClinic));
      final scope = ClinicalSafetySessionScope(
        restoreSession: load,
        navigationGuard: guard,
      );
      final first = await scope.snapshot();
      guard.invalidate();
      expect(guard.canAccessProtectedRoutes, isFalse);
      activeClinic = 4;
      guard.markSessionAvailable();
      final second = await scope.snapshot();
      expect(first.clinicContextId, 3);
      expect(second.clinicContextId, 4);
      expect(first.navigationRevision, isNot(second.navigationRevision));
      expect(first.accessToken, isNot(second.accessToken));
    },
  );
}
