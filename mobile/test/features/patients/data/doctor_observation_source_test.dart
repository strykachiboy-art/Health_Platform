import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/administration/data/wd09cf_operations_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/doctor_observation_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_allergy_remote_data_source.dart';

class _Network implements DeviceConnectivitySource {
  _Network(this.value);
  DeviceNetworkStatus value;
  @override
  Future<DeviceNetworkStatus> checkStatus() async => value;
  @override
  Stream<DeviceNetworkStatus> get statusChanges => const Stream<DeviceNetworkStatus>.empty();
}

void main() {
  test('vitals draft preserves decimal and integer wire types', () {
    expect(DoctorObservationSource.vitalDraft({'temperature':'36.5', 'heart_rate':'78'}),
        {'temperature':'36.5', 'heart_rate':78});
    for (final value in [{'clinic_id':'8'}, {'temperature':'NaN'}, {'heart_rate':'1.5'},
        {'oxygen_saturation':'101'}, {'temperature':''}]) {
      expect(() => DoctorObservationSource.vitalDraft(value), throwsFormatException);
    }
  });
  test('allergy draft cannot carry identity or status', () {
    expect(DoctorObservationSource.allergyDraft(name:' Example ', type:'food', severity:'unknown'),
      {'allergen_name':'Example','allergen_type':'food','severity':'unknown'});
    expect(() => DoctorObservationSource.allergyDraft(name:'', type:'food', severity:'unknown'), throwsFormatException);
  });
  for (final mode in ['valid', 'wrong-role', 'foreign-patient', 'offline', 'invalidated', 'wrong-receipt', 'uncertain-reentry']) {
    test('Doctor observations $mode', () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var gets = 0;
      var writes = 0;
      final client = MockClient((request) async {
        expect(request.headers['authorization'], 'Bearer observation-test');
        if (request.method == 'GET') {
          gets++;
          expect(request.url.path, '/api/v1/patients/11');
          return http.Response(jsonEncode({'success':true,'data':{'id':11,
            'clinic_id':mode == 'foreign-patient' ? 9 : 7,'is_active':true,
            'first_name':'Synthetic','last_name':'Patient'}}), 200);
        }
        writes++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/patients/11/vitals');
        expect(jsonDecode(request.body), {'temperature':'36.5','heart_rate':78});
        if (mode == 'invalidated') {
          guard.invalidate();
        }
        if (mode == 'uncertain-reentry') {
          return http.Response('{}', 500);
        }
        return http.Response(jsonEncode({'success':true,'data':{'id':32,
          'patient_id':mode == 'wrong-receipt' ? 12 : 11,'recorded_by_id':41,
          'consultation_id':null,'temperature':'36.5','heart_rate':78,
          'recorded_at':'2026-11-26T08:00:00Z'}}), 201);
      });
      addTearDown(client.close);
      final network = _Network(mode == 'offline' ? DeviceNetworkStatus.disconnected : DeviceNetworkStatus.connected);
      final gate = Wd09cfOneShotGate();
      final source = DoctorObservationSource(PatientAllergyRemoteDataSource(
        apiClient: ApiClient(config: const BackendConfig(baseUrl:'http://127.0.0.1:5000', allowInsecureHttp:true),client:client),
        loadSession: () async => AuthSessionState.ready(AuthTokenResponse(accessToken:'observation-test',refreshToken:'refresh',userId:41,role:mode == 'wrong-role' ? 'admin' : 'doctor',clinicContextId:7,tenantId:'12345678-1234-1234-1234-123456789abc')),
        sessionNavigationGuard:guard), network, gate);
      if (mode == 'wrong-role' || mode == 'foreign-patient') {
        await expectLater(source.inspect(11), throwsA(isA<DoctorObservationUnavailable>()));
        expect(writes,0);
        expect(gets,mode == 'wrong-role' ? 0 : 1);
        return;
      }
      final review = await source.inspect(11);
      Future<void> submit(String intent) async {
        await source.recordVitals(review,intentId:intent,measurements:{'temperature':'36.5','heart_rate':'78'});
      }
      if (mode == 'valid') {
        await submit('test-intent-1');
        await expectLater(submit('test-intent-1'), throwsA(isA<DoctorObservationUnconfirmed>()));
      } else if (mode == 'offline') {
        await expectLater(submit('test-intent-1'), throwsA(isA<DoctorObservationUnavailable>()));
      } else {
        await expectLater(submit('test-intent-1'), throwsA(isA<DoctorObservationUnconfirmed>()));
        if (mode == 'uncertain-reentry') {
          await expectLater(submit('test-intent-2'), throwsA(isA<DoctorObservationUnconfirmed>()));
        }
      }
      expect(writes,mode == 'offline' ? 0 : 1);
    });
  }
}
