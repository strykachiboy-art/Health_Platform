import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';

/// P23E.F: in-memory, admission-bound views of server-owned clinical records.
enum HandoffLife { draft, ready, acknowledged, cancelled }

enum PrimaryClinicianKind { primaryDoctor, primaryNurse }

String primaryKindWire(PrimaryClinicianKind kind) => switch (kind) {
  PrimaryClinicianKind.primaryDoctor => 'primary_doctor',
  PrimaryClinicianKind.primaryNurse => 'primary_nurse',
};

int requiredClinicalId(Map<String, dynamic> value, String key) {
  final field = value[key];
  if (field is! int || field <= 0 || field > 2147483647) {
    throw FormatException('Invalid care-team identifier: $key');
  }
  return field;
}

int? optionalClinicalId(Map<String, dynamic> value, String key) =>
    value[key] == null ? null : requiredClinicalId(value, key);

String? optionalClinicalText(
  Map<String, dynamic> value,
  String key, {
  int limit = 10000,
}) {
  final field = value[key];
  if (field == null) {
    return null;
  }
  if (field is! String || field.length > limit) {
    throw FormatException('Invalid care-team text: $key');
  }
  return field;
}

String requiredClinicalText(Map<String, dynamic> value, String key, int limit) {
  final field = optionalClinicalText(value, key, limit: limit);
  if (field == null || field.trim().isEmpty) {
    throw FormatException('Missing care-team text: $key');
  }
  return field;
}

DateTime requiredClinicalTime(Map<String, dynamic> value, String key) {
  final field = value[key];
  if (field is! String ||
      !RegExp(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}').hasMatch(field)) {
    throw FormatException('Invalid clinical timestamp: $key');
  }
  final parsed = DateTime.tryParse(field);
  if (parsed == null) {
    throw FormatException('Invalid clinical timestamp: $key');
  }
  return parsed;
}

DateTime? optionalClinicalTime(Map<String, dynamic> value, String key) =>
    value[key] == null ? null : requiredClinicalTime(value, key);

HandoffLife _handoffLife(Object? value) => switch (value) {
  'draft' => HandoffLife.draft,
  'ready' => HandoffLife.ready,
  'acknowledged' => HandoffLife.acknowledged,
  'cancelled' => HandoffLife.cancelled,
  _ => throw const FormatException('Unknown nursing handoff status.'),
};

PrimaryClinicianKind _primaryKind(Object? value) => switch (value) {
  'primary_doctor' => PrimaryClinicianKind.primaryDoctor,
  'primary_nurse' => PrimaryClinicianKind.primaryNurse,
  _ => throw const FormatException('Unknown primary assignment kind.'),
};

class PrimaryClinicianAssignment {
  const PrimaryClinicianAssignment({
    required this.id,
    required this.kind,
    required this.staffId,
    required this.staffUserId,
    required this.displayName,
    required this.active,
    required this.assignedAt,
    required this.endedAt,
  });

  final int id;
  final PrimaryClinicianKind kind;
  final int staffId;
  final int staffUserId;
  final String displayName;
  final bool active;
  final DateTime assignedAt;
  final DateTime? endedAt;

  bool get current => endedAt == null;

  factory PrimaryClinicianAssignment.fromJson(Map<String, dynamic> json) {
    final active = json['staff_active'];
    if (active is! bool) {
      throw const FormatException('Invalid staff active status.');
    }
    return PrimaryClinicianAssignment(
      id: requiredClinicalId(json, 'assignment_id'),
      kind: _primaryKind(json['assignment_kind']),
      staffId: requiredClinicalId(json, 'staff_id'),
      staffUserId: requiredClinicalId(json, 'staff_user_id'),
      displayName: requiredClinicalText(json, 'staff_display_name', 200),
      active: active,
      assignedAt: requiredClinicalTime(json, 'assigned_at'),
      endedAt: optionalClinicalTime(json, 'ended_at'),
    );
  }
}

class AdmissionCareTeam {
  const AdmissionCareTeam({
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
    required this.admissionActive,
    required this.primaryDoctor,
    required this.primaryNurse,
    required this.confirmed,
  });

  final int clinicId;
  final int patientId;
  final int admissionId;
  final bool admissionActive;
  final PrimaryClinicianAssignment? primaryDoctor;
  final PrimaryClinicianAssignment? primaryNurse;
  final bool confirmed;

  PrimaryClinicianAssignment? assignment(PrimaryClinicianKind kind) =>
      kind == PrimaryClinicianKind.primaryDoctor ? primaryDoctor : primaryNurse;

  factory AdmissionCareTeam.fromJson(
    Map<String, dynamic> json, {
    required int clinicId,
    required int patientId,
    required int admissionId,
  }) {
    if (requiredClinicalId(json, 'clinic_id') != clinicId ||
        requiredClinicalId(json, 'patient_id') != patientId ||
        requiredClinicalId(json, 'admission_id') != admissionId ||
        json['authoritative_assignment_available'] != true ||
        json['admission_status'] != 'admitted' ||
        json['admission_active'] != true) {
      throw const FormatException('Care-team admission scope unavailable.');
    }
    final state = json['responsibility'];
    if (state is! Map<String, dynamic>) {
      throw const FormatException('Missing authoritative care-team state.');
    }
    PrimaryClinicianAssignment? parse(String name, PrimaryClinicianKind kind) {
      final value = state[name];
      if (value == null) {
        return null;
      }
      if (value is! Map<String, dynamic>) {
        throw const FormatException('Invalid primary assignment.');
      }
      final parsed = PrimaryClinicianAssignment.fromJson(value);
      if (parsed.kind != kind || !parsed.current) {
        throw const FormatException('Incorrect current assignment kind.');
      }
      return parsed;
    }

    final doctor = parse('primary_doctor', PrimaryClinicianKind.primaryDoctor);
    final nurse = parse('primary_nurse', PrimaryClinicianKind.primaryNurse);
    if (state['primary_doctor_staff_id'] != doctor?.staffId ||
        state['primary_nurse_staff_id'] != nurse?.staffId ||
        state['primary_doctor_confirmed'] != (doctor?.active ?? false) ||
        state['primary_nurse_confirmed'] != (nurse?.active ?? false) ||
        state['confirmed'] !=
            ((doctor?.active ?? false) && (nurse?.active ?? false))) {
      throw const FormatException('Inconsistent care-team responsibility.');
    }
    return AdmissionCareTeam(
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admissionId,
      admissionActive: true,
      primaryDoctor: doctor,
      primaryNurse: nurse,
      confirmed: state['confirmed'] == true,
    );
  }
}

class NursingHandoffEntry {
  const NursingHandoffEntry({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
    required this.outgoingStaffId,
    required this.incomingStaffId,
    required this.status,
    required this.version,
    required this.shiftStartAt,
    required this.shiftEndAt,
    required this.situation,
    required this.background,
    required this.assessment,
    required this.recommendation,
    required this.safetyConcerns,
    required this.pendingActions,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int admissionId;
  final int outgoingStaffId;
  final int? incomingStaffId;
  final HandoffLife status;
  final int version;
  final DateTime shiftStartAt;
  final DateTime shiftEndAt;
  final String? situation;
  final String? background;
  final String? assessment;
  final String? recommendation;
  final String? safetyConcerns;
  final String? pendingActions;

  bool get mayEdit => status == HandoffLife.draft;
  bool get mayReady =>
      status == HandoffLife.draft &&
      incomingStaffId != null &&
      (situation?.trim().isNotEmpty ?? false) &&
      (assessment?.trim().isNotEmpty ?? false) &&
      (recommendation?.trim().isNotEmpty ?? false);
  bool get mayAcknowledge => status == HandoffLife.ready;
  bool get mayCancel =>
      status == HandoffLife.draft || status == HandoffLife.ready;

  factory NursingHandoffEntry.fromJson(
    Map<String, dynamic> json, {
    required int clinicId,
    required int patientId,
    required int admissionId,
  }) {
    if (requiredClinicalId(json, 'clinic_id') != clinicId ||
        requiredClinicalId(json, 'patient_id') != patientId ||
        requiredClinicalId(json, 'admission_id') != admissionId) {
      throw const FormatException('Handoff clinical scope mismatch.');
    }
    final start = requiredClinicalTime(json, 'shift_start_at');
    final end = requiredClinicalTime(json, 'shift_end_at');
    if (!end.isAfter(start)) {
      throw const FormatException('Invalid handoff shift interval.');
    }
    final state = _handoffLife(json['status']);
    final incoming = optionalClinicalId(json, 'incoming_staff_id');
    final outgoing = requiredClinicalId(json, 'outgoing_staff_id');
    if (incoming != null && incoming == outgoing) {
      throw const FormatException('Outgoing and incoming nurse must differ.');
    }
    if ((state == HandoffLife.ready || state == HandoffLife.acknowledged) &&
        incoming == null) {
      throw const FormatException('Handoff incoming nurse missing.');
    }
    return NursingHandoffEntry(
      id: requiredClinicalId(json, 'id'),
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admissionId,
      outgoingStaffId: outgoing,
      incomingStaffId: incoming,
      status: state,
      version: requiredClinicalId(json, 'version'),
      shiftStartAt: start,
      shiftEndAt: end,
      situation: optionalClinicalText(json, 'situation'),
      background: optionalClinicalText(json, 'background'),
      assessment: optionalClinicalText(json, 'assessment'),
      recommendation: optionalClinicalText(json, 'recommendation'),
      safetyConcerns: optionalClinicalText(json, 'safety_concerns'),
      pendingActions: optionalClinicalText(json, 'pending_actions'),
    );
  }
}

List<NursingHandoffEntry> handoffList(
  Map<String, dynamic> envelope, {
  required int clinicId,
  required int patientId,
  required int admissionId,
}) {
  final rows = envelope['items'];
  final total = envelope['total'];
  if (rows is! List || total is! int || total != rows.length || total < 0) {
    throw const FormatException('Invalid handoff list envelope.');
  }
  final result = <NursingHandoffEntry>[];
  final ids = <int>{};
  for (final row in rows) {
    if (row is! Map<String, dynamic>) {
      throw const FormatException('Invalid handoff list item.');
    }
    final item = NursingHandoffEntry.fromJson(
      row,
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admissionId,
    );
    if (!ids.add(item.id)) {
      throw const FormatException('Duplicate handoff identifier.');
    }
    result.add(item);
  }
  return List<NursingHandoffEntry>.unmodifiable(result);
}

List<PrimaryClinicianAssignment> careTeamHistory(
  Map<String, dynamic> envelope,
) {
  final rows = envelope['items'];
  final total = envelope['total'];
  if (rows is! List || total is! int || total != rows.length || total < 0) {
    throw const FormatException('Invalid assignment history envelope.');
  }
  final result = <PrimaryClinicianAssignment>[];
  final ids = <int>{};
  for (final row in rows) {
    if (row is! Map<String, dynamic>) {
      throw const FormatException('Invalid assignment history item.');
    }
    final item = PrimaryClinicianAssignment.fromJson(row);
    if (!ids.add(item.id)) {
      throw const FormatException('Duplicate assignment ID.');
    }
    result.add(item);
  }
  return List<PrimaryClinicianAssignment>.unmodifiable(result);
}

class NursingHandoffCareReview {
  const NursingHandoffCareReview({
    required this.admission,
    required this.clinicId,
    required this.sessionUserId,
    required this.careTeam,
    required this.handoffs,
    required this.assignmentHistory,
    required this.mayHandoffWrite,
    required this.mayTeamWrite,
  });

  final PatientAdmission admission;
  final int clinicId;
  final int sessionUserId;
  final AdmissionCareTeam careTeam;
  final List<NursingHandoffEntry> handoffs;
  final List<PrimaryClinicianAssignment> assignmentHistory;
  final bool mayHandoffWrite;
  final bool mayTeamWrite;

  NursingHandoffEntry? handoff(int id) {
    for (final row in handoffs) {
      if (row.id == id) {
        return row;
      }
    }
    return null;
  }
}
