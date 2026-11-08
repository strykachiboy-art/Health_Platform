import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/nursing/data/models/ward_admission_workflow.dart';
import 'package:clinic_system_pro/features/nursing/data/models/ward_overview.dart';

Map<String, dynamic> _reservation({
  int patientId = 7,
  String status = 'pending',
}) => {
  'id': 33,
  'patient_id': patientId,
  'bed_id': 9,
  'reserved_by_id': 8,
  'status': status,
  'reason': 'Clinical review',
  'reserved_at': '2026-11-08T09:00:00+00:00',
  'expires_at': null,
};

void main() {
  test('active reservation is typed and patient scoped', () {
    final reservation = WardReservationRecord.fromJson(
      _reservation(),
      expectedPatientId: 7,
    );
    expect(reservation.id, 33);
    expect(reservation.bedId, 9);
    expect(reservation.isPendingAndUnexpired, isTrue);
    expect(reservation.reservedAt.isUtc, isTrue);
    expect(
      () => WardReservationRecord.fromJson(
        _reservation(patientId: 8),
        expectedPatientId: 7,
      ),
      throwsFormatException,
    );
  });

  test(
    'unknown reservation state, bad datetime and malformed ID fail closed',
    () {
      expect(
        () => WardReservationRecord.fromJson(
          _reservation(status: 'invented'),
          expectedPatientId: 7,
        ),
        throwsFormatException,
      );
      expect(
        () => WardReservationRecord.fromJson({
          ..._reservation(),
          'reserved_at': 'tomorrow',
        }, expectedPatientId: 7),
        throwsFormatException,
      );
      expect(
        () => WardReservationRecord.fromJson({
          ..._reservation(),
          'id': '33',
        }, expectedPatientId: 7),
        throwsFormatException,
      );
    },
  );

  test('transfer must match admission and both beds', () {
    final raw = {
      'id': 41,
      'admission_id': 11,
      'from_bed_id': 5,
      'to_bed_id': 9,
      'reason': 'Clinical transfer',
    };
    final transfer = WardTransferRecord.fromJson(
      raw,
      expectedAdmissionId: 11,
      expectedFromBedId: 5,
      expectedToBedId: 9,
    );
    expect(transfer.toBedId, 9);
    expect(
      () => WardTransferRecord.fromJson(
        raw,
        expectedAdmissionId: 12,
        expectedFromBedId: 5,
        expectedToBedId: 9,
      ),
      throwsFormatException,
    );
    expect(
      () => WardTransferRecord.fromJson(
        raw,
        expectedAdmissionId: 11,
        expectedFromBedId: 5,
        expectedToBedId: 10,
      ),
      throwsFormatException,
    );
  });

  test('snapshot only allows matching server bed and role', () {
    const ward = WardRecord(
      id: 3,
      clinicId: 2,
      name: 'General',
      kind: WardKind.general,
      capacity: 20,
    );
    const bed = WardBedRecord(
      id: 9,
      wardId: 3,
      number: '09',
      status: WardBedStatus.available,
    );
    const viewer = WardAdmissionSnapshot(
      patientId: 7,
      ward: ward,
      bed: bed,
      activeAdmission: null,
      activeReservation: null,
      mayWrite: false,
    );
    const clinician = WardAdmissionSnapshot(
      patientId: 7,
      ward: ward,
      bed: bed,
      activeAdmission: null,
      activeReservation: null,
      mayWrite: true,
    );
    expect(viewer.allows(WardAdmissionAction.admitDirect), isFalse);
    expect(clinician.allows(WardAdmissionAction.admitDirect), isTrue);
    expect(clinician.allows(WardAdmissionAction.transfer), isFalse);
  });
}
