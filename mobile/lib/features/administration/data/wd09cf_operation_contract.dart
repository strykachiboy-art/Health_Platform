/// Source-pinned WD09C-F Flask request contracts. A field whitelist is safer
/// than a free-form JSON or endpoint editor: clinic/tenant IDs never leave here.
enum Wd09cfAction {
  staffCreate,
  staffUpdate,
  staffStatus,
  departmentCreate,
  departmentUpdate,
  departmentStatus,
  inventoryCreate,
  inventoryUpdate,
  assetCreate,
  assetUpdate,
  assetRetire,
  assetDispose,
  assetAssign,
  assetReturn,
  settingsUpdate,
  reportGenerate,
  payrollCreate,
  payrollMarkPaid,
  leaveRequest,
}

enum Wd09cfInputKind { text, positiveId, nonNegativeInt, decimal, date, choice }

class Wd09cfField {
  const Wd09cfField(
    this.key,
    this.label, {
    this.required = false,
    this.kind = Wd09cfInputKind.text,
    this.maxLength = 255,
    this.values = const <String>[],
  });
  final String key;
  final String label;
  final bool required;
  final Wd09cfInputKind kind;
  final int maxLength;
  final List<String> values;
}

const wd09cfStaffRoles = <String>{
  'super_admin',
  'admin',
  'doctor',
  'nurse',
  'pharmacist',
  'lab_technician',
  'receptionist',
  'accountant',
  'paramedic',
  'emt',
  'driver',
  'ambulance_dispatcher',
  'ambulance_coordinator',
  'other',
};
const wd09cfReportRoles = <String>{
  'super_admin',
  'admin',
  'doctor',
  'nurse',
  'receptionist',
  'accountant',
  'pharmacist',
  'lab_technician',
};
const wd09cfAssetRoles = <String>{'admin', 'accountant'};
const wd09cfInventoryRoles = <String>{'admin', 'pharmacist'};
const wd09cfManagerRoles = <String>{'admin', 'super_admin'};
const wd09cfPayrollRoles = <String>{'admin', 'super_admin', 'accountant'};

const wd09cfStaffStatus = <String>[
  'active',
  'on_leave',
  'suspended',
  'terminated',
];
const wd09cfDepartmentStatus = <String>['active', 'inactive', 'suspended'];
const wd09cfAssetCategory = <String>[
  'medical_equipment',
  'medical_device',
  'it_equipment',
  'computer',
  'network_equipment',
  'furniture',
  'vehicle',
  'operational_equipment',
  'facility_equipment',
  'other',
];
const wd09cfInventoryCategory = <String>[
  'medical_supply',
  'equipment',
  'consumable',
  'office_supply',
  'other',
];
const wd09cfReportType = <String>[
  'overview',
  'patients',
  'staff',
  'appointments',
  'billing',
  'inventory',
  'lab',
  'pharmacy',
  'ward',
];
const wd09cfLeaveType = <String>[
  'annual',
  'sick',
  'maternity',
  'paternity',
  'compassionate',
  'study',
  'unpaid',
  'emergency',
  'other',
];

extension Wd09cfActionDetails on Wd09cfAction {
  String get title => switch (this) {
    Wd09cfAction.staffCreate => 'Create staff profile',
    Wd09cfAction.staffUpdate => 'Update staff names',
    Wd09cfAction.staffStatus => 'Change staff status',
    Wd09cfAction.departmentCreate => 'Create department',
    Wd09cfAction.departmentUpdate => 'Update department',
    Wd09cfAction.departmentStatus => 'Change department status',
    Wd09cfAction.inventoryCreate => 'Create inventory item',
    Wd09cfAction.inventoryUpdate => 'Update inventory item',
    Wd09cfAction.assetCreate => 'Register asset',
    Wd09cfAction.assetUpdate => 'Update asset details',
    Wd09cfAction.assetRetire => 'Retire asset',
    Wd09cfAction.assetDispose => 'Dispose asset',
    Wd09cfAction.assetAssign => 'Assign asset to staff',
    Wd09cfAction.assetReturn => 'Return assigned asset',
    Wd09cfAction.settingsUpdate => 'Change clinic display settings',
    Wd09cfAction.reportGenerate => 'Generate report',
    Wd09cfAction.payrollCreate => 'Create payroll record',
    Wd09cfAction.payrollMarkPaid => 'Mark payroll as paid',
    Wd09cfAction.leaveRequest => 'Request my staff leave',
  };

  Set<String> get roles => switch (this) {
    Wd09cfAction.staffCreate ||
    Wd09cfAction.staffUpdate ||
    Wd09cfAction.staffStatus ||
    Wd09cfAction.departmentCreate ||
    Wd09cfAction.departmentUpdate ||
    Wd09cfAction.departmentStatus => wd09cfManagerRoles,
    Wd09cfAction.inventoryCreate ||
    Wd09cfAction.inventoryUpdate => wd09cfInventoryRoles,
    Wd09cfAction.assetCreate ||
    Wd09cfAction.assetUpdate ||
    Wd09cfAction.assetRetire ||
    Wd09cfAction.assetDispose ||
    Wd09cfAction.assetAssign ||
    Wd09cfAction.assetReturn => wd09cfAssetRoles,
    Wd09cfAction.settingsUpdate => const <String>{'admin'},
    Wd09cfAction.reportGenerate => wd09cfReportRoles,
    Wd09cfAction.payrollCreate ||
    Wd09cfAction.payrollMarkPaid => wd09cfPayrollRoles,
    Wd09cfAction.leaveRequest => wd09cfStaffRoles,
  };

  bool allowedFor(String role, int? clinic) =>
      clinic != null && clinic > 0 && roles.contains(role.trim().toLowerCase());

  bool get needsRecordId => switch (this) {
    Wd09cfAction.staffUpdate ||
    Wd09cfAction.staffStatus ||
    Wd09cfAction.departmentUpdate ||
    Wd09cfAction.departmentStatus ||
    Wd09cfAction.inventoryUpdate ||
    Wd09cfAction.assetUpdate ||
    Wd09cfAction.assetRetire ||
    Wd09cfAction.assetDispose ||
    Wd09cfAction.assetAssign ||
    Wd09cfAction.assetReturn ||
    Wd09cfAction.payrollMarkPaid => true,
    _ => false,
  };

  String get verb => switch (this) {
    Wd09cfAction.staffUpdate ||
    Wd09cfAction.staffStatus ||
    Wd09cfAction.departmentUpdate ||
    Wd09cfAction.departmentStatus ||
    Wd09cfAction.inventoryUpdate ||
    Wd09cfAction.assetUpdate ||
    Wd09cfAction.settingsUpdate => 'PATCH',
    _ => 'POST',
  };

  String path(int? id) {
    if (needsRecordId && (id == null || id <= 0 || id > 2147483647)) {
      throw ArgumentError('A positive record ID is required.');
    }
    return switch (this) {
      Wd09cfAction.staffCreate => 'api/v1/staff',
      Wd09cfAction.staffUpdate => 'api/v1/staff/$id',
      Wd09cfAction.staffStatus => 'api/v1/staff/$id/status',
      Wd09cfAction.departmentCreate => 'api/v1/departments',
      Wd09cfAction.departmentUpdate => 'api/v1/departments/$id',
      Wd09cfAction.departmentStatus => 'api/v1/departments/$id/status',
      Wd09cfAction.inventoryCreate => 'api/v1/inventory/items',
      Wd09cfAction.inventoryUpdate => 'api/v1/inventory/items/$id',
      Wd09cfAction.assetCreate => 'api/v1/assets',
      Wd09cfAction.assetUpdate => 'api/v1/assets/$id',
      Wd09cfAction.assetRetire => 'api/v1/assets/$id/retire',
      Wd09cfAction.assetDispose => 'api/v1/assets/$id/dispose',
      Wd09cfAction.assetAssign => 'api/v1/assets/$id/assign',
      Wd09cfAction.assetReturn => 'api/v1/assets/$id/return',
      Wd09cfAction.settingsUpdate => 'api/v1/settings/clinic',
      Wd09cfAction.reportGenerate => 'api/v1/reports',
      Wd09cfAction.payrollCreate => 'api/v1/staff/payroll',
      Wd09cfAction.payrollMarkPaid => 'api/v1/staff/payroll/$id/pay',
      Wd09cfAction.leaveRequest => 'api/v1/staff/leave',
    };
  }

  String? get recordReadPrefix => switch (this) {
    Wd09cfAction.staffUpdate || Wd09cfAction.staffStatus => 'api/v1/staff',
    Wd09cfAction.departmentUpdate ||
    Wd09cfAction.departmentStatus => 'api/v1/departments',
    Wd09cfAction.inventoryUpdate => 'api/v1/inventory/items',
    Wd09cfAction.assetUpdate ||
    Wd09cfAction.assetRetire ||
    Wd09cfAction.assetDispose ||
    Wd09cfAction.assetAssign ||
    Wd09cfAction.assetReturn => 'api/v1/assets',
    Wd09cfAction.payrollMarkPaid => 'api/v1/staff/payroll',
    _ => null,
  };

  List<Wd09cfField> get fields => switch (this) {
    Wd09cfAction.staffCreate => const <Wd09cfField>[
      Wd09cfField('first_name', 'First name', required: true, maxLength: 80),
      Wd09cfField('last_name', 'Last name', required: true, maxLength: 80),
      Wd09cfField(
        'user_id',
        'Linked user ID (optional)',
        kind: Wd09cfInputKind.positiveId,
      ),
      Wd09cfField('specialty', 'Specialty', maxLength: 100),
      Wd09cfField('phone', 'Phone', maxLength: 30),
      Wd09cfField('email', 'Email', maxLength: 120),
    ],
    Wd09cfAction.staffUpdate => const <Wd09cfField>[
      Wd09cfField(
        'first_name',
        'New first name',
        required: true,
        maxLength: 80,
      ),
      Wd09cfField('last_name', 'New last name', required: true, maxLength: 80),
    ],
    Wd09cfAction.staffStatus => const <Wd09cfField>[
      Wd09cfField(
        'status',
        'Staff status',
        required: true,
        kind: Wd09cfInputKind.choice,
        values: wd09cfStaffStatus,
      ),
    ],
    Wd09cfAction.departmentCreate => const <Wd09cfField>[
      Wd09cfField('code', 'Department code', required: true, maxLength: 100),
      Wd09cfField('name', 'Department name', required: true, maxLength: 150),
      Wd09cfField('description', 'Description', maxLength: 5000),
    ],
    Wd09cfAction.departmentUpdate => const <Wd09cfField>[
      Wd09cfField('name', 'Department name', required: true, maxLength: 150),
    ],
    Wd09cfAction.departmentStatus => const <Wd09cfField>[
      Wd09cfField(
        'status',
        'Department status',
        required: true,
        kind: Wd09cfInputKind.choice,
        values: wd09cfDepartmentStatus,
      ),
    ],
    Wd09cfAction.inventoryCreate => const <Wd09cfField>[
      Wd09cfField(
        'name',
        'Inventory item name',
        required: true,
        maxLength: 150,
      ),
      Wd09cfField(
        'category',
        'Category',
        required: true,
        kind: Wd09cfInputKind.choice,
        values: wd09cfInventoryCategory,
      ),
      Wd09cfField('sku', 'SKU', maxLength: 80),
      Wd09cfField('unit', 'Unit', maxLength: 30),
      Wd09cfField(
        'initial_quantity',
        'Initial quantity',
        required: true,
        kind: Wd09cfInputKind.nonNegativeInt,
      ),
      Wd09cfField(
        'reorder_level',
        'Reorder level',
        required: true,
        kind: Wd09cfInputKind.nonNegativeInt,
      ),
    ],
    Wd09cfAction.inventoryUpdate => const <Wd09cfField>[
      Wd09cfField(
        'name',
        'New inventory item name',
        required: true,
        maxLength: 150,
      ),
    ],
    Wd09cfAction.assetCreate => const <Wd09cfField>[
      Wd09cfField('asset_tag', 'Asset tag', required: true, maxLength: 100),
      Wd09cfField('name', 'Asset name', required: true, maxLength: 255),
      Wd09cfField(
        'category',
        'Category',
        required: true,
        kind: Wd09cfInputKind.choice,
        values: wd09cfAssetCategory,
      ),
      Wd09cfField('location', 'Location', maxLength: 255),
    ],
    Wd09cfAction.assetUpdate => const <Wd09cfField>[
      Wd09cfField('name', 'New asset name', required: true, maxLength: 255),
      Wd09cfField('location', 'New location (optional)', maxLength: 255),
    ],
    Wd09cfAction.assetRetire => const <Wd09cfField>[],
    Wd09cfAction.assetDispose => const <Wd09cfField>[
      Wd09cfField(
        'disposal_reason',
        'Reason for disposal',
        required: true,
        maxLength: 500,
      ),
    ],
    Wd09cfAction.assetAssign => const <Wd09cfField>[
      Wd09cfField(
        'staff_id',
        'Staff ID',
        required: true,
        kind: Wd09cfInputKind.positiveId,
      ),
      Wd09cfField('notes', 'Assignment note', maxLength: 5000),
    ],
    Wd09cfAction.assetReturn => const <Wd09cfField>[
      Wd09cfField('notes', 'Return note', maxLength: 5000),
    ],
    Wd09cfAction.settingsUpdate => const <Wd09cfField>[
      Wd09cfField(
        'expected_version',
        'Current settings version',
        required: true,
        kind: Wd09cfInputKind.positiveId,
      ),
      Wd09cfField(
        'language',
        'Language code (e.g. en)',
        required: true,
        maxLength: 20,
      ),
      Wd09cfField(
        'date_format',
        'Date format',
        required: true,
        kind: Wd09cfInputKind.choice,
        values: <String>[
          'YYYY-MM-DD',
          'DD-MM-YYYY',
          'MM-DD-YYYY',
          'DD/MM/YYYY',
          'MM/DD/YYYY',
        ],
      ),
      Wd09cfField(
        'time_format',
        'Time format',
        required: true,
        kind: Wd09cfInputKind.choice,
        values: <String>['12h', '24h'],
      ),
    ],
    Wd09cfAction.reportGenerate => const <Wd09cfField>[
      Wd09cfField(
        'report_type',
        'Report type',
        required: true,
        kind: Wd09cfInputKind.choice,
        values: wd09cfReportType,
      ),
      Wd09cfField(
        'report_format',
        'Output format',
        required: true,
        kind: Wd09cfInputKind.choice,
        values: <String>['pdf', 'csv', 'xlsx'],
      ),
    ],
    Wd09cfAction.payrollCreate => const <Wd09cfField>[
      Wd09cfField(
        'staff_id',
        'Staff ID',
        required: true,
        kind: Wd09cfInputKind.positiveId,
      ),
      Wd09cfField(
        'pay_period_start',
        'Period start YYYY-MM-DD',
        required: true,
        kind: Wd09cfInputKind.date,
      ),
      Wd09cfField(
        'pay_period_end',
        'Period end YYYY-MM-DD',
        required: true,
        kind: Wd09cfInputKind.date,
      ),
      Wd09cfField(
        'base_salary',
        'Base salary',
        required: true,
        kind: Wd09cfInputKind.decimal,
      ),
      Wd09cfField('bonuses', 'Bonuses', kind: Wd09cfInputKind.decimal),
      Wd09cfField('deductions', 'Deductions', kind: Wd09cfInputKind.decimal),
    ],
    Wd09cfAction.payrollMarkPaid => const <Wd09cfField>[],
    Wd09cfAction.leaveRequest => const <Wd09cfField>[
      Wd09cfField(
        'leave_type',
        'Leave type',
        required: true,
        kind: Wd09cfInputKind.choice,
        values: wd09cfLeaveType,
      ),
      Wd09cfField(
        'start_date',
        'Start YYYY-MM-DD',
        required: true,
        kind: Wd09cfInputKind.date,
      ),
      Wd09cfField(
        'end_date',
        'End YYYY-MM-DD',
        required: true,
        kind: Wd09cfInputKind.date,
      ),
      Wd09cfField('reason', 'Reason', maxLength: 2000),
    ],
  };

  Map<String, dynamic> parseBody(Map<String, String> raw) {
    for (final key in raw.keys) {
      if (!fields.any((field) => field.key == key)) {
        throw const FormatException('Unrecognized operation field.');
      }
    }
    final result = <String, dynamic>{};
    for (final field in fields) {
      final input = (raw[field.key] ?? '').trim();
      if (input.isEmpty) {
        if (field.required) {
          throw FormatException('${field.label} is required.');
        }
        continue;
      }
      if (input.length >
          (field.kind == Wd09cfInputKind.text ? field.maxLength : 64)) {
        throw FormatException('${field.label} is too long.');
      }
      switch (field.kind) {
        case Wd09cfInputKind.text:
          result[field.key] = input;
        case Wd09cfInputKind.choice:
          if (!field.values.contains(input)) {
            throw FormatException('Invalid ${field.label}.');
          }
          result[field.key] = input;
        case Wd09cfInputKind.positiveId:
          final n = int.tryParse(input);
          if (n == null || n <= 0 || n > 2147483647) {
            throw FormatException('Invalid ${field.label}.');
          }
          result[field.key] = n;
        case Wd09cfInputKind.nonNegativeInt:
          final n = int.tryParse(input);
          if (n == null || n < 0 || n > 2147483647) {
            throw FormatException('Invalid ${field.label}.');
          }
          result[field.key] = n;
        case Wd09cfInputKind.decimal:
          if (!RegExp(r'^\d+(?:\.\d{1,2})?$').hasMatch(input) ||
              double.tryParse(input) == null) {
            throw FormatException('Invalid ${field.label}.');
          }
          result[field.key] = input;
        case Wd09cfInputKind.date:
          if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(input)) {
            throw FormatException('Invalid ${field.label}.');
          }
          final date = DateTime.tryParse(input);
          if (date == null ||
              date.year != int.parse(input.substring(0, 4)) ||
              date.month != int.parse(input.substring(5, 7)) ||
              date.day != int.parse(input.substring(8, 10))) {
            throw FormatException('Invalid ${field.label}.');
          }
          result[field.key] = input;
      }
    }
    if (this == Wd09cfAction.payrollCreate ||
        this == Wd09cfAction.leaveRequest) {
      final start =
          result[this == Wd09cfAction.payrollCreate
                  ? 'pay_period_start'
                  : 'start_date']
              as String;
      final end =
          result[this == Wd09cfAction.payrollCreate
                  ? 'pay_period_end'
                  : 'end_date']
              as String;
      if (end.compareTo(start) < 0) {
        throw const FormatException('End date precedes start date.');
      }
    }
    if (this == Wd09cfAction.payrollCreate) {
      final base = double.parse(result['base_salary'] as String);
      final bonuses = double.tryParse(result['bonuses'] as String? ?? '0') ?? 0;
      final deductions =
          double.tryParse(result['deductions'] as String? ?? '0') ?? 0;
      if (deductions > base + bonuses) {
        throw const FormatException('Deductions exceed earnings.');
      }
    }
    if (this == Wd09cfAction.settingsUpdate) {
      final language = result['language'] as String;
      if (!RegExp(r'^[A-Za-z]+(?:-[A-Za-z]+)?$').hasMatch(language)) {
        throw const FormatException('Invalid language code.');
      }
    }
    return result;
  }
}
