import 'dart:async';
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
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_ai/data/clinical_ai_remote_data_source.dart';

const _hospital = '123e4567-e89b-42d3-a456-426614174001';

class _Connectivity implements DeviceConnectivitySource {
  _Connectivity(this.status);
  final DeviceNetworkStatus status;
  @override
  Future<DeviceNetworkStatus> checkStatus() async => status;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

ClinicalAiRemoteDataSource _source({
  required MockClient mock,
  required SessionNavigationGuard guard,
  DeviceNetworkStatus status = DeviceNetworkStatus.connected,
  String role = 'DOCTOR',
  String? tenant = _hospital,
}) {
  final dashboard = DashboardRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: mock,
    ),
    loadSession: () async => AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'aaa.bbb.ccc',
        refreshToken: 'ddd.eee.fff',
        userId: 19,
        role: role,
        tenantId: tenant,
        clinicContextId: 8,
      ),
    ),
    sessionNavigationGuard: guard,
  );
  return ClinicalAiRemoteDataSource(
    dashboard: dashboard,
    guard: guard,
    connectivity: _Connectivity(status),
  );
}

void main() {
  test(
    'GAP-AI drug checks use existing API once and are only advisory',
    () async {
      var calls = 0;
      final mock = MockClient((request) async {
        calls++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/ai/drug-interactions');
        expect(request.headers['authorization'], 'Bearer aaa.bbb.ccc');
        expect(jsonDecode(request.body), {
          'drug_names': ['Drug A', 'Drug B'],
          'patient_id': 30,
        });
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'summary': 'Clinician review required',
              'interactions': [],
              'recommendations': ['Review dosing'],
            },
          }),
          200,
        );
      });
      addTearDown(mock.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final result = await _source(
        mock: mock,
        guard: guard,
      ).drugInteractions(drugNames: ['Drug A', 'Drug B'], patientId: 30);
      expect(result.lines, contains('Review dosing'));
      expect(calls, 1);
    },
  );

  test(
    'GAP-AI lab interpretation uses correct read-only advisory contract',
    () async {
      final mock = MockClient((request) async {
        expect(request.url.path, '/api/v1/ai/lab-results/interpret');
        expect(jsonDecode(request.body), {
          'result_data': {'glucose': '100 mg/dL'},
          'lab_order_id': 4,
        });
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'interpretation': 'Review result in clinical context',
              'abnormal_findings': [],
              'recommendations': [],
            },
          }),
          200,
        );
      });
      addTearDown(mock.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final result = await _source(
        mock: mock,
        guard: guard,
      ).interpretLab(resultData: {'glucose': '100 mg/dL'}, labOrderId: 4);
      expect(result.title, contains('Laboratory'));
    },
  );

  test(
    'GAP-AI offline and missing tenant grant never send a request',
    () async {
      var calls = 0;
      final mock = MockClient((_) async {
        calls++;
        throw StateError('No requests');
      });
      addTearDown(mock.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(
          mock: mock,
          guard: guard,
          status: DeviceNetworkStatus.disconnected,
        ).triage(patientId: 1, symptoms: 'Fever'),
        throwsA(isA<ClinicalAiUnavailable>()),
      );
      await expectLater(
        _source(
          mock: mock,
          guard: guard,
          tenant: null,
        ).triage(patientId: 1, symptoms: 'Fever'),
        throwsA(isA<ClinicalAiUnavailable>()),
      );
      expect(calls, 0);
    },
  );

  test('GAP-AI unauthorized role cannot dispatch', () async {
    var calls = 0;
    final mock = MockClient((_) async {
      calls++;
      throw StateError('No requests');
    });
    addTearDown(mock.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(
        mock: mock,
        guard: guard,
        role: 'PATIENT',
      ).triage(patientId: 1, symptoms: 'Fever'),
      throwsA(isA<ClinicalAiUnavailable>()),
    );
    expect(calls, 0);
  });

  test(
    'GAP-AI post-response clinic invalidation discards stale suggestion',
    () async {
      late SessionNavigationGuard guard;
      final mock = MockClient((_) async {
        guard.invalidate();
        return http.Response(
          '{"success":true,"data":{"risk_score":"low"}}',
          200,
        );
      });
      addTearDown(mock.close);
      guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(
          mock: mock,
          guard: guard,
        ).triage(patientId: 1, symptoms: 'Fever'),
        throwsA(isA<ClinicalAiUnavailable>()),
      );
    },
  );

  test(
    'GAP-AI malformed risk level cannot be displayed as clinical fact',
    () async {
      final mock = MockClient(
        (_) async => http.Response(
          '{"success":true,"data":{"risk_score":"certain cure"}}',
          200,
        ),
      );
      addTearDown(mock.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(
          mock: mock,
          guard: guard,
        ).triage(patientId: 1, symptoms: 'Fever'),
        throwsFormatException,
      );
    },
  );
}
