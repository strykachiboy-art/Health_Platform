import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';
import 'package:clinic_system_pro/features/emar/data/models/emar_barcode_verification.dart';

EmarAdministration reviewed() => EmarAdministration.fromJson(
  {
    'id': 11,
    'clinic_id': 3,
    'patient_id': 5,
    'admission_id': 7,
    'prescription_id': 17,
    'prescription_item_id': 19,
    'record_version': 1,
    'record_status': 'draft',
    'status': 'due',
    'source_order_version': 2,
  },
  expectedClinicId: 3,
  expectedPatientId: 5,
  expectedAdmissionId: 7,
);

Map<String, dynamic> event({bool success = true}) => {
  'id': 41,
  'clinic_id': 3,
  'patient_id': success ? 5 : null,
  'admission_id': success ? 7 : null,
  'medication_administration_id': success ? 11 : null,
  'actor_staff_id': 17,
  'patient_barcode': 'PAT-5',
  'medication_barcode': 'MED-3',
  'scan_timestamp': '2026-11-09T11:00:00+00:00',
  'scan_device': 'test-scanner-1',
  'operation_identity': 'scan-one',
  'verification_result': success ? 'barcode_verified' : 'not_barcode_verified',
  'failure_code': success ? null : 'unknown_patient_barcode',
  'failure_reason': success ? null : 'Unknown patient barcode',
  'patient_match': success,
  'medication_match': success,
  'dose_match': success,
  'route_match': success,
  'time_window_match': success,
  'source_order_version': success ? 2 : null,
  'verification_policy_version': 1,
};

EmarBarcodeVerification parse(Map<String, dynamic> value) =>
    EmarBarcodeVerification.fromEnvelope(
      {
        'message': 'Barcode medication verification completed',
        'verification': value,
      },
      reviewed: reviewed(),
      operationKey: 'scan-one',
      patientBarcode: 'PAT-5',
      medicationBarcode: 'MED-3',
      scanDevice: 'test-scanner-1',
      scanTimestamp: DateTime.utc(2026, 11, 9, 11),
    );

void main() {
  test('verified result requires all server matches and target binding', () {
    final result = parse(event());
    expect(result.verified, isTrue);
    expect(result.administrationId, 11);
    expect(result.sourceOrderVersion, 2);
    expect(result.policyVersion, 1);
  });

  test('HTTP 200 failure may have null administration without throwing', () {
    final failed = parse(event(success: false));
    expect(failed.verified, isFalse);
    expect(failed.administrationId, isNull);
    expect(failed.failureCode, EmarBarcodeFailure.unknownPatientBarcode);
  });

  test('successful label with false match flag is rejected', () {
    expect(
      () => parse({...event(), 'dose_match': false}),
      throwsFormatException,
    );
  });

  test('successful label with failure code is rejected', () {
    expect(
      () => parse({...event(), 'failure_code': 'wrong_medication'}),
      throwsFormatException,
    );
  });

  test('cross-clinic or other-administration result is never trusted', () {
    expect(() => parse({...event(), 'clinic_id': 8}), throwsFormatException);
    expect(
      () => parse({...event(), 'medication_administration_id': 12}),
      throwsFormatException,
    );
    expect(() => parse({...event(), 'patient_id': 6}), throwsFormatException);
    expect(() => parse({...event(), 'admission_id': 8}), throwsFormatException);
  });

  test('unknown and inconsistent verification payloads fail closed', () {
    expect(
      () => parse({...event(), 'verification_result': 'maybe'}),
      throwsFormatException,
    );
    expect(
      () => parse({...event(success: false), 'failure_code': 'new_code'}),
      throwsFormatException,
    );
    expect(
      () => parse({...event(), 'source_order_version': 3}),
      throwsFormatException,
    );
    expect(
      () => parse({...event(success: false), 'failure_code': null}),
      throwsFormatException,
    );
  });

  test('operation identity, scanned bytes and time/device must echo', () {
    expect(
      () => parse({...event(), 'operation_identity': 'different'}),
      throwsFormatException,
    );
    expect(
      () => parse({...event(), 'patient_barcode': 'PAT-6'}),
      throwsFormatException,
    );
    expect(
      () => parse({...event(), 'medication_barcode': 'MED-9'}),
      throwsFormatException,
    );
    expect(
      () => parse({...event(), 'scan_device': 'other-device'}),
      throwsFormatException,
    );
    expect(
      () => parse({...event(), 'scan_timestamp': '2026-11-09T11:01:00Z'}),
      throwsFormatException,
    );
  });

  test('missing, invalid or naive scan time is not accepted', () {
    expect(
      () => parse({...event(), 'scan_timestamp': '2026-11-09T11:00:00'}),
      throwsFormatException,
    );
    expect(
      () => parse({...event(), 'verification_policy_version': 0}),
      throwsFormatException,
    );
    expect(
      () => parse({...event(), 'patient_match': 1}),
      throwsFormatException,
    );
    expect(
      () => EmarBarcodeVerification.fromEnvelope(
        {},
        reviewed: reviewed(),
        operationKey: 'scan-one',
        patientBarcode: 'PAT-5',
        medicationBarcode: 'MED-3',
        scanDevice: 'test-scanner-1',
        scanTimestamp: DateTime.utc(2026, 11, 9, 11),
      ),
      throwsFormatException,
    );
  });
}
