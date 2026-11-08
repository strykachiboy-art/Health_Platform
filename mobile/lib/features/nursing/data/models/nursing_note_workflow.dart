import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';

// P23E.E: transient, clinic/patient/admission-scoped nursing note records.
enum NursingNoteKind {
  assessment,
  progress,
  intervention,
  education,
  safety,
  other,
}

enum NursingNoteLife { draft, signed }

enum NursingRecordLife { draft, finalRecord, amended }

int _id(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! int || value <= 0 || value > 2147483647) {
    throw FormatException('Invalid nursing note identifier: $key');
  }
  return value;
}

int? _optionalId(Map<String, dynamic> json, String key) =>
    json[key] == null ? null : _id(json, key);

String _text(Map<String, dynamic> json, String key, int limit) {
  final value = json[key];
  if (value is! String || value.trim().isEmpty || value.length > limit) {
    throw FormatException('Invalid nursing note text: $key');
  }
  return value;
}

DateTime _instant(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! String ||
      !RegExp(
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$',
      ).hasMatch(value)) {
    throw FormatException('Invalid nursing note timestamp: $key');
  }
  final parsed = DateTime.tryParse(value);
  if (parsed == null) {
    throw FormatException('Invalid nursing note timestamp: $key');
  }
  return parsed.toUtc();
}

DateTime? _optionalInstant(Map<String, dynamic> json, String key) =>
    json[key] == null ? null : _instant(json, key);

NursingNoteKind _kind(Object? raw) => switch (raw) {
  'assessment' => NursingNoteKind.assessment,
  'progress' => NursingNoteKind.progress,
  'intervention' => NursingNoteKind.intervention,
  'education' => NursingNoteKind.education,
  'safety' => NursingNoteKind.safety,
  'other' => NursingNoteKind.other,
  _ => throw const FormatException('Unknown nursing note type.'),
};

NursingNoteLife _life(Object? raw) => switch (raw) {
  'draft' => NursingNoteLife.draft,
  'signed' => NursingNoteLife.signed,
  _ => throw const FormatException('Unknown nursing note lifecycle status.'),
};

NursingRecordLife _recordLife(Object? raw) => switch (raw) {
  'draft' => NursingRecordLife.draft,
  'final' => NursingRecordLife.finalRecord,
  'amended' => NursingRecordLife.amended,
  _ => throw const FormatException('Unknown clinical record status.'),
};

class NursingNoteEntry {
  const NursingNoteEntry({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
    required this.authorStaffId,
    required this.kind,
    required this.status,
    required this.recordStatus,
    required this.version,
    required this.recordVersion,
    required this.text,
    required this.observedAt,
    required this.signedAt,
    required this.finalizedByStaffId,
    required this.latestAmendmentId,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int admissionId;
  final int authorStaffId;
  final NursingNoteKind kind;
  final NursingNoteLife status;
  final NursingRecordLife recordStatus;
  final int version;
  final int recordVersion;
  final String text;
  final DateTime observedAt;
  final DateTime? signedAt;
  final int? finalizedByStaffId;
  final int? latestAmendmentId;

  bool get mayEditDraft =>
      status == NursingNoteLife.draft &&
      recordStatus == NursingRecordLife.draft;

  bool get maySign => mayEditDraft;
  bool get mayAmend =>
      status == NursingNoteLife.signed &&
      (recordStatus == NursingRecordLife.finalRecord ||
          recordStatus == NursingRecordLife.amended);

  static NursingNoteEntry fromJson(
    Map<String, dynamic> json, {
    required int clinicId,
    required int patientId,
    required int admissionId,
  }) {
    if (_id(json, 'clinic_id') != clinicId ||
        _id(json, 'patient_id') != patientId ||
        _id(json, 'admission_id') != admissionId) {
      throw const FormatException('Nursing note clinical scope mismatch.');
    }
    final status = _life(json['status']);
    final recordStatus = _recordLife(json['record_status']);
    if ((status == NursingNoteLife.draft &&
            recordStatus != NursingRecordLife.draft) ||
        (status == NursingNoteLife.signed &&
            recordStatus == NursingRecordLife.draft)) {
      throw const FormatException('Inconsistent note/record state.');
    }
    return NursingNoteEntry(
      id: _id(json, 'id'),
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admissionId,
      authorStaffId: _id(json, 'author_staff_id'),
      kind: _kind(json['note_type']),
      status: status,
      recordStatus: recordStatus,
      version: _id(json, 'version'),
      recordVersion: _id(json, 'record_version'),
      text: _text(json, 'note_text', 20000),
      observedAt: _instant(json, 'observed_at'),
      signedAt: _optionalInstant(json, 'signed_at'),
      finalizedByStaffId: _optionalId(json, 'finalized_by_staff_id'),
      latestAmendmentId: _optionalId(json, 'latest_amendment_id'),
    );
  }
}

class NursingNoteReview {
  const NursingNoteReview({
    required this.admission,
    required this.clinicId,
    required this.notes,
    required this.mayWrite,
  });

  final PatientAdmission admission;
  final int clinicId;
  final List<NursingNoteEntry> notes;
  final bool mayWrite;

  NursingNoteEntry? note(int id) {
    for (final note in notes) {
      if (note.id == id) {
        return note;
      }
    }
    return null;
  }
}

List<NursingNoteEntry> nursingNoteList(
  Map<String, dynamic> json, {
  required int clinicId,
  required int patientId,
  required int admissionId,
}) {
  final rows = json['items'];
  final total = json['total'];
  if (rows is! List || total is! int || total < 0 || total != rows.length) {
    throw const FormatException('Invalid nursing note list envelope.');
  }
  final notes = <NursingNoteEntry>[];
  final ids = <int>{};
  for (final row in rows) {
    if (row is! Map<String, dynamic>) {
      throw const FormatException('Invalid nursing note list item.');
    }
    final entry = NursingNoteEntry.fromJson(
      row,
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admissionId,
    );
    if (!ids.add(entry.id)) {
      throw const FormatException('Duplicate nursing note identifier.');
    }
    notes.add(entry);
  }
  return List<NursingNoteEntry>.unmodifiable(notes);
}

/// Transient amendment metadata; the signed original remains server-owned.
class NursingNoteAmendment {
  const NursingNoteAmendment({
    required this.id,
    required this.noteId,
    required this.previousVersion,
    required this.newVersion,
    required this.reason,
    required this.amendedAt,
  });

  final int id;
  final int noteId;
  final int previousVersion;
  final int newVersion;
  final String reason;
  final DateTime amendedAt;

  factory NursingNoteAmendment.fromJson(
    Map<String, dynamic> json, {
    required int clinicId,
    required int patientId,
    required int noteId,
  }) {
    if (_id(json, 'clinic_id') != clinicId ||
        _id(json, 'patient_id') != patientId ||
        _id(json, 'source_record_id') != noteId ||
        json['source_record_type'] != 'nursing_note') {
      throw const FormatException('Amendment clinical scope mismatch.');
    }
    final oldVersion = _id(json, 'previous_version');
    final newVersion = _id(json, 'new_version');
    if (newVersion != oldVersion + 1 || newVersion < 2) {
      throw const FormatException('Invalid amendment version transition.');
    }
    return NursingNoteAmendment(
      id: _id(json, 'id'),
      noteId: noteId,
      previousVersion: oldVersion,
      newVersion: newVersion,
      reason: _text(json, 'reason', 10000),
      amendedAt: _instant(json, 'amended_at'),
    );
  }
}
