import 'dart:convert';

// These are fixed Flask operations, not a generic HTTP console. No URL, role,
// token, actor ID, clinic ID or tenant ID is accepted from a form.
enum SaOperation {
  rules,
  ruleDetail,
  ruleCreate,
  ruleUpdate,
  ruleDisable,
  operations,
  failures,
  staffMemberships,
  departmentMembers,
  assignMembership,
  primaryMembership,
  endMembership,
  excuses,
  excuseDetail,
  myExcuses,
  excuseCreate,
  excuseApprove,
  excuseReject,
  leaves,
  leaveDetail,
  leaveApprove,
  leaveReject,
  payroll,
  payrollDetail,
  payrollGenerate,
}

enum SaKind { text, id, integer, choice, date, timestamp, object, salaries }

class SaField {
  const SaField(
    this.key,
    this.label,
    this.kind, {
    this.required = true,
    this.choices = const [],
    this.max = 2000,
  });
  final String key;
  final String label;
  final SaKind kind;
  final bool required;
  final List<String> choices;
  final int max;
}

const _id = SaField('id', 'Record ID', SaKind.id);
const _staff = SaField('staff_id', 'Staff ID', SaKind.id);
const _department = SaField('department_id', 'Department ID', SaKind.id);
const _page = SaField(
  'page',
  'Page (default 1)',
  SaKind.id,
  required: false,
  max: 1000000,
);
const _staffFilter = SaField(
  'staff_id',
  'Filter by staff ID (optional)',
  SaKind.id,
  required: false,
);
const _status = SaField(
  'status',
  'Status filter',
  SaKind.choice,
  required: false,
  choices: ['pending', 'approved', 'rejected'],
);
const _reason = SaField(
  'reason',
  'Rejection reason (optional)',
  SaKind.text,
  required: false,
);
const _history = SaField(
  'include_ended',
  'Include ended memberships',
  SaKind.choice,
  required: false,
  choices: ['true', 'false'],
);
const _name = SaField('name', 'Rule name', SaKind.text, max: 255);
const _description = SaField(
  'description',
  'Description',
  SaKind.text,
  required: false,
  max: 5000,
);
const _severity = SaField(
  'severity',
  'Severity',
  SaKind.choice,
  choices: ['info', 'low', 'moderate', 'high', 'critical'],
);
const _action = SaField(
  'action',
  'Action',
  SaKind.choice,
  choices: [
    'inform',
    'alert',
    'require_acknowledgement',
    'require_justification',
    'block',
  ],
);
const _ruleTypes = [
  'drug_interaction',
  'allergy_conflict',
  'contraindication',
  'max_dose',
  'min_dose',
  'age_restriction',
  'weight_restriction',
  'pregnancy_restriction',
  'duplicate_therapy',
  'therapeutic_duplication',
  'lab_conflict',
  'renal_function',
  'hepatic_function',
  'diagnosis_conflict',
  'frequency_limit',
  'duration_limit',
  'patient_specific_restriction',
];
const _configuration = SaField(
  'configuration',
  'Backend rule configuration (JSON object)',
  SaKind.object,
  max: 12000,
);
const _conditions = SaField(
  'conditions',
  'Rule conditions (JSON object, optional)',
  SaKind.object,
  required: false,
  max: 12000,
);
const _priority = SaField(
  'priority',
  'Priority (default 100)',
  SaKind.integer,
  required: false,
  max: 2147483647,
);
const _enabled = SaField(
  'enabled',
  'Enabled',
  SaKind.choice,
  choices: ['false', 'true'],
);
const _from = SaField(
  'effective_from',
  'Effective from (ISO 8601 with timezone)',
  SaKind.timestamp,
  required: false,
);
const _until = SaField(
  'effective_until',
  'Effective until (ISO 8601 with timezone)',
  SaKind.timestamp,
  required: false,
);

extension SaOperationContract on SaOperation {
  String get title => switch (this) {
    SaOperation.rules => 'Clinical rules — global list',
    SaOperation.ruleDetail => 'Clinical rule — global detail',
    SaOperation.ruleCreate => 'Create global soft clinical rule',
    SaOperation.ruleUpdate => 'Update global soft clinical rule',
    SaOperation.ruleDisable => 'Disable global soft clinical rule',
    SaOperation.operations => 'Platform operational dashboard',
    SaOperation.failures => 'Platform failure dashboard',
    SaOperation.staffMemberships => 'Departments for staff member',
    SaOperation.departmentMembers => 'Staff in department',
    SaOperation.assignMembership => 'Assign staff to department',
    SaOperation.primaryMembership => 'Set primary department',
    SaOperation.endMembership => 'End department membership',
    SaOperation.excuses => 'Clinic staff excuses',
    SaOperation.excuseDetail => 'Excuse details',
    SaOperation.myExcuses => 'My excuses (linked staff only)',
    SaOperation.excuseCreate => 'Submit my excuse (linked staff only)',
    SaOperation.excuseApprove => 'Approve staff excuse',
    SaOperation.excuseReject => 'Reject staff excuse',
    SaOperation.leaves => 'Clinic leave requests',
    SaOperation.leaveDetail => 'Leave request details',
    SaOperation.leaveApprove => 'Approve staff leave',
    SaOperation.leaveReject => 'Reject staff leave',
    SaOperation.payroll => 'Clinic payroll records',
    SaOperation.payrollDetail => 'Payroll record details',
    SaOperation.payrollGenerate => 'Generate payroll batch',
  };
  String get group => index <= SaOperation.ruleDisable.index
      ? 'Clinical rule administration'
      : index <= SaOperation.failures.index
      ? 'Platform observability'
      : index <= SaOperation.endMembership.index
      ? 'Staff-department membership'
      : index <= SaOperation.excuseReject.index
      ? 'Staff excuses'
      : index <= SaOperation.leaveReject.index
      ? 'Leave review'
      : 'Payroll';
  bool get clinicRequired => index > SaOperation.failures.index;
  bool get releaseHeld => const {
    SaOperation.ruleCreate,
    SaOperation.ruleUpdate,
    SaOperation.ruleDisable,
    SaOperation.payrollGenerate,
  }.contains(this);
  bool get writes => method != 'GET';
  String get method => switch (this) {
    SaOperation.ruleUpdate => 'PATCH',
    SaOperation.endMembership => 'DELETE',
    SaOperation.ruleCreate ||
    SaOperation.ruleDisable ||
    SaOperation.assignMembership ||
    SaOperation.primaryMembership ||
    SaOperation.excuseCreate ||
    SaOperation.excuseApprove ||
    SaOperation.excuseReject ||
    SaOperation.leaveApprove ||
    SaOperation.leaveReject ||
    SaOperation.payrollGenerate => 'POST',
    _ => 'GET',
  };
  List<SaField> get fields => switch (this) {
    SaOperation.rules || SaOperation.operations => const [],
    SaOperation.failures => const [
      SaField(
        'limit',
        'Recent failure limit (default 50)',
        SaKind.id,
        required: false,
        max: 100,
      ),
    ],
    SaOperation.ruleDetail ||
    SaOperation.ruleDisable ||
    SaOperation.excuseDetail ||
    SaOperation.leaveDetail ||
    SaOperation.payrollDetail ||
    SaOperation.excuseApprove ||
    SaOperation.leaveApprove => const [_id],
    SaOperation.ruleCreate => const [
      SaField('rule_code', 'Rule code', SaKind.text, max: 100),
      _name,
      _description,
      SaField('rule_type', 'Rule type', SaKind.choice, choices: _ruleTypes),
      _severity,
      _action,
      _configuration,
      _conditions,
      _priority,
      _enabled,
      _from,
      _until,
    ],
    SaOperation.ruleUpdate => const [
      _id,
      SaField(
        'name',
        'New name (optional)',
        SaKind.text,
        required: false,
        max: 255,
      ),
      _description,
      SaField(
        'severity',
        'New severity (optional)',
        SaKind.choice,
        required: false,
        choices: ['info', 'low', 'moderate', 'high', 'critical'],
      ),
      SaField(
        'action',
        'New action (optional)',
        SaKind.choice,
        required: false,
        choices: [
          'inform',
          'alert',
          'require_acknowledgement',
          'require_justification',
          'block',
        ],
      ),
      SaField(
        'configuration',
        'New configuration (JSON object, optional)',
        SaKind.object,
        required: false,
        max: 12000,
      ),
      _conditions,
      _priority,
      SaField(
        'enabled',
        'Enabled (optional)',
        SaKind.choice,
        required: false,
        choices: ['false', 'true'],
      ),
      _from,
      _until,
    ],
    SaOperation.staffMemberships => const [_staff, _history],
    SaOperation.departmentMembers => const [_department, _history],
    SaOperation.assignMembership => const [
      _staff,
      _department,
      SaField(
        'is_primary',
        'Make primary?',
        SaKind.choice,
        choices: ['true', 'false'],
      ),
    ],
    SaOperation.primaryMembership ||
    SaOperation.endMembership => const [_staff, _department],
    SaOperation.excuses ||
    SaOperation.leaves => const [_page, _staffFilter, _status],
    SaOperation.myExcuses => const [_page],
    SaOperation.excuseCreate => const [
      SaField(
        'excuse_type',
        'Excuse type',
        SaKind.choice,
        choices: ['medical', 'family', 'emergency', 'personal', 'other'],
      ),
      SaField('description', 'Description', SaKind.text),
      SaField(
        'leave_request_id',
        'Related leave ID (optional)',
        SaKind.id,
        required: false,
      ),
      SaField(
        'document_url',
        'Supporting document reference (optional)',
        SaKind.text,
        required: false,
        max: 500,
      ),
    ],
    SaOperation.excuseReject || SaOperation.leaveReject => const [_id, _reason],
    SaOperation.payroll => const [_page, _staffFilter],
    SaOperation.payrollGenerate => const [
      SaField('pay_period_start', 'Period start YYYY-MM-DD', SaKind.date),
      SaField('pay_period_end', 'Period end YYYY-MM-DD', SaKind.date),
      SaField(
        'salary_lookup',
        'Staff IDs and exact salaries (JSON object; decimal strings)',
        SaKind.salaries,
        max: 12000,
      ),
    ],
  };

  bool allowed(int? clinic, bool staging) =>
      (!clinicRequired || (clinic != null && clinic > 0)) &&
      (!releaseHeld || staging);

  SaRequest prepare(Map<String, String> raw) {
    if (raw.keys.any((key) => !fields.any((f) => f.key == key))) {
      throw const FormatException('Unrecognized operation field.');
    }
    final values = <String, dynamic>{};
    for (final field in fields) {
      final value = (raw[field.key] ?? '').trim();
      if (value.isEmpty) {
        if (field.required) {
          throw FormatException('${field.label} is required.');
        }
        continue;
      }
      if (value.length >
          (field.kind == SaKind.id || field.kind == SaKind.integer
              ? 10
              : field.max)) {
        throw FormatException('${field.label} is too long.');
      }
      switch (field.kind) {
        case SaKind.id:
        case SaKind.integer:
          final number = int.tryParse(value);
          final max = field.max == 2000 ? 2147483647 : field.max;
          if (number == null ||
              number < (field.kind == SaKind.id ? 1 : 0) ||
              number > max ||
              !RegExp(r'^\d+$').hasMatch(value)) {
            throw FormatException('Invalid ${field.label}.');
          }
          values[field.key] = number;
        case SaKind.choice:
          if (!field.choices.contains(value)) {
            throw FormatException('Invalid ${field.label}.');
          }
          values[field.key] =
              const {
                'enabled',
                'is_primary',
                'include_ended',
              }.contains(field.key)
              ? value == 'true'
              : value;
        case SaKind.date:
          if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(value)) {
            throw const FormatException('Use YYYY-MM-DD.');
          }
          final date = DateTime.tryParse(value);
          if (date == null || date.toIso8601String().substring(0, 10) != value) {
            throw const FormatException('Invalid calendar date.');
          }
          values[field.key] = value;
        case SaKind.timestamp:
          if (!RegExp(
                r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$',
              ).hasMatch(value) ||
              DateTime.tryParse(value) == null) {
            throw const FormatException(
              'A timezone-aware timestamp is required.',
            );
          }
          values[field.key] = value;
        case SaKind.object:
          final parsed = jsonDecode(value);
          if (parsed is! Map<String, dynamic>) {
            throw const FormatException('A JSON object is required.');
          }
          values[field.key] = parsed;
        case SaKind.salaries:
          final parsed = jsonDecode(value);
          if (parsed is! Map<String, dynamic> ||
              parsed.isEmpty ||
              parsed.length > 100) {
            throw const FormatException('Enter 1–100 staff salary entries.');
          }
          for (final entry in parsed.entries) {
            final id = int.tryParse(entry.key);
            final salary = entry.value;
            if (id == null ||
                id <= 0 ||
                id > 2147483647 ||
                id.toString() != entry.key ||
                salary is! String ||
                !RegExp(r'^\d{1,12}(?:\.\d{1,2})?$').hasMatch(salary)) {
              throw const FormatException(
                'Salary entries require positive staff IDs and decimal strings.',
              );
            }
          }
          values[field.key] = parsed;
        case SaKind.text:
          values[field.key] = value;
      }
    }
    if (this == SaOperation.ruleCreate) {
      final code = (values['rule_code'] as String).toUpperCase();
      if (!RegExp(r'^[A-Z][A-Z0-9_]{2,99}$').hasMatch(code)) {
        throw const FormatException('Invalid rule code.');
      }
      values['rule_code'] = code;
      values['scope'] = 'global';
    }
    final id = values['id'];
    final staff = values['staff_id'];
    final department = values['department_id'];
    final path = switch (this) {
      SaOperation.rules || SaOperation.ruleCreate => 'clinical-safety/rules',
      SaOperation.ruleDetail ||
      SaOperation.ruleUpdate => 'clinical-safety/rules/$id',
      SaOperation.ruleDisable => 'clinical-safety/rules/$id/disable',
      SaOperation.operations => 'operations',
      SaOperation.failures => 'operations/failures',
      SaOperation.staffMemberships => 'staff-departments/staff/$staff',
      SaOperation.departmentMembers =>
        'staff-departments/department/$department',
      SaOperation.assignMembership || SaOperation.endMembership =>
        'staff-departments/staff/$staff/department/$department',
      SaOperation.primaryMembership =>
        'staff-departments/staff/$staff/department/$department/primary',
      SaOperation.excuses || SaOperation.excuseCreate => 'staff/excuses',
      SaOperation.myExcuses => 'staff/excuses/me',
      SaOperation.excuseDetail => 'staff/excuses/$id',
      SaOperation.excuseApprove => 'staff/excuses/$id/approve',
      SaOperation.excuseReject => 'staff/excuses/$id/reject',
      SaOperation.leaves => 'staff/leave',
      SaOperation.leaveDetail => 'staff/leave/$id',
      SaOperation.leaveApprove => 'staff/leave/$id/approve',
      SaOperation.leaveReject => 'staff/leave/$id/reject',
      SaOperation.payroll => 'staff/payroll',
      SaOperation.payrollDetail => 'staff/payroll/$id',
      SaOperation.payrollGenerate => 'staff/payroll/generate',
    };
    final body = Map<String, dynamic>.of(values)..remove('id');
    if (this == SaOperation.ruleUpdate && body.isEmpty) {
      throw const FormatException('Enter at least one change.');
    }
    if (this == SaOperation.payrollGenerate &&
        (body['pay_period_end'] as String).compareTo(
              body['pay_period_start'] as String,
            ) <
            0) {
      throw const FormatException('End precedes start.');
    }
    if (body['effective_from'] is String &&
        body['effective_until'] is String &&
        !DateTime.parse(body['effective_until'] as String)
            .isAfter(DateTime.parse(body['effective_from'] as String))) {
      throw const FormatException('Effective end must follow start.');
    }
    final query = <String, String>{};
    if (!writes) {
      if (this == SaOperation.rules) query['scope'] = 'global';
      for (final entry in body.entries) {
        if (this == SaOperation.staffMemberships && entry.key == 'staff_id' ||
            this == SaOperation.departmentMembers &&
                entry.key == 'department_id') {
          continue;
        }
        query[entry.key] = entry.value.toString();
      }
      body.clear();
    }
    if (this == SaOperation.assignMembership) body.remove('staff_id');
    if (this == SaOperation.primaryMembership ||
        this == SaOperation.endMembership) {
      body.clear();
    }
    final suffix = query.isEmpty ? '' : '?${Uri(queryParameters: query).query}';
    return SaRequest._(
      this,
      'api/v1/$path$suffix',
      _freezeMap(values),
      _freezeMap(body),
    );
  }
}

class SaRequest {
  const SaRequest._(this.operation, this.path, this.values, this.body);
  final SaOperation operation;
  final String path;
  final Map<String, dynamic> values;
  final Map<String, dynamic> body;
}

Map<String, dynamic> _freezeMap(Map<String, dynamic> value) =>
    Map<String, dynamic>.unmodifiable({
      for (final entry in value.entries) entry.key: _freeze(entry.value),
    });
Object? _freeze(Object? value) {
  if (value is Map<String, dynamic>) return _freezeMap(value);
  if (value is List) return List<Object?>.unmodifiable(value.map(_freeze));
  return value;
}
