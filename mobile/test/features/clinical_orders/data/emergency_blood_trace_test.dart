import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/blood_product_order.dart';

Map<String, dynamic> row() => <String, dynamic>{
  'id': 17,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 9,
  'ordered_by_staff_id': 8,
  'product_type': 'packed_red_blood_cells',
  'units_requested': 2,
  'units_issued': 2,
  'indication': 'Emergency red-cell issue',
  'priority': 'stat',
  'status': 'completed',
  'version': 5,
  'patient_blood_type_at_order': 'O+',
  'compatibility_status': 'emergency_release',
  'compatibility_verified_by_staff_id': 12,
  'compatibility_verified_at': '2026-11-08T08:00:00+00:00',
  'patient_blood_type_verified': 'O+',
  'product_blood_type': 'O-',
  'emergency_release_reason': 'Urgent authorized release',
  'crossmatch_reference': null,
  'ordered_at': '2026-11-08T07:00:00+00:00',
  'completed_at': '2026-11-08T08:15:00+00:00',
  'fulfilled_by_staff_id': 12,
  'cancelled_at': null,
  'emergency_release_requested': true,
  'emergency_request_reason': 'Urgent hemorrhage',
  'emergency_requested_by_staff_id': 8,
  'emergency_requested_at': '2026-11-08T07:30:00+00:00',
  'emergency_review_outcome': null,
  'emergency_reviewed_by_staff_id': null,
  'emergency_reviewed_at': null,
  'emergency_review_note': null,
  'issued_units': <Map<String, dynamic>>[
    {
      'id': 41,
      'clinic_id': 3,
      'patient_id': 7,
      'admission_id': 9,
      'order_id': 17,
      'unit_identifier': 'EM-001',
      'product_blood_type': 'O-',
      'issued_by_staff_id': 12,
      'issued_at': '2026-11-08T08:15:00+00:00',
    },
    {
      'id': 42,
      'clinic_id': 3,
      'patient_id': 7,
      'admission_id': 9,
      'order_id': 17,
      'unit_identifier': 'EM-002',
      'product_blood_type': 'O-',
      'issued_by_staff_id': 12,
      'issued_at': '2026-11-08T08:15:00+00:00',
    },
  ],
  'lab_followups': <Map<String, dynamic>>[
    {
      'id': 51,
      'clinic_id': 3,
      'patient_id': 7,
      'admission_id': 9,
      'order_id': 17,
      'issued_unit_id': 41,
      'status': 'pending',
      'lab_order_item_id': null,
      'recorded_by_staff_id': null,
      'recorded_at': null,
      'evidence_note': null,
      'escalation_note': null,
    },
    {
      'id': 52,
      'clinic_id': 3,
      'patient_id': 7,
      'admission_id': 9,
      'order_id': 17,
      'issued_unit_id': 42,
      'status': 'pending',
      'lab_order_item_id': null,
      'recorded_by_staff_id': null,
      'recorded_at': null,
      'evidence_note': null,
      'escalation_note': null,
    },
  ],
};

Map<String, dynamic> clone(Map<String, dynamic> input) =>
    jsonDecode(jsonEncode(input)) as Map<String, dynamic>;

BloodProductOrder parsed(Map<String, dynamic> value) =>
    BloodProductOrder.fromJson(
      value,
      expectedClinicId: 3,
      expectedPatientId: 7,
      expectedAdmissionId: 9,
    );

void main() {
  test('emergency trace reads two independently pending unit records', () {
    final order = parsed(row());
    final trace = order.emergencyTrace!;
    expect(trace.issuedUnits.map((e) => e.identifier).toList(), <String>[
      'EM-001',
      'EM-002',
    ]);
    expect(trace.pendingCount, 2);
    expect(trace.recordedCount, 0);
    expect(trace.escalatedCount, 0);
    expect(trace.reviewOutcome, isNull);
  });

  test('verified evidence is linked not certified as compatible', () {
    final data = row();
    final first = (data['lab_followups'] as List)[0] as Map<String, dynamic>;
    first.addAll({
      'status': 'evidence_recorded',
      'lab_order_item_id': 99,
      'recorded_by_staff_id': 12,
      'recorded_at': '2026-11-08T09:30:00+00:00',
      'evidence_note': 'Recorded laboratory evidence',
    });
    final trace = parsed(data).emergencyTrace!;
    expect(trace.pendingCount, 1);
    expect(trace.recordedCount, 1);
    expect(trace.labFollowups.first.labOrderItemId, 99);
  });

  test(
    'discrepancy and independent physician review are separately represented',
    () {
      final data = row();
      final first = (data['lab_followups'] as List)[0] as Map<String, dynamic>;
      first.addAll({
        'status': 'discrepancy_escalated',
        'lab_order_item_id': 99,
        'recorded_by_staff_id': 12,
        'recorded_at': '2026-11-08T09:30:00+00:00',
        'evidence_note': 'Discrepant laboratory result',
        'escalation_note': 'Escalated to blood bank',
      });
      data['emergency_review_outcome'] = 'reviewed';
      data['emergency_reviewed_by_staff_id'] = 14;
      data['emergency_reviewed_at'] = '2026-11-08T10:00:00+00:00';
      data['emergency_review_note'] = 'Independent documentation review';
      final trace = parsed(data).emergencyTrace!;
      expect(trace.escalatedCount, 1);
      expect(trace.pendingCount, 1);
      expect(trace.reviewOutcome, 'reviewed');
    },
  );

  test('foreign issued unit tenant fails closed', () {
    final data = row();
    ((data['issued_units'] as List)[0] as Map)['clinic_id'] = 4;
    expect(() => parsed(data), throwsFormatException);
  });

  test('foreign laboratory evidence patient fails closed', () {
    final data = row();
    ((data['lab_followups'] as List)[0] as Map)['patient_id'] = 8;
    expect(() => parsed(data), throwsFormatException);
  });

  test('duplicate unit and duplicate evidence mapping fail closed', () {
    final duplicateUnit = row();
    ((duplicateUnit['issued_units'] as List)[1] as Map)['id'] = 41;
    expect(() => parsed(duplicateUnit), throwsFormatException);
    final duplicateLab = row();
    ((duplicateLab['lab_followups'] as List)[1] as Map)['issued_unit_id'] = 41;
    expect(() => parsed(duplicateLab), throwsFormatException);
  });

  test('reusing a laboratory result for two units fails closed', () {
    final data = row();
    for (final raw in (data['lab_followups'] as List)) {
      (raw as Map<String, dynamic>).addAll(<String, dynamic>{
        'status': 'evidence_recorded',
        'lab_order_item_id': 99,
        'recorded_by_staff_id': 12,
        'recorded_at': '2026-11-08T09:30:00+00:00',
        'evidence_note': 'Evidence',
      });
    }
    expect(() => parsed(data), throwsFormatException);
  });

  test('incomplete pending evidence and missing unit trace fail closed', () {
    final data = row();
    ((data['lab_followups'] as List)[0] as Map)['lab_order_item_id'] = 99;
    expect(() => parsed(data), throwsFormatException);
    final missing = row();
    (missing['issued_units'] as List).removeLast();
    expect(() => parsed(missing), throwsFormatException);
  });

  test('review cannot be self-approved or partially recorded', () {
    final data = row();
    data['emergency_review_outcome'] = 'reviewed';
    data['emergency_reviewed_by_staff_id'] = 8;
    data['emergency_reviewed_at'] = '2026-11-08T09:30:00+00:00';
    data['emergency_review_note'] = 'Self-review';
    expect(() => parsed(data), throwsFormatException);
    final partial = row();
    partial['emergency_reviewed_by_staff_id'] = 14;
    expect(() => parsed(partial), throwsFormatException);
  });

  test('pre-verification physician emergency intent is read-only', () {
    final data = row();
    data['compatibility_status'] = 'pending';
    data['status'] = 'active';
    data['units_issued'] = 0;
    data['completed_at'] = null;
    data['fulfilled_by_staff_id'] = null;
    data['compatibility_verified_at'] = null;
    data['compatibility_verified_by_staff_id'] = null;
    data['patient_blood_type_verified'] = null;
    data['emergency_release_reason'] = null;
    data['product_blood_type'] = null;
    data['issued_units'] = <Map<String, dynamic>>[];
    data['lab_followups'] = <Map<String, dynamic>>[];
    final trace = parsed(data).emergencyTrace!;
    expect(trace.requestedByStaffId, 8);
    expect(trace.issuedUnits, isEmpty);
    expect(trace.pendingCount, 0);
  });

  test('legacy emergency payload gives no approval or trace inference', () {
    final legacy = row();
    for (final key in <String>[
      'emergency_release_requested',
      'emergency_request_reason',
      'emergency_requested_by_staff_id',
      'emergency_requested_at',
      'emergency_review_outcome',
      'emergency_reviewed_by_staff_id',
      'emergency_reviewed_at',
      'emergency_review_note',
      'issued_units',
      'lab_followups',
    ]) {
      legacy.remove(key);
    }
    expect(parsed(legacy).emergencyTrace, isNull);
    final partial = clone(legacy)..['issued_units'] = [];
    expect(() => parsed(partial), throwsFormatException);
  });
}
