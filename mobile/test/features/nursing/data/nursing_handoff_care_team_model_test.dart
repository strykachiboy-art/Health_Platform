import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/features/nursing/data/models/nursing_handoff_care_team.dart';

Map<String, dynamic> _assignment({
  int id = 1,
  String kind = 'primary_nurse',
  int staffId = 9,
  bool active = true,
}) => {
  'assignment_id': id,
  'assignment_kind': kind,
  'staff_id': staffId,
  'staff_user_id': 18,
  'staff_display_name': 'Assigned clinician',
  'staff_active': active,
  'assigned_at': '2026-11-08T08:00:00Z',
  'ended_at': null,
};

Map<String, dynamic> _team() => {
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 11,
  'admission_status': 'admitted',
  'admission_active': true,
  'authoritative_assignment_available': true,
  'responsibility': {
    'primary_doctor_staff_id': null,
    'primary_nurse_staff_id': 9,
    'primary_doctor': null,
    'primary_nurse': _assignment(),
    'primary_doctor_confirmed': false,
    'primary_nurse_confirmed': true,
    'confirmed': false,
  },
};

Map<String, dynamic> _handoff({
  int clinic = 3,
  String status = 'draft',
  int? incoming = 10,
  int version = 1,
}) => {
  'id': 20,
  'clinic_id': clinic,
  'patient_id': 7,
  'admission_id': 11,
  'outgoing_staff_id': 9,
  'incoming_staff_id': incoming,
  'status': status,
  'version': version,
  'shift_start_at': '2026-11-08T08:00:00Z',
  'shift_end_at': '2026-11-08T16:00:00Z',
  'situation': 'Clinical situation',
  'background': 'Pertinent background',
  'assessment': 'Clinical assessment',
  'recommendation': 'Recommended actions',
  'safety_concerns': null,
  'pending_actions': null,
};

void main() {
  test('care-team response parses confirmed active primary responsibility', () {
    final parsed = AdmissionCareTeam.fromJson(
      _team(),
      clinicId: 3,
      patientId: 7,
      admissionId: 11,
    );
    expect(parsed.primaryNurse?.staffId, 9);
    expect(parsed.primaryDoctor, isNull);
    expect(parsed.confirmed, isFalse);
    expect(parsed.admissionActive, isTrue);
  });

  test(
    'care-team response fails closed for cross-clinic and false authority',
    () {
      expect(
        () => AdmissionCareTeam.fromJson(
          _team(),
          clinicId: 4,
          patientId: 7,
          admissionId: 11,
        ),
        throwsFormatException,
      );
      final changed = _team();
      changed['authoritative_assignment_available'] = false;
      expect(
        () => AdmissionCareTeam.fromJson(
          changed,
          clinicId: 3,
          patientId: 7,
          admissionId: 11,
        ),
        throwsFormatException,
      );
    },
  );

  test('responsibility staff-id mismatch is rejected', () {
    final changed = _team();
    final responsibility = changed['responsibility'] as Map<String, dynamic>;
    responsibility['primary_nurse_staff_id'] = 55;
    expect(
      () => AdmissionCareTeam.fromJson(
        changed,
        clinicId: 3,
        patientId: 7,
        admissionId: 11,
      ),
      throwsFormatException,
    );
  });

  test('handoff status and readiness derive from server records', () {
    final parsed = NursingHandoffEntry.fromJson(
      _handoff(),
      clinicId: 3,
      patientId: 7,
      admissionId: 11,
    );
    expect(parsed.mayReady, isTrue);
    expect(parsed.mayAcknowledge, isFalse);
    final ready = NursingHandoffEntry.fromJson(
      _handoff(status: 'ready'),
      clinicId: 3,
      patientId: 7,
      admissionId: 11,
    );
    expect(ready.mayAcknowledge, isTrue);
    final acknowledged = NursingHandoffEntry.fromJson(
      _handoff(status: 'acknowledged'),
      clinicId: 3,
      patientId: 7,
      admissionId: 11,
    );
    expect(acknowledged.mayCancel, isFalse);
  });

  test('handoff cannot be ready without identified incoming nurse', () {
    expect(
      () => NursingHandoffEntry.fromJson(
        _handoff(status: 'ready', incoming: null),
        clinicId: 3,
        patientId: 7,
        admissionId: 11,
      ),
      throwsFormatException,
    );
    expect(
      () => NursingHandoffEntry.fromJson(
        _handoff(clinic: 4),
        clinicId: 3,
        patientId: 7,
        admissionId: 11,
      ),
      throwsFormatException,
    );
  });

  test('self-handoff with same incoming and outgoing staff is rejected', () {
    expect(
      () => NursingHandoffEntry.fromJson(
        _handoff(incoming: 9),
        clinicId: 3,
        patientId: 7,
        admissionId: 11,
      ),
      throwsFormatException,
    );
  });

  test('list and assignment history reject duplicate IDs', () {
    expect(
      () => handoffList(
        {
          'items': [_handoff(), _handoff()],
          'total': 2,
        },
        clinicId: 3,
        patientId: 7,
        admissionId: 11,
      ),
      throwsFormatException,
    );
    expect(
      () => careTeamHistory({
        'items': [_assignment(), _assignment()],
        'total': 2,
      }),
      throwsFormatException,
    );
  });

  test(
    'assignment kind uses backend literal and history cannot silently shrink',
    () {
      expect(
        primaryKindWire(PrimaryClinicianKind.primaryDoctor),
        'primary_doctor',
      );
      expect(
        primaryKindWire(PrimaryClinicianKind.primaryNurse),
        'primary_nurse',
      );
      expect(
        () => careTeamHistory({
          'items': [_assignment()],
          'total': 2,
        }),
        throwsFormatException,
      );
    },
  );
}
