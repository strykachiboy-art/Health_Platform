import 'dart:convert';
import 'dart:typed_data';
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

class _Online implements DeviceConnectivitySource {
  @override
  Future<DeviceNetworkStatus> checkStatus() async => DeviceNetworkStatus.connected;
  @override
  Stream<DeviceNetworkStatus> get statusChanges => const Stream<DeviceNetworkStatus>.empty();
}

DoctorObservationSource fixture(SessionNavigationGuard guard, MockClient client) =>
  DoctorObservationSource(PatientAllergyRemoteDataSource(
    apiClient: ApiClient(config: const BackendConfig(baseUrl: 'http://127.0.0.1:5000', allowInsecureHttp: true), client: client),
    loadSession: () async => AuthSessionState.ready(AuthTokenResponse(accessToken: 'doctor-token', refreshToken: 'refresh', userId: 41, role: 'doctor', clinicContextId: 7, tenantId: '12345678-1234-1234-1234-123456789abc')),
    sessionNavigationGuard: guard), _Online(), Wd09cfOneShotGate());
http.Response response(Map<String, dynamic> row, [int status = 200]) => http.Response(jsonEncode(row), status);
Map<String, dynamic> allergy(String stamp, {int clinic = 7, String name = 'Example', String clinical = 'active', String verification = 'unconfirmed'}) => {
  'id': 12, 'clinic_id': clinic, 'patient_id': 11, 'allergen_name': name,
  'allergen_type': 'food', 'severity': 'unknown', 'clinical_status': clinical,
  'verification_status': verification, 'recorded_at': '2026-11-26T08:00:00Z',
  'created_at': '2026-11-26T08:00:00Z', 'updated_at': stamp, 'recorded_by_id': 41,
  'allergen_code': null, 'reaction': null, 'notes': null, 'onset_at': null,
};
void main() {
  const revision = '2026-11-26T08:00:00.123456Z';
  const newer = '2026-11-26T08:00:01.123456Z';
  for (final mode in ['edit', 'transition', 'stale', 'conflict', 'foreign', 'uncertain']) {
    test('Doctor allergy CAS $mode', () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var reads = 0;
      var patches = 0;
      final client = MockClient((request) async {
        expect(request.headers['authorization'], 'Bearer doctor-token');
        if (request.method == 'GET' && request.url.path.endsWith('/patients/11')) {
          return response({'success': true, 'data': {'id': 11, 'clinic_id': 7, 'is_active': true, 'first_name': 'Synthetic', 'last_name': 'Patient'}});
        }
        if (request.method == 'GET') {
          reads++;
          expect(request.url.path, '/api/v1/patients/11/allergies/12');
          return response({'success': true, 'data': allergy(mode == 'stale' && reads > 1 ? newer : revision, clinic: mode == 'foreign' ? 8 : 7)});
        }
        patches++;
        expect(request.method, 'PATCH');
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['expected_updated_at'], revision);
        expect(body.containsKey('clinic_id'), isFalse);
        if (mode == 'conflict') { return response({'success': false}, 409); }
        if (mode == 'uncertain') { return response({'success': false}, 500); }
        if (mode == 'transition') {
          expect(request.url.path, '/api/v1/patients/11/allergies/12/status');
          expect(body['reason'], 'Reviewed evidence');
          expect(body['verification_status'], 'refuted');
          return response({'success': true, 'data': allergy(newer, clinical: 'inactive', verification: 'refuted')});
        }
        expect(request.url.path, '/api/v1/patients/11/allergies/12');
        expect(body['allergen_name'], 'Edited');
        return response({'success': true, 'data': allergy(newer, name: 'Edited')});
      });
      addTearDown(client.close);
      final source = fixture(guard, client);
      final patient = await source.inspect(11);
      if (mode == 'foreign') {
        await expectLater(source.detail(patient, 12), throwsFormatException);
        expect(patches, 0);
        return;
      }
      final prior = await source.detail(patient, 12);
      final attempt = mode == 'transition'
          ? source.transitionAllergy(patient, prior, intentId: 'intent-123', reason: 'Reviewed evidence', verificationStatus: 'refuted')
          : source.editAllergy(patient, prior, intentId: 'intent-123', name: 'Edited', type: 'food', severity: 'unknown');
      if (mode == 'edit' || mode == 'transition') {
        final saved = await attempt;
        expect(saved.updatedAt.toIso8601String(), newer);
        expect(saved.id, 12);
      } else if (mode == 'uncertain') {
        await expectLater(attempt, throwsA(isA<DoctorObservationUnconfirmed>()));
        await expectLater(source.transitionAllergy(patient, prior, intentId: 'intent-456', reason: 'Review', clinicalStatus: 'resolved'), throwsA(isA<DoctorObservationUnconfirmed>()));
      } else {
        await expectLater(attempt, throwsA(isA<DoctorObservationConflict>()));
      }
      expect(patches, mode == 'stale' ? 0 : 1);
    });
  }
  test('discharge requires complete clinician-authored document', () {
    final fields = {for (final key in DoctorClinicalRecords.dischargeFields.keys) key: 'Synthetic $key'};
    final body = DoctorClinicalRecords.dischargeDocument(fields, 'First\nSecond');
    expect(body['secondary_diagnoses'], ['First', 'Second']);
    expect(() => DoctorClinicalRecords.dischargeDocument({}, ''), throwsFormatException);
    expect(() => DoctorClinicalRecords.dischargeDocument({...fields, 'patient_id': '1'}, ''), throwsFormatException);
  });
  test('profile rejects non-image and oversized payload', () {
    expect(DoctorDirectoryProfile.imageMime(Uint8List.fromList([137,80,78,71,13,10,26,10])), 'image/png');
    expect(DoctorDirectoryProfile.imageMime(Uint8List.fromList([255,216,255])), 'image/jpeg');
    expect(() => DoctorDirectoryProfile.imageMime(Uint8List.fromList([1,2,3])), throwsFormatException);
    expect(() => DoctorDirectoryProfile.imageMime(Uint8List(5 * 1024 * 1024 + 1)), throwsFormatException);
  });
  test('consultation directory rejects foreign patient before returning PHI', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((request) async {
      if (request.url.path.endsWith('/my-patients')) {
        return response({'success': true, 'data': [{'id': 11}], 'pagination': {'page': 1, 'per_page': 20, 'total': 1, 'pages': 1, 'has_next': false, 'has_prev': false}});
      }
      return response({'success': true, 'data': {'id': 11, 'clinic_id': 99}});
    });
    addTearDown(client.close);
    final source = fixture(guard, client);
    await expectLater(source.myPatients(await source.directorySession()), throwsA(isA<FormatException>()));
  });
}
