import 'package:clinic_system_pro/core/models/clinic_hours_status.dart';
import 'package:clinic_system_pro/features/administration/data/wd13_clinic_management_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/models/appointment_record.dart';
import 'package:clinic_system_pro/features/profile/data/profile_remote_data_source.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, dynamic> clinic() => <String, dynamic>{
  'id': 7,
  'name': 'Clinic',
  'clinic_type': 'general',
  'status': 'active',
  'parent_clinic_id': null,
  'is_headquarters': true,
  'timezone': 'Africa/Lagos',
  'opening_time': '20:00:00',
  'closing_time': '06:00:00',
};
Map<String, dynamic> appointment() => <String, dynamic>{
  'id': 4,
  'clinic_id': 7,
  'patient_id': 8,
  'staff_id': 9,
  'status': 'scheduled',
  'appointment_type': 'in_person',
  'scheduled_start': '2026-12-01T07:00:00',
  'scheduled_end': '2026-12-01T08:00:00',
  'reminder_sent': false,
};
AppointmentRecord parse(Map<String, dynamic> json) =>
    AppointmentRecord.fromJson(json, expectedClinicId: 7, expectedPatientId: 8);
void main() {
  test('legacy missing metadata does not invent an open state', () {
    expect(Wd13Clinic.parse(clinic()).hoursStatus, isNull);
    expect(parse(appointment()).clinicHoursConflict, isNull);
  });
  test('overnight status and next opening come only from server', () {
    final value = Wd13Clinic.parse({
      ...clinic(),
      'operating_mode': 'scheduled',
      'is_open_now': false,
      'next_opening_at': '2026-12-01T19:00:00+00:00',
    }).hoursStatus!;
    expect(value.isOpenNow, isFalse);
    expect(value.summary, contains('20:00:00 – 06:00:00'));
    expect(value.summary, contains('Next opening (UTC)'));
  });
  test('older hours 01 mode-only response remains compatible', () {
    final value = ClinicHoursStatus.fromJson({
      'operating_mode': 'always_open',
    })!;
    expect(value.isOpenNow, isNull);
    expect(value.summary, contains('unknown'));
  });
  test('explicit false, true and null conflicts are not conflated', () {
    for (final value in <bool?>[true, false, null]) {
      final record = parse({...appointment(), 'clinic_hours_conflict': value});
      expect(record.clinicHoursConflict, value);
      expect(record.status, AppointmentRecordStatus.scheduled);
      expect(record.scheduledStart, '2026-12-01T07:00:00');
    }
    expect(
      () => parse({...appointment(), 'clinic_hours_conflict': 'true'}),
      throwsFormatException,
    );
    expect(
      () => parse({
        ...appointment(),
        'clinic_id': 999,
        'clinic_hours_conflict': true,
      }),
      throwsFormatException,
    );
  });
  test('malformed or inconsistent metadata rejected', () {
    for (final value in <Map<String, dynamic>>[
      {'operating_mode': 'unknown'},
      {'operating_mode': 'scheduled', 'is_open_now': 'false'},
      {
        'operating_mode': 'scheduled',
        'is_open_now': false,
        'next_opening_at': '2026-12-01T19:00:00',
      },
      {'operating_mode': 'unspecified', 'is_open_now': true},
      {'operating_mode': 'always_open', 'is_open_now': false},
    ]) {
      expect(() => ClinicHoursStatus.fromJson(value), throwsFormatException);
    }
  });
  test('private profile metadata remains clinic scoped', () {
    final envelope = <String, dynamic>{
      'success': true,
      'data': <String, dynamic>{
        'user': <String, dynamic>{'id': 5, 'is_active': true, 'clinic_id': 7},
        'clinic': <String, dynamic>{
          ...clinic(),
          'operating_mode': 'scheduled',
          'is_open_now': true,
        },
      },
    };
    final profile = AuthenticatedProfile.fromEnvelope(
      envelope,
      expectedUserId: 5,
      expectedClinicId: 7,
    );
    expect(profile.clinicHoursStatus!.isOpenNow, isTrue);
    expect(
      () => AuthenticatedProfile.fromEnvelope(
        envelope,
        expectedUserId: 5,
        expectedClinicId: 99,
      ),
      throwsFormatException,
    );
  });
}
