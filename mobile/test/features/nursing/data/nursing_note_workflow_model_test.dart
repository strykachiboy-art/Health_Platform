import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/features/nursing/data/models/nursing_note_workflow.dart';

Map<String, dynamic> note({
  int clinic = 3,
  int patient = 7,
  int admission = 11,
  int id = 21,
  String status = 'draft',
  String recordStatus = 'draft',
  String kind = 'progress',
  int version = 1,
  int recordVersion = 1,
  String text = 'Progress documented',
}) => {
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'admission_id': admission,
  'author_staff_id': 8,
  'note_type': kind,
  'status': status,
  'record_status': recordStatus,
  'record_version': recordVersion,
  'version': version,
  'note_text': text,
  'observed_at': '2026-11-08T10:00:00+00:00',
  'signed_at': status == 'signed' ? '2026-11-08T10:30:00+00:00' : null,
};

NursingNoteEntry parse(Map<String, dynamic> value) => NursingNoteEntry.fromJson(
  value,
  clinicId: 3,
  patientId: 7,
  admissionId: 11,
);

void main() {
  test('draft note parses and permits draft edit/sign only', () {
    final result = parse(note());
    expect(result.id, 21);
    expect(result.kind, NursingNoteKind.progress);
    expect(result.status, NursingNoteLife.draft);
    expect(result.recordStatus, NursingRecordLife.draft);
    expect(result.mayEditDraft, isTrue);
    expect(result.maySign, isTrue);
    expect(result.mayAmend, isFalse);
  });

  test('signed and amended notes cannot be draft-edited', () {
    final signed = parse(note(status: 'signed', recordStatus: 'final'));
    final amended = parse(
      note(status: 'signed', recordStatus: 'amended', recordVersion: 2),
    );
    for (final value in [signed, amended]) {
      expect(value.mayEditDraft, isFalse);
      expect(value.maySign, isFalse);
      expect(value.mayAmend, isTrue);
    }
    expect(amended.recordVersion, 2);
  });

  test('all six nursing note kinds parse strictly', () {
    for (final kind in NursingNoteKind.values) {
      expect(parse(note(kind: kind.name)).kind, kind);
    }
    expect(() => parse(note(kind: 'unknown')), throwsFormatException);
  });

  test('cross-clinic, cross-patient and cross-admission rejected', () {
    expect(() => parse(note(clinic: 4)), throwsFormatException);
    expect(() => parse(note(patient: 8)), throwsFormatException);
    expect(() => parse(note(admission: 12)), throwsFormatException);
  });

  test('invalid timestamps, record states and empty text fail closed', () {
    expect(() => parse(note(text: '  ')), throwsFormatException);
    expect(
      () => parse(note(status: 'signed', recordStatus: 'draft')),
      throwsFormatException,
    );
    expect(
      () => parse(note(status: 'draft', recordStatus: 'final')),
      throwsFormatException,
    );
    final malformed = note()..['observed_at'] = '2026-11-08T10:00:00';
    expect(() => parse(malformed), throwsFormatException);
  });

  test('amendment history validates note scope and version increment', () {
    final row = <String, dynamic>{
      'id': 55,
      'clinic_id': 3,
      'patient_id': 7,
      'source_record_type': 'nursing_note',
      'source_record_id': 21,
      'previous_version': 1,
      'new_version': 2,
      'reason': 'Corrected observation',
      'amended_at': '2026-11-08T11:00:00+00:00',
    };
    final result = NursingNoteAmendment.fromJson(
      row,
      clinicId: 3,
      patientId: 7,
      noteId: 21,
    );
    expect(result.newVersion, 2);
    expect(result.reason, 'Corrected observation');
    expect(
      () => NursingNoteAmendment.fromJson(
        {...row, 'clinic_id': 4},
        clinicId: 3,
        patientId: 7,
        noteId: 21,
      ),
      throwsFormatException,
    );
  });

  test('note-list envelope must match count and reject duplicate IDs', () {
    final list = nursingNoteList(
      {
        'items': [note(id: 21), note(id: 22)],
        'total': 2,
      },
      clinicId: 3,
      patientId: 7,
      admissionId: 11,
    );
    expect(list.map((n) => n.id), [21, 22]);
    expect(
      () => nursingNoteList(
        {
          'items': [note()],
          'total': 2,
        },
        clinicId: 3,
        patientId: 7,
        admissionId: 11,
      ),
      throwsFormatException,
    );
    expect(
      () => nursingNoteList(
        {
          'items': [note(), note()],
          'total': 2,
        },
        clinicId: 3,
        patientId: 7,
        admissionId: 11,
      ),
      throwsFormatException,
    );
  });
}
