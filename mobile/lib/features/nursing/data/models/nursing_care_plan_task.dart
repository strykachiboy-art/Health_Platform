import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';

// P23E.D: transient clinic, patient, and admission-scoped nursing models.
enum NursingPlanStatus { active, onHold, completed, cancelled }

enum NursingTaskStatus { pending, inProgress, completed, cancelled }

enum NursingTaskPriority { routine, high, urgent }

enum NursingPlanAction { hold, resume, complete, cancel }

enum NursingTaskAction { start, complete, cancel }

int nursingId(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! int || value <= 0 || value > 2147483647) {
    throw FormatException('Invalid nursing identifier: $key');
  }
  return value;
}

int _version(Map<String, dynamic> json) => nursingId(json, 'version');

String _text(Map<String, dynamic> json, String key, int max) {
  final value = json[key];
  if (value is! String || value.trim().isEmpty || value.length > max) {
    throw FormatException('Invalid nursing text: $key');
  }
  return value;
}

String? _optionalText(Map<String, dynamic> json, String key, int max) {
  if (json[key] == null) {
    return null;
  }
  final value = json[key];
  if (value is! String || value.length > max) {
    throw FormatException('Invalid nursing text: $key');
  }
  return value;
}

int? _optionalId(Map<String, dynamic> json, String key) =>
    json[key] == null ? null : nursingId(json, key);

DateTime? _timestamp(
  Map<String, dynamic> json,
  String key, {
  bool mustExist = false,
}) {
  final value = json[key];
  if (value == null && !mustExist) {
    return null;
  }
  if (value is! String ||
      !RegExp(
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$',
      ).hasMatch(value)) {
    throw FormatException('Invalid nursing timestamp: $key');
  }
  final parsed = DateTime.tryParse(value);
  if (parsed == null) {
    throw FormatException('Invalid nursing timestamp: $key');
  }
  return parsed.toUtc();
}

NursingPlanStatus _planStatus(Object? value) => switch (value) {
  'active' => NursingPlanStatus.active,
  'on_hold' => NursingPlanStatus.onHold,
  'completed' => NursingPlanStatus.completed,
  'cancelled' => NursingPlanStatus.cancelled,
  _ => throw const FormatException('Unknown nursing care-plan status.'),
};

NursingTaskStatus _taskStatus(Object? value) => switch (value) {
  'pending' => NursingTaskStatus.pending,
  'in_progress' => NursingTaskStatus.inProgress,
  'completed' => NursingTaskStatus.completed,
  'cancelled' => NursingTaskStatus.cancelled,
  _ => throw const FormatException('Unknown nursing task status.'),
};

NursingTaskPriority _priority(Object? value) => switch (value) {
  'routine' => NursingTaskPriority.routine,
  'high' => NursingTaskPriority.high,
  'urgent' => NursingTaskPriority.urgent,
  _ => throw const FormatException('Unknown nursing task priority.'),
};

class NursingCarePlan {
  const NursingCarePlan({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
    required this.createdByStaffId,
    required this.title,
    required this.problem,
    required this.goal,
    required this.status,
    required this.version,
    required this.startedAt,
    required this.targetAt,
    required this.completedAt,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int admissionId;
  final int createdByStaffId;
  final String title;
  final String problem;
  final String goal;
  final NursingPlanStatus status;
  final int version;
  final DateTime startedAt;
  final DateTime? targetAt;
  final DateTime? completedAt;

  bool allows(NursingPlanAction action) => switch (action) {
    NursingPlanAction.hold => status == NursingPlanStatus.active,
    NursingPlanAction.resume => status == NursingPlanStatus.onHold,
    NursingPlanAction.complete => status == NursingPlanStatus.active,
    NursingPlanAction.cancel =>
      status == NursingPlanStatus.active || status == NursingPlanStatus.onHold,
  };

  factory NursingCarePlan.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    required int expectedPatientId,
    required int expectedAdmissionId,
  }) {
    final clinicId = nursingId(json, 'clinic_id');
    final patientId = nursingId(json, 'patient_id');
    final admissionId = nursingId(json, 'admission_id');
    if (clinicId != expectedClinicId ||
        patientId != expectedPatientId ||
        admissionId != expectedAdmissionId) {
      throw const FormatException('Care plan clinical context mismatch.');
    }
    return NursingCarePlan(
      id: nursingId(json, 'id'),
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admissionId,
      createdByStaffId: nursingId(json, 'created_by_staff_id'),
      title: _text(json, 'title', 200),
      problem: _text(json, 'problem', 10000),
      goal: _text(json, 'goal', 10000),
      status: _planStatus(json['status']),
      version: _version(json),
      startedAt: _timestamp(json, 'started_at', mustExist: true)!,
      targetAt: _timestamp(json, 'target_at'),
      completedAt: _timestamp(json, 'completed_at'),
    );
  }
}

class NursingCareTask {
  const NursingCareTask({
    required this.id,
    required this.carePlanId,
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
    required this.createdByStaffId,
    required this.assignedToStaffId,
    required this.description,
    required this.instructions,
    required this.priority,
    required this.status,
    required this.version,
    required this.dueAt,
  });

  final int id;
  final int carePlanId;
  final int clinicId;
  final int patientId;
  final int admissionId;
  final int createdByStaffId;
  final int? assignedToStaffId;
  final String description;
  final String? instructions;
  final NursingTaskPriority priority;
  final NursingTaskStatus status;
  final int version;
  final DateTime? dueAt;

  bool allows(NursingTaskAction action) => switch (action) {
    NursingTaskAction.start => status == NursingTaskStatus.pending,
    NursingTaskAction.complete => status == NursingTaskStatus.inProgress,
    NursingTaskAction.cancel =>
      status == NursingTaskStatus.pending ||
          status == NursingTaskStatus.inProgress,
  };

  factory NursingCareTask.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    required int expectedPatientId,
    required int expectedAdmissionId,
  }) {
    final clinicId = nursingId(json, 'clinic_id');
    final patientId = nursingId(json, 'patient_id');
    final admissionId = nursingId(json, 'admission_id');
    if (clinicId != expectedClinicId ||
        patientId != expectedPatientId ||
        admissionId != expectedAdmissionId) {
      throw const FormatException('Care task clinical context mismatch.');
    }
    return NursingCareTask(
      id: nursingId(json, 'id'),
      carePlanId: nursingId(json, 'care_plan_id'),
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admissionId,
      createdByStaffId: nursingId(json, 'created_by_staff_id'),
      assignedToStaffId: _optionalId(json, 'assigned_to_staff_id'),
      description: _text(json, 'description', 10000),
      instructions: _optionalText(json, 'instructions', 10000),
      priority: _priority(json['priority']),
      status: _taskStatus(json['status']),
      version: _version(json),
      dueAt: _timestamp(json, 'due_at'),
    );
  }
}

class NursingCareList<T> {
  const NursingCareList(this.items, this.total);
  final List<T> items;
  final int total;

  factory NursingCareList.parse(
    Map<String, dynamic> json,
    T Function(Map<String, dynamic>) parseRow,
  ) {
    final rows = json['items'];
    final count = json['total'];
    if (rows is! List || count is! int || count < 0 || rows.length != count) {
      throw const FormatException('Nursing list envelope invalid.');
    }
    final parsed = <T>[];
    final seen = <int>{};
    for (final row in rows) {
      if (row is! Map<String, dynamic>) {
        throw const FormatException('Nursing list row invalid.');
      }
      final id = nursingId(row, 'id');
      if (!seen.add(id)) {
        throw const FormatException('Duplicate nursing row.');
      }
      parsed.add(parseRow(row));
    }
    return NursingCareList<T>(List<T>.unmodifiable(parsed), count);
  }
}

class NursingCareSnapshot {
  const NursingCareSnapshot({
    required this.admission,
    required this.clinicId,
    required this.plans,
    required this.tasks,
    required this.mayWrite,
  });
  final PatientAdmission admission;
  final int clinicId;
  final List<NursingCarePlan> plans;
  final List<NursingCareTask> tasks;
  final bool mayWrite;

  NursingCarePlan? plan(int id) {
    for (final item in plans) {
      if (item.id == id) {
        return item;
      }
    }
    return null;
  }

  NursingCareTask? task(int id) {
    for (final item in tasks) {
      if (item.id == id) {
        return item;
      }
    }
    return null;
  }
}
