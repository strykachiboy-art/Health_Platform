/// CE1 Surgery projections are documentation/status views, not clinical authority.
Map<String, dynamic> _surgeryObject(Object? raw, String name) {
  if (raw is! Map<String, dynamic>) {
    throw FormatException('$name must be an object.');
  }
  return raw;
}

String _surgeryText(Object? raw, String name, {int max = 90}) {
  if (raw is! String || raw.isEmpty || raw != raw.trim() || raw.length > max) {
    throw FormatException('$name is invalid.');
  }
  return raw;
}

int _surgeryNumber(Object? raw, String name, {bool positive = false}) {
  if (raw is! int || (positive ? raw <= 0 : raw < 0)) {
    throw FormatException('$name is invalid.');
  }
  return raw;
}

bool _surgeryBool(Object? raw, String name, bool expected) {
  if (raw != expected) {
    throw FormatException('$name has an unsafe authority value.');
  }
  return expected;
}

List<dynamic> _surgeryList(Object? raw, String name) {
  if (raw is! List<dynamic>) {
    throw FormatException('$name must be a list.');
  }
  return raw;
}

/// Only the roles/schemas projected by the pinned CE1 backend are accepted.
String surgeryViewForRole(String role) => switch (role.toLowerCase()) {
  'super_admin' => 'super_admin',
  'admin' => 'management',
  'doctor' => 'doctor',
  'nurse' => 'nurse',
  'receptionist' ||
  'driver' ||
  'ambulance_dispatcher' ||
  'ambulance_coordinator' ||
  'other' => 'operations',
  'accountant' => 'finance',
  'patient' => 'patient',
  _ => 'restricted',
};

bool surgeryCanReadCases(String role) => const <String>{
  'super_admin',
  'admin',
  'doctor',
  'nurse',
}.contains(role.toLowerCase());

class SurgeryCasePreview {
  const SurgeryCasePreview({
    required this.caseId,
    required this.caseStatus,
    required this.documentationStatus,
    required this.pacuPreparationStatus,
  });

  final int caseId;
  final String caseStatus;
  final String documentationStatus;
  final String pacuPreparationStatus;

  factory SurgeryCasePreview.fromJson(Map<String, dynamic> map) {
    const keys = <String>{
      'case_id',
      'case_status',
      'documentation_status',
      'pacu_preparation_status',
    };
    if (map.keys.any((key) => !keys.contains(key))) {
      throw const FormatException('Unexpected Surgery preview data.');
    }
    final status = _surgeryText(map['case_status'], 'case_status');
    final documentation = _surgeryText(
      map['documentation_status'],
      'documentation_status',
    );
    final pacu = _surgeryText(
      map['pacu_preparation_status'],
      'pacu_preparation_status',
    );
    if (!const {'planned', 'on_hold', 'cancelled'}.contains(status) ||
        !const {
          'not_started',
          'draft',
          'surgeon_signed',
          'dual_signed',
        }.contains(documentation) ||
        !const {
          'not_started',
          'draft',
          'prepared',
          'reviewed',
        }.contains(pacu)) {
      throw const FormatException('Unknown Surgery preview state.');
    }
    return SurgeryCasePreview(
      caseId: _surgeryNumber(map['case_id'], 'case_id', positive: true),
      caseStatus: status,
      documentationStatus: documentation,
      pacuPreparationStatus: pacu,
    );
  }
}

class SurgeryDashboardWidget {
  SurgeryDashboardWidget({
    required this.view,
    required this.scope,
    required this.visibility,
    required this.totalCases,
    required this.scheduledBookings,
    required this.pendingSignatures,
    required this.linkedBillingInvoices,
    required this.sealedDocumentation,
    required this.reviewedPacuPreparations,
    required this.dualSignedReports,
    required this.cases,
    required this.releaseRequired,
  });

  final String view;
  final String scope;
  final String visibility;
  final int totalCases;
  final int scheduledBookings;
  final int pendingSignatures;
  final int linkedBillingInvoices;
  final int sealedDocumentation;
  final int reviewedPacuPreparations;
  final int dualSignedReports;
  final List<SurgeryCasePreview> cases;
  final bool releaseRequired;

  factory SurgeryDashboardWidget.fromJson(
    Map<String, dynamic> map, {
    required String role,
  }) {
    const keys = <String>{
      'view',
      'scope',
      'visibility',
      'total_cases',
      'planned',
      'on_hold',
      'cancelled',
      'scheduled_bookings',
      'sealed_documentation',
      'reviewed_pacu_preparations',
      'dual_signed_reports',
      'pending_signatures',
      'linked_billing_invoices',
      'cases',
      'release_required',
      'period_applies',
      'documentation_only',
      'operative_completion_authorized',
      'nursing_transfer_authorized',
      'medication_administration_authorized',
      'billing_clearance_authorized',
    };
    if (map.keys.any((key) => !keys.contains(key))) {
      throw const FormatException('Unexpected Surgery dashboard data.');
    }
    final view = _surgeryText(map['view'], 'view');
    if (view != surgeryViewForRole(role)) {
      throw const FormatException('Surgery view does not match account role.');
    }
    final scope = _surgeryText(map['scope'], 'scope');
    final visibility = _surgeryText(map['visibility'], 'visibility');
    if (!const {
          'system_aggregate',
          'clinic',
          'assigned_cases',
          'operational_aggregate',
          'billing_aggregate',
          'own_patient',
          'restricted',
        }.contains(scope) ||
        !const {
          'aggregate_only',
          'assigned_only',
          'clinic_team',
          'own_summary',
          'restricted',
          'release_required',
        }.contains(visibility)) {
      throw const FormatException('Unknown Surgery dashboard scope.');
    }
    final allowedScope = switch (view) {
      'super_admin' => 'system_aggregate',
      'management' => 'clinic',
      'doctor' || 'nurse' => 'assigned_cases',
      'finance' => 'billing_aggregate',
      'patient' => 'own_patient',
      'operations' => 'operational_aggregate',
      _ => 'restricted',
    };
    if (scope != allowedScope &&
        !(visibility == 'restricted' && scope == 'restricted')) {
      throw const FormatException('Surgery widget scope does not match role.');
    }
    final validVisibility = switch (view) {
      'super_admin' => const <String>{'aggregate_only'},
      'management' => const <String>{'clinic_team', 'restricted'},
      'doctor' || 'nurse' => const <String>{'assigned_only', 'restricted'},
      'operations' ||
      'finance' => const <String>{'aggregate_only', 'restricted'},
      'patient' => const <String>{'release_required'},
      _ => const <String>{'restricted'},
    };
    if (!validVisibility.contains(visibility)) {
      throw const FormatException('Surgery widget visibility exceeds role.');
    }
    for (final flag in const [
      'operative_completion_authorized',
      'nursing_transfer_authorized',
      'medication_administration_authorized',
      'billing_clearance_authorized',
    ]) {
      _surgeryBool(map[flag], flag, false);
    }
    _surgeryBool(map['documentation_only'], 'documentation_only', true);
    if (map['period_applies'] is! bool || map['release_required'] is! bool) {
      throw const FormatException('Invalid Surgery widget flags.');
    }
    // Parse every backend count, even fields not currently displayed.
    for (final name in const ['planned', 'on_hold', 'cancelled']) {
      _surgeryNumber(map[name], name);
    }
    final rawCases = _surgeryList(map['cases'], 'cases');
    if (rawCases.length > 8 ||
        (visibility != 'assigned_only' &&
            visibility != 'clinic_team' &&
            rawCases.isNotEmpty)) {
      throw const FormatException('Surgery case preview visibility violation.');
    }
    final cases = rawCases
        .map(
          (e) => SurgeryCasePreview.fromJson(_surgeryObject(e, 'case preview')),
        )
        .toList();
    if (cases.map((e) => e.caseId).toSet().length != cases.length) {
      throw const FormatException('Duplicate Surgery cases.');
    }
    final releaseRequired = map['release_required'] as bool;
    final countFields = <String, int>{
      for (final field in const [
        'total_cases',
        'planned',
        'on_hold',
        'cancelled',
        'scheduled_bookings',
        'sealed_documentation',
        'reviewed_pacu_preparations',
        'dual_signed_reports',
        'pending_signatures',
        'linked_billing_invoices',
      ])
        field: _surgeryNumber(map[field], field),
    };
    final visibleCountKeys = switch (view) {
      'operations' => const <String>{'scheduled_bookings'},
      'finance' => const <String>{'linked_billing_invoices'},
      'patient' => const <String>{'total_cases'},
      _ => countFields.keys.toSet(),
    };
    if ((visibility == 'restricted' &&
            countFields.values.any((count) => count != 0)) ||
        countFields.entries.any(
          (entry) => !visibleCountKeys.contains(entry.key) && entry.value != 0,
        )) {
      throw const FormatException('Surgery count disclosure exceeds role.');
    }
    if ((view == 'patient' && !releaseRequired) ||
        (view != 'patient' && releaseRequired)) {
      throw const FormatException('Surgery release boundary mismatch.');
    }
    return SurgeryDashboardWidget(
      view: view,
      scope: scope,
      visibility: visibility,
      totalCases: _surgeryNumber(map['total_cases'], 'total_cases'),
      scheduledBookings: _surgeryNumber(
        map['scheduled_bookings'],
        'scheduled_bookings',
      ),
      pendingSignatures: _surgeryNumber(
        map['pending_signatures'],
        'pending_signatures',
      ),
      linkedBillingInvoices: _surgeryNumber(
        map['linked_billing_invoices'],
        'linked_billing_invoices',
      ),
      sealedDocumentation: _surgeryNumber(
        map['sealed_documentation'],
        'sealed_documentation',
      ),
      reviewedPacuPreparations: _surgeryNumber(
        map['reviewed_pacu_preparations'],
        'reviewed_pacu_preparations',
      ),
      dualSignedReports: _surgeryNumber(
        map['dual_signed_reports'],
        'dual_signed_reports',
      ),
      cases: List<SurgeryCasePreview>.unmodifiable(cases),
      releaseRequired: releaseRequired,
    );
  }
}

class SurgeryCaseRead {
  const SurgeryCaseRead({
    required this.caseId,
    required this.caseStatus,
    required this.journalStatus,
    required this.pacuStatus,
    required this.reportStatus,
    required this.signatureRoles,
    required this.materialTraceCount,
    required this.clinicalReferenceCount,
  });

  final int caseId;
  final String caseStatus;
  final String journalStatus;
  final String pacuStatus;
  final String reportStatus;
  final List<String> signatureRoles;
  final int materialTraceCount;
  final int clinicalReferenceCount;

  factory SurgeryCaseRead.fromJson(Map<String, dynamic> json) {
    _surgeryBool(json['documentary_only'], 'documentary_only', true);
    for (final field in const [
      'operative_completion_authorized',
      'pacu_arrival_or_transfer_authorized',
      'sign_out_authorized',
      'medication_administration_authorized',
      'billing_clearance_authorized',
    ]) {
      _surgeryBool(json[field], field, false);
    }
    final status = _surgeryText(json['case_status'], 'case_status');
    if (!const {'planned', 'on_hold', 'cancelled'}.contains(status)) {
      throw const FormatException('Unknown Surgery case state.');
    }
    final roles = _surgeryList(
      json['signature_roles'],
      'signature_roles',
    ).map((item) => _surgeryText(item, 'signature role')).toList();
    return SurgeryCaseRead(
      caseId: _surgeryNumber(json['case_id'], 'case_id', positive: true),
      caseStatus: status,
      journalStatus: _surgeryText(json['journal_status'], 'journal_status'),
      pacuStatus: _surgeryText(json['pacu_preparation_status'], 'pacu status'),
      reportStatus: _surgeryText(
        json['operative_report_status'],
        'report status',
      ),
      signatureRoles: List<String>.unmodifiable(roles),
      materialTraceCount: _surgeryNumber(
        json['material_trace_count'],
        'material_trace_count',
      ),
      clinicalReferenceCount: _surgeryNumber(
        json['clinical_reference_count'],
        'clinical_reference_count',
      ),
    );
  }
}

class SurgeryWorklist {
  const SurgeryWorklist({
    required this.clinicId,
    required this.scope,
    required this.total,
    required this.limit,
    required this.offset,
    required this.cases,
  });

  final int clinicId;
  final String scope;
  final int total;
  final int limit;
  final int offset;
  final List<SurgeryCaseRead> cases;

  factory SurgeryWorklist.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinic,
    required String role,
  }) {
    _surgeryBool(json['documentation_only'], 'documentation_only', true);
    _surgeryBool(
      json['operative_completion_authorized'],
      'operative_completion_authorized',
      false,
    );
    _surgeryBool(
      json['clinical_execution_authorized'],
      'clinical_execution_authorized',
      false,
    );
    final clinic = _surgeryNumber(
      json['clinic_id'],
      'clinic_id',
      positive: true,
    );
    if (clinic != expectedClinic) {
      throw const FormatException('Surgery worklist clinic mismatch.');
    }
    final scope = _surgeryText(json['scope'], 'scope');
    if (scope !=
        (const <String>{'doctor', 'nurse'}.contains(role.toLowerCase())
            ? 'assigned_cases'
            : 'clinic')) {
      throw const FormatException(
        'Surgery worklist assignment scope mismatch.',
      );
    }
    final limit = _surgeryNumber(json['limit'], 'limit', positive: true);
    final offset = _surgeryNumber(json['offset'], 'offset');
    if (limit > 50 || offset > 5000) {
      throw const FormatException('Surgery pagination out of bounds.');
    }
    final metrics = _surgeryObject(json['metrics'], 'metrics');
    for (final key in const [
      'planned',
      'on_hold',
      'cancelled',
      'sealed_documentation',
      'reviewed_pacu_preparations',
      'dual_signed_reports',
    ]) {
      _surgeryNumber(metrics[key], key);
    }
    final rawCases = _surgeryList(json['cases'], 'cases');
    if (rawCases.length > limit) {
      throw const FormatException('Surgery pagination overflow.');
    }
    final cases = rawCases
        .map((e) => SurgeryCaseRead.fromJson(_surgeryObject(e, 'case')))
        .toList();
    if (cases.map((e) => e.caseId).toSet().length != cases.length) {
      throw const FormatException('Duplicate Surgery case.');
    }
    return SurgeryWorklist(
      clinicId: clinic,
      scope: scope,
      total: _surgeryNumber(json['total'], 'total'),
      limit: limit,
      offset: offset,
      cases: List<SurgeryCaseRead>.unmodifiable(cases),
    );
  }
}

class SurgeryCaseDetail {
  const SurgeryCaseDetail({
    required this.caseRecord,
    required this.clinicId,
    required this.caseVersion,
  });

  final SurgeryCaseRead caseRecord;
  final int clinicId;
  final int caseVersion;

  factory SurgeryCaseDetail.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinic,
    required int expectedCase,
  }) {
    final caseRecord = SurgeryCaseRead.fromJson(json);
    final clinic = _surgeryNumber(
      json['clinic_id'],
      'clinic_id',
      positive: true,
    );
    if (clinic != expectedClinic || caseRecord.caseId != expectedCase) {
      throw const FormatException('Surgery case identity mismatch.');
    }
    for (final field in const [
      'patient_id',
      'admission_id',
      'procedure_order_id',
    ]) {
      _surgeryNumber(json[field], field, positive: true);
    }
    return SurgeryCaseDetail(
      caseRecord: caseRecord,
      clinicId: clinic,
      caseVersion: _surgeryNumber(json['case_version'], 'case_version'),
    );
  }
}
