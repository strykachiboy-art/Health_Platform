// WD07-B S11: read-only presentation of *existing*, role-scoped dashboard
// subcontracts. Never synthesizes a server metric or grants any permission.
// Every displayed value is taken from a validated server response.

class DashboardExtendedMetric {
  const DashboardExtendedMetric(this.label, this.value);
  final String label;
  final String value;
}

class DashboardExtendedBreakdown {
  const DashboardExtendedBreakdown(this.title, this.entries);
  final String title;
  final List<String> entries;
}

class DashboardExtendedSection {
  const DashboardExtendedSection(this.title, this.metrics, this.breakdowns);
  final String title;
  final List<DashboardExtendedMetric> metrics;
  final List<DashboardExtendedBreakdown> breakdowns;
}

class DashboardExtendedSections {
  const DashboardExtendedSections(this.sections);
  final List<DashboardExtendedSection> sections;

  static Map<String, dynamic> _object(Object? value, String field) {
    if (value is! Map<String, dynamic>) {
      throw FormatException('Invalid $field dashboard section.');
    }
    return value;
  }

  static Map<String, dynamic>? _optionalObject(
    Map<String, dynamic> parent,
    String key,
  ) {
    if (!parent.containsKey(key) || parent[key] == null) return null;
    return _object(parent[key], key);
  }

  static String _text(Object? value, String field) {
    if (value is! String ||
        value.trim() != value ||
        value.isEmpty ||
        value.length > 150) {
      throw FormatException('Invalid $field text.');
    }
    return value;
  }

  static String _number(Object? value, String field) {
    if (value is int && value >= 0) return value.toString();
    if (value is double && value.isFinite && value >= 0) {
      return value.toString();
    }
    // Retain the exact backend Decimal serialization. Do not round via double.
    if (value is String &&
        value.length <= 100 &&
        RegExp(r'^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$')
            .hasMatch(value)) {
      return value;
    }
    throw FormatException('Invalid $field numeric value.');
  }

  static const _labels = <String, String>{
    'outstanding_invoice_count': 'Outstanding invoices',
    'outstanding_invoice_amount': 'Outstanding invoice amount',
    'overdue_invoice_count': 'Overdue invoices',
    'overdue_invoice_amount': 'Overdue invoice amount',
    'successful_payments_today': 'Successful payments today',
    'successful_payments_today_amount': 'Payments received today',
    'pending_payment_count': 'Pending payments',
    'total_ai_requests': 'AI requests',
    'ai_requests': 'My AI requests',
    'total_credits_used': 'AI credits consumed',
    'total_input_tokens': 'Input tokens',
    'total_output_tokens': 'Output tokens',
    'total_tokens': 'AI tokens',
    'estimated_cost': 'Estimated AI cost (backend currency)',
    'pending_reviews': 'Pending AI reviews',
    'approved_reviews': 'Approved AI reviews',
    'rejected_reviews': 'Rejected AI reviews',
    'approved_results': 'Approved AI results',
    'rejected_results': 'Rejected AI results',
    'low_risk_results': 'Low-risk AI results',
    'medium_risk_results': 'Medium-risk AI results',
    'high_risk_results': 'High-risk AI results',
    'critical_risk_results': 'Critical-risk AI results',
    'total_departments': 'Departments',
    'active_departments': 'Active departments',
    'inactive_departments': 'Inactive departments',
    'suspended_departments': 'Suspended departments',
    'total_users': 'Total users',
    'active_users': 'Active users',
    'inactive_users': 'Inactive users',
    'total_admins': 'Administrators',
    'total': 'Assigned patients',
    'active_admins': 'Active administrators',
    'total_super_admins': 'Super administrators',
    'active_admissions': 'Active admissions',
    'total_beds': 'Total beds',
    'occupied_beds': 'Occupied beds',
    'available_beds': 'Available beds',
    'active_care_plans': 'Active care plans',
    'pending_tasks': 'Pending nursing tasks',
    'in_progress_tasks': 'In-progress nursing tasks',
    'urgent_open_tasks': 'Urgent nursing tasks',
    'overdue_open_tasks': 'Overdue nursing tasks',
    'unsigned_assessments': 'Unsigned assessments',
    'ready_handoffs': 'Ready handoffs',
    'scheduled': 'Scheduled medication administrations',
    'due': 'Medications due',
    'administered': 'Medications administered',
    'held': 'Medications held',
    'refused': 'Medications refused',
    'missed': 'Missed administrations',
    'draft': 'Draft reconciliations',
    'final': 'Final reconciliations',
    'amended': 'Amended reconciliations',
    'draft_discharge': 'Draft discharge reconciliations',
    'finalized_discharge': 'Final discharge reconciliations',
  };

  static DashboardExtendedSection _section(
    String title,
    Map<String, dynamic> source,
    List<String> keys, {
    List<DashboardExtendedBreakdown> breakdowns = const [],
  }) {
    final entries = <DashboardExtendedMetric>[];
    for (final key in keys) {
      // Absent optional metrics are not replaced with fabricated zeroes.
      if (!source.containsKey(key)) continue;
      entries.add(
        DashboardExtendedMetric(
          _labels[key] ?? key,
          _number(source[key], '$title.$key'),
        ),
      );
    }
    return DashboardExtendedSection(
      title,
      List<DashboardExtendedMetric>.unmodifiable(entries),
      List<DashboardExtendedBreakdown>.unmodifiable(breakdowns),
    );
  }

  static DashboardExtendedBreakdown? _rows(
    Map<String, dynamic> parent,
    String key,
    String title,
    String labelField,
    List<String> numericFields,
  ) {
    if (!parent.containsKey(key)) return null;
    final raw = parent[key];
    if (raw is! List || raw.length > 500) {
      throw FormatException('Invalid $key dashboard breakdown.');
    }
    final entries = <String>[];
    for (final item in raw) {
      final obj = _object(item, key);
      final label = _text(obj[labelField], '$key.$labelField');
      final values = <String>[];
      for (final field in numericFields) {
        if (!obj.containsKey(field)) {
          throw FormatException('Missing $key.$field.');
        }
        values.add(
          '${_labels[field] ?? field.replaceAll('_', ' ')}: '
          '${_number(obj[field], '$key.$field')}',
        );
      }
      entries.add('$label — ${values.join(' · ')}');
    }
    return DashboardExtendedBreakdown(
      title,
      List<String>.unmodifiable(entries),
    );
  }

  static void _addIfContent(
    List<DashboardExtendedSection> target,
    DashboardExtendedSection section,
  ) {
    if (section.metrics.isNotEmpty || section.breakdowns.isNotEmpty) {
      target.add(section);
    }
  }

  static void _ai(
    List<DashboardExtendedSection> target,
    Map<String, dynamic> payload,
    String role,
  ) {
    final ai = _optionalObject(payload, 'ai');
    if (ai == null) return;
    final overview = role == 'accountant'
        ? ai
        : _object(ai['overview'], 'ai.overview');
    final fields = role == 'accountant'
        ? const ['total_ai_requests', 'total_credits_used', 'estimated_cost']
        : role == 'doctor' ||
              role == 'nurse' ||
              role == 'pharmacist' ||
              role == 'lab_technician'
        ? const [
            'ai_requests',
            'pending_reviews',
            'high_risk_results',
            'critical_risk_results',
            'approved_results',
            'rejected_results',
          ]
        : const [
            'total_ai_requests',
            'total_credits_used',
            'total_input_tokens',
            'total_output_tokens',
            'total_tokens',
            'estimated_cost',
            'pending_reviews',
            'approved_reviews',
            'rejected_reviews',
            'low_risk_results',
            'medium_risk_results',
            'high_risk_results',
            'critical_risk_results',
          ];
    final breakdowns = <DashboardExtendedBreakdown>[];
    final usage = _rows(
      ai,
      'feature_usage',
      'AI usage by feature',
      'feature',
      role == 'doctor' ||
              role == 'nurse' ||
              role == 'pharmacist' ||
              role == 'lab_technician'
          ? const ['request_count']
          : const ['request_count', 'credits_used', 'estimated_cost'],
    );
    if (usage != null) breakdowns.add(usage);
    final risks = _rows(
      ai,
      'risk_summary',
      'AI risk levels',
      'risk_level',
      const ['count'],
    );
    if (risks != null) breakdowns.add(risks);
    final reviewKey = ai.containsKey('review_summary')
        ? 'review_summary'
        : 'approval_summary';
    final reviews = _rows(
      ai,
      reviewKey,
      'AI approval status',
      'approval_status',
      const ['count'],
    );
    if (reviews != null) breakdowns.add(reviews);
    _addIfContent(
      target,
      _section('AI usage and safety', overview, fields, breakdowns: breakdowns),
    );
  }

  static void _hospital(
    List<DashboardExtendedSection> target,
    Map<String, dynamic> payload,
  ) {
    final hospital = _optionalObject(payload, 'hospital');
    if (hospital == null) return;
    final sections = <String, List<String>>{
      'admissions': [
        'active_admissions',
        'total_beds',
        'occupied_beds',
        'available_beds',
      ],
      'nursing': [
        'active_care_plans',
        'pending_tasks',
        'in_progress_tasks',
        'urgent_open_tasks',
        'overdue_open_tasks',
        'unsigned_assessments',
        'ready_handoffs',
      ],
      'emar': ['scheduled', 'due', 'administered', 'held', 'refused', 'missed'],
      'medication_reconciliation': [
        'draft',
        'final',
        'amended',
        'draft_discharge',
        'finalized_discharge',
      ],
    };
    final names = <String, String>{
      'admissions': 'Hospital bed capacity',
      'nursing': 'Nursing workload',
      'emar': 'Medication administrations',
      'medication_reconciliation': 'Medication reconciliation',
    };
    for (final entry in sections.entries) {
      final obj = _optionalObject(hospital, entry.key);
      if (obj != null) {
        _addIfContent(target, _section(names[entry.key]!, obj, entry.value));
      }
    }
  }

  static DashboardExtendedSections fromPayload(
    Map<String, dynamic> payload,
    String role,
  ) {
    final normalized = role.trim().toLowerCase();
    final result = <DashboardExtendedSection>[];
    if (normalized == 'accountant') {
      final financial = _optionalObject(payload, 'overview');
      if (financial != null) {
        _addIfContent(
          result,
          _section('Billing and payment totals', financial, const [
            'outstanding_invoice_count',
            'outstanding_invoice_amount',
            'overdue_invoice_count',
            'overdue_invoice_amount',
            'successful_payments_today',
            'successful_payments_today_amount',
            'pending_payment_count',
          ]),
        );
      }
      _ai(result, payload, normalized);
    } else if (normalized == 'super_admin') {
      final acl = _optionalObject(payload, 'access_control');
      if (acl != null) {
        final usersByRole = <DashboardExtendedBreakdown>[];
        final raw = acl['users_by_role'];
        if (raw != null) {
          final map = _object(raw, 'access_control.users_by_role');
          if (map.length > 50) {
            throw const FormatException('Too many role summaries.');
          }
          final entries = <String>[];
          for (final item in map.entries) {
            entries.add(
              '${_text(item.key, 'access_control.role')}: '
              '${_number(item.value, 'access_control.role_count')}',
            );
          }
          usersByRole.add(
            DashboardExtendedBreakdown(
              'User distribution by role',
              List<String>.unmodifiable(entries),
            ),
          );
        }
        _addIfContent(
          result,
          _section('System access control', acl, const [
            'total_users',
            'active_users',
            'inactive_users',
            'total_admins',
            'active_admins',
            'total_super_admins',
          ], breakdowns: usersByRole),
        );
      }
      final dept = _optionalObject(payload, 'departments');
      if (dept != null) {
        _addIfContent(
          result,
          _section('System departments', dept, const [
            'total_departments',
            'active_departments',
          ]),
        );
      }
      _ai(result, payload, normalized);
    } else if (normalized == 'admin') {
      _hospital(result, payload);
      final dept = _optionalObject(payload, 'departments');
      if (dept != null) {
        _addIfContent(
          result,
          _section('Clinic departments', dept, const [
            'total_departments',
            'active_departments',
            'inactive_departments',
            'suspended_departments',
          ]),
        );
      }
      _ai(result, payload, normalized);
    } else if (normalized == 'doctor' ||
        normalized == 'nurse' ||
        normalized == 'pharmacist' ||
        normalized == 'lab_technician') {
      if (normalized == 'doctor' || normalized == 'nurse') {
        _hospital(result, payload);
        final mine = _optionalObject(payload, 'my_patients');
        if (mine != null && mine.containsKey('total')) {
          _addIfContent(
            result,
            _section('My patient assignment summary', mine, const ['total']),
          );
        }
      }
      _ai(result, payload, normalized);
    } else if (normalized == 'receptionist') {
      final capacity = _optionalObject(payload, 'hospital_capacity');
      if (capacity != null) {
        _addIfContent(
          result,
          _section('Hospital capacity', capacity, const [
            'active_admissions',
            'total_beds',
            'occupied_beds',
            'available_beds',
          ]),
        );
      }
    }
    // Paramedic, EMT, transport, patient and other have no new role-specific
    // sections in this API. Their S10-B presentation remains unchanged.
    return DashboardExtendedSections(
      List<DashboardExtendedSection>.unmodifiable(result),
    );
  }
}
