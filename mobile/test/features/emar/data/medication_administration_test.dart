import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

Map<String, dynamic> administration({
  int clinic = 3,
  int patient = 5,
  int admission = 7,
  String status = 'due',
}) => {
  'id': 11,
  'clinic_id': clinic,
  'patient_id': patient,
  'admission_id': admission,
  'prescription_id': 17,
  'prescription_item_id': 19,
  'status': status,
  'record_status': 'draft',
  'record_version': 1,
  'scheduled_at': '2026-11-09T11:00:00+01:00',
  'dose_value': '0.125',
  'dose_unit': 'mg',
  'route_code': 'oral',
  'administered_at': null,
  'source_order_version': 2,
  'verification_result': null,
  'barcode_verification_event_id': null,
};

Map<String, dynamic> event({
  int clinic = 3,
  int administrationId = 11,
  String result = 'barcode_verified',
}) => {
  'id': 31,
  'clinic_id': clinic,
  'medication_administration_id': administrationId,
  'actor_staff_id': 41,
  'verification_result': result,
  'failure_code': result == 'barcode_verified' ? null : 'wrong_medication',
  'failure_reason': result == 'barcode_verified' ? null : 'Mismatch',
  'patient_match': true,
  'medication_match': result == 'barcode_verified',
  'dose_match': true,
  'route_match': true,
  'time_window_match': true,
  'scan_timestamp': '2026-11-09T10:59:30Z',
  'verification_policy_version': 1,
};

void main() {
  test('read record retains precise dose and UTC times', () {
    final row = EmarAdministration.fromJson(
      administration(),
      expectedClinicId: 3,
      expectedPatientId: 5,
      expectedAdmissionId: 7,
      expectedId: 11,
    );
    expect(row.doseValue, '0.125');
    expect(row.scheduledAt!.toIso8601String(), '2026-11-09T10:00:00.000Z');
    expect(row.isPending, isTrue);
    expect(row.recordStatus, EmarRecordStatus.draft);
  });

  test('record cannot escape clinic patient admission or ID scope', () {
    for (final row in [
      administration(clinic: 8),
      administration(patient: 8),
      administration(admission: 8),
    ]) {
      expect(
        () => EmarAdministration.fromJson(
          row,
          expectedClinicId: 3,
          expectedPatientId: 5,
          expectedAdmissionId: 7,
        ),
        throwsFormatException,
      );
    }
    expect(
      () => EmarAdministration.fromJson(
        administration(),
        expectedClinicId: 3,
        expectedId: 33,
      ),
      throwsFormatException,
    );
  });

  test('statuses are closed; missing and malformed values are rejected', () {
    for (final status in [
      'scheduled',
      'due',
      'administered',
      'held',
      'refused',
      'missed',
      'cancelled',
    ]) {
      final row = EmarAdministration.fromJson(
        administration(status: status),
        expectedClinicId: 3,
      );
      expect(row.status.name, status);
    }
    for (final broken in [
      {...administration(), 'status': 'late'},
      {...administration(), 'status': null},
      {...administration(), 'record_status': 'signed'},
      {...administration(), 'record_version': 0},
      {...administration(), 'dose_value': 0.125},
      {...administration(), 'scheduled_at': '2026-11-09T11:00:00'},
    ]) {
      expect(
        () => EmarAdministration.fromJson(broken, expectedClinicId: 3),
        throwsFormatException,
      );
    }
  });

  test('raw paginated envelope checks page, duplicates and tenant', () {
    final json = <String, dynamic>{
      'items': [administration()],
      'total': 51,
      'page': 2,
      'per_page': 50,
    };
    EmarAdministrationPage parse(Map<String, dynamic> value) =>
        EmarAdministrationPage.fromJson(
          value,
          expectedClinicId: 3,
          expectedAdmissionId: 7,
          expectedPatientId: 5,
          requestedPage: 2,
          requestedPerPage: 50,
        );
    final page = parse(json);
    expect(page.items.single.id, 11);
    expect(page.hasNext, isFalse);
    expect(() => parse({...json, 'page': 1}), throwsFormatException);
    expect(() => parse({'success': true, 'data': json}), throwsFormatException);
    expect(
      () => parse({
        ...json,
        'items': [administration(), administration()],
      }),
      throwsFormatException,
    );
    expect(
      () => parse({
        ...json,
        'items': [administration(clinic: 200)],
      }),
      throwsFormatException,
    );
  });

  test('empty page is valid and immutable', () {
    final page = EmarAdministrationPage.fromJson(
      {'items': [], 'total': 0, 'page': 1, 'per_page': 50},
      expectedClinicId: 3,
      expectedAdmissionId: 7,
      requestedPage: 1,
      requestedPerPage: 50,
    );
    expect(page.items, isEmpty);
    expect(
      () => page.items.add(
        EmarAdministration.fromJson(administration(), expectedClinicId: 3),
      ),
      throwsUnsupportedError,
    );
  });

  test('verification event is server-owned and preserves failed scan', () {
    final good = EmarBarcodeEvent.fromJson(
      event(),
      expectedClinicId: 3,
      expectedAdministrationId: 11,
    );
    expect(good.isVerified, isTrue);
    final failed = EmarBarcodeEvent.fromJson(
      event(result: 'not_barcode_verified'),
      expectedClinicId: 3,
      expectedAdministrationId: 11,
    );
    expect(failed.isVerified, isFalse);
    expect(failed.failureCode, EmarBarcodeFailure.wrongMedication);
  });

  test(
    'verification rejects unknown failures, inconsistent success and scope',
    () {
      for (final raw in [
        event(clinic: 90),
        event(administrationId: 12),
        {...event(), 'failure_code': 'invented'},
        {...event(), 'medication_match': false},
        {...event(), 'verification_result': 'unknown'},
        {...event(), 'time_window_match': null},
        {...event(), 'scan_timestamp': '2026-11-09 10:59:30'},
      ]) {
        expect(
          () => EmarBarcodeEvent.fromJson(
            raw,
            expectedClinicId: 3,
            expectedAdministrationId: 11,
          ),
          throwsFormatException,
        );
      }
    },
  );

  test('history must have raw nested envelope, exact total and unique IDs', () {
    EmarBarcodeHistory parse(Map<String, dynamic> json) =>
        EmarBarcodeHistory.fromJson(
          json,
          expectedClinicId: 3,
          expectedAdministrationId: 11,
        );
    final history = parse({
      'history': {
        'items': [event()],
        'total': 1,
      },
    });
    expect(history.items.single.id, 31);
    expect(
      () => parse({
        'items': [event()],
        'total': 1,
      }),
      throwsFormatException,
    );
    expect(
      () => parse({
        'history': {
          'items': [event()],
          'total': 2,
        },
      }),
      throwsFormatException,
    );
    expect(
      () => parse({
        'history': {
          'items': [event(), event()],
          'total': 2,
        },
      }),
      throwsFormatException,
    );
  });
}
