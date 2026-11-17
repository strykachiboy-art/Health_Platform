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
import 'package:clinic_system_pro/features/ai/data/clinical_ai_remote_data_source.dart';

class _Network implements DeviceConnectivitySource {
  _Network(this.status);
  DeviceNetworkStatus status;
  @override
  Future<DeviceNetworkStatus> checkStatus() async => status;
  @override
  Stream<DeviceNetworkStatus> get statusChanges => const Stream.empty();
}

void main() {
  late SessionNavigationGuard guard;
  late _Network network;
  late MockClient client;
  late ApiClient api;
  late ClinicalAiRemoteDataSource ai;
  var role = 'doctor';
  var clinic = 7;
  var token = 'token-A';
  var requests = 0;
  var response = <String, dynamic>{
    'success': true,
    'data': {'risk_score': 'medium', 'summary': 'Review.'},
  };
  Completer<void>? hold;

  setUp(() {
    guard = SessionNavigationGuard()..markSessionAvailable();
    network = _Network(DeviceNetworkStatus.connected);
    requests = 0;
    role = 'doctor';
    clinic = 7;
    token = 'token-A';
    hold = null;
    response = <String, dynamic>{
      'success': true,
      'data': {'risk_score': 'medium', 'summary': 'Review.'},
    };
    client = MockClient((request) async {
      requests++;
      expect(request.method, 'POST');
      expect(request.headers['authorization'], 'Bearer token-A');
      if (hold != null) await hold!.future;
      return http.Response(
        jsonEncode(response),
        200,
        headers: {'content-type': 'application/json'},
      );
    });
    api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    ai = ClinicalAiRemoteDataSource(
      apiClient: api,
      guard: guard,
      connectivity: network,
      loadSession: () async => AuthSessionState.ready(
        AuthTokenResponse(
          accessToken: token,
          refreshToken: 'refresh',
          userId: 21,
          role: role,
          clinicContextId: clinic,
        ),
      ),
    );
    addTearDown(() {
      api.close();
      client.close();
      guard.dispose();
    });
  });

  test(
    'explicit triage POST uses existing token and patient scope only once',
    () async {
      final result = await ai.assistTriage(patientId: 3, symptoms: 'Fever');
      expect(result.riskScore, 'medium');
      expect(requests, 1);
      await expectLater(
        ai.assistTriage(patientId: 3, symptoms: 'Fever'),
        throwsA(isA<ClinicalAiOutcomeUnknown>()),
      );
      expect(requests, 1);
    },
  );

  test(
    'unauthorized role, absent clinic, offline and unknown never dispatch',
    () async {
      role = 'patient';
      await expectLater(
        ai.assistTriage(patientId: 3, symptoms: 'Fever'),
        throwsA(isA<ClinicalAiUnavailable>()),
      );
      role = 'doctor';
      clinic = 0;
      await expectLater(
        ai.assistTriage(patientId: 3, symptoms: 'Fever'),
        throwsA(isA<ClinicalAiUnavailable>()),
      );
      clinic = 7;
      for (final status in [
        DeviceNetworkStatus.disconnected,
        DeviceNetworkStatus.unknown,
      ]) {
        network.status = status;
        await expectLater(
          ai.assistTriage(patientId: 3, symptoms: 'Fever'),
          throwsA(isA<ClinicalAiUnavailable>()),
        );
      }
      expect(requests, 0);
    },
  );

  test('duplicate drug names rejected before transport', () async {
    expect(
      () => ai.checkDrugInteractions(
        patientId: 3,
        drugNames: ['Aspirin', 'aspirin'],
      ),
      throwsA(isA<ClinicalAiUnavailable>()),
    );
    expect(requests, 0);
  });

  test('in-flight clinic switch discards result and blocks replay', () async {
    hold = Completer<void>();
    final first = ai.assistTriage(patientId: 3, symptoms: 'Fever');
    for (var i = 0; i < 40 && requests == 0; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 2));
    }
    expect(requests, 1);
    clinic = 9;
    hold!.complete();
    await expectLater(first, throwsA(isA<ClinicalAiOutcomeUnknown>()));
    clinic = 7;
    await expectLater(
      ai.assistTriage(patientId: 3, symptoms: 'Fever'),
      throwsA(isA<ClinicalAiOutcomeUnknown>()),
    );
    expect(requests, 1);
  });

  test(
    'malformed post-dispatch response is uncertain, never retried',
    () async {
      response = {
        'success': true,
        'data': {'risk_score': 'impossible'},
      };
      await expectLater(
        ai.assistTriage(patientId: 3, symptoms: 'Fever'),
        throwsA(isA<ClinicalAiOutcomeUnknown>()),
      );
      await expectLater(
        ai.assistTriage(patientId: 3, symptoms: 'Fever'),
        throwsA(isA<ClinicalAiOutcomeUnknown>()),
      );
      expect(requests, 1);
    },
  );

  test(
    'lab and drug POSTs map exact contract and retain clinic authority',
    () async {
      response = {
        'success': true,
        'data': {'interactions': [], 'recommendations': []},
      };
      final drugs = await ai.checkDrugInteractions(
        patientId: 3,
        drugNames: ['Aspirin', 'Warfarin'],
      );
      expect(drugs.interactions, isEmpty);
      response = {
        'success': true,
        'data': {'interpretation': 'Review result', 'abnormal_findings': []},
      };
      final lab = await ai.interpretLabResults(
        patientId: 3,
        labOrderId: 44,
        resultData: {'value': '12'},
      );
      expect(lab.interpretation, 'Review result');
      expect(requests, 2);
    },
  );
}
