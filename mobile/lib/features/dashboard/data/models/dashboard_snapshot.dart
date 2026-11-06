/// P22.S03 — read-only backend dashboard contract. These values are advisory
/// snapshots, never clinical authorization or permission grants.
///
/// Money/Decimal values are kept as text: Pydantic serializes Decimal to
/// JSON strings, and converting them through double would lose precision.
enum DashboardFamily {
  system,
  management,
  clinical,
  operations,
  finance,
  patient;

  static DashboardFamily fromRole(String role) => switch (role.toLowerCase()) {
    'super_admin' => DashboardFamily.system,
    'admin' => DashboardFamily.management,
    'doctor' ||
    'nurse' ||
    'pharmacist' ||
    'lab_technician' ||
    'paramedic' ||
    'emt' => DashboardFamily.clinical,
    'receptionist' ||
    'driver' ||
    'ambulance_dispatcher' ||
    'ambulance_coordinator' ||
    'other' => DashboardFamily.operations,
    'accountant' => DashboardFamily.finance,
    'patient' => DashboardFamily.patient,
    _ => throw const FormatException('Unrecognized dashboard role.'),
  };

  String get expectedScope => switch (this) {
    DashboardFamily.system => 'system',
    DashboardFamily.patient => 'personal',
    _ => 'clinic',
  };
}

Map<String, dynamic> _object(Object? value, String field) {
  if (value is! Map<String, dynamic>) {
    throw FormatException('$field must be a JSON object.');
  }
  return value;
}

List<dynamic> _array(Object? value, String field) {
  if (value is! List<dynamic>) {
    throw FormatException('$field must be an array.');
  }
  return value;
}

String _string(Object? value, String field, {int maxLength = 200}) {
  if (value is! String ||
      value.isEmpty ||
      value != value.trim() ||
      value.length > maxLength) {
    throw FormatException('$field must be a valid non-empty string.');
  }
  return value;
}

String? _optionalString(Object? value, String field, {int maxLength = 500}) {
  if (value == null) return null;
  return _string(value, field, maxLength: maxLength);
}

int _count(Object? value, String field, {bool positive = false}) {
  if (value is! int || (positive ? value <= 0 : value < 0)) {
    throw FormatException('$field must be a valid nonnegative integer.');
  }
  return value;
}

DateTime _timestamp(Object? value, String field) {
  final text = _string(value, field, maxLength: 64);
  // Reject timezone-naive timestamps; clinical dashboard snapshots must
  // not depend on a device's local timezone.
  if (!RegExp(r'(Z|[+-][0-9]{2}:[0-9]{2})$').hasMatch(text)) {
    throw FormatException('$field requires an explicit timezone.');
  }
  final parsed = DateTime.tryParse(text);
  if (parsed == null) throw FormatException('$field is not a timestamp.');
  return parsed.toUtc();
}

DateTime _calendarDate(Object? value, String field) {
  final text = _string(value, field, maxLength: 10);
  if (!RegExp(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$').hasMatch(text)) {
    throw FormatException('$field must be YYYY-MM-DD.');
  }
  final parsed = DateTime.tryParse('${text}T00:00:00Z');
  if (parsed == null || parsed.toIso8601String().substring(0, 10) != text) {
    throw FormatException('$field must be a real calendar date.');
  }
  return parsed;
}

/// Exact string form is retained for lossless financial amount display.
class DashboardNumber {
  const DashboardNumber._(this.text);

  final String text;

  factory DashboardNumber.fromJson(
    Object? value, {
    bool allowNegative = false,
  }) {
    if (value is bool) {
      throw const FormatException('Dashboard numeric value is invalid.');
    }
    if (value is int) {
      if (!allowNegative && value < 0) {
        throw const FormatException('Dashboard numeric value is negative.');
      }
      return DashboardNumber._(value.toString());
    }
    if (value is double) {
      if (!value.isFinite || (!allowNegative && value < 0)) {
        throw const FormatException('Dashboard numeric value is invalid.');
      }
      return DashboardNumber._(value.toString());
    }
    if (value is String &&
        value.length <= 128 &&
        RegExp(r'^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$')
            .hasMatch(value) &&
        (allowNegative || !value.startsWith('-'))) {
      return DashboardNumber._(value);
    }
    throw const FormatException('Dashboard numeric value is invalid.');
  }
}

class DashboardContext {
  const DashboardContext({
    required this.role,
    required this.family,
    required this.scope,
    required this.clinicId,
    required this.generatedAt,
  });

  final String role;
  final DashboardFamily family;
  final String scope;
  final int? clinicId;
  final DateTime generatedAt;

  factory DashboardContext.fromJson(Map<String, dynamic> json) {
    final role = _string(json['role'], 'context.role', maxLength: 40);
    final family = DashboardFamily.fromRole(role);
    final scope = _string(json['scope'], 'context.scope', maxLength: 20);
    if (scope != family.expectedScope) {
      throw const FormatException('Dashboard scope does not match role.');
    }
    final rawClinicId = json['clinic_id'];
    final clinicId = rawClinicId == null
        ? null
        : _count(rawClinicId, 'context.clinic_id', positive: true);
    if ((scope == 'system' && clinicId != null) ||
        (scope == 'clinic' && clinicId == null)) {
      throw const FormatException('Dashboard clinic scope is invalid.');
    }
    return DashboardContext(
      role: role,
      family: family,
      scope: scope,
      clinicId: clinicId,
      generatedAt: _timestamp(json['generated_at'], 'context.generated_at'),
    );
  }
}

class DashboardPeriod {
  const DashboardPeriod({required this.from, required this.to});

  final DateTime from;
  final DateTime to;

  factory DashboardPeriod.fromJson(Map<String, dynamic> json) {
    final from = _calendarDate(json['date_from'], 'period.date_from');
    final to = _calendarDate(json['date_to'], 'period.date_to');
    if (to.isBefore(from)) {
      throw const FormatException('Dashboard period is reversed.');
    }
    return DashboardPeriod(from: from, to: to);
  }
}

/// Validated, family-specific overview numbers. No invented default zeroes.
class DashboardOverview {
  DashboardOverview._(this.family, this.values);

  final DashboardFamily family;
  final Map<String, DashboardNumber> values;

  static const Map<DashboardFamily, List<String>> _requiredFields = {
    DashboardFamily.system: [
      'total_clinics',
      'active_clinics',
      'suspended_clinics',
      'total_users',
      'active_users',
      'total_staff',
      'total_patients',
    ],
    DashboardFamily.management: [
      'total_patients',
      'active_patients',
      'total_staff',
      'active_staff',
      'appointments_today',
      'missed_appointments_today',
      'active_admissions',
      'occupied_beds',
      'pending_lab_orders',
    ],
    DashboardFamily.clinical: [
      'appointments_today',
      'confirmed_appointments_today',
      'pending_consultations',
      'pending_lab_orders',
      'active_prescriptions',
      'active_admissions',
    ],
    DashboardFamily.operations: [
      'appointments_today',
      'missed_appointments_today',
      'scheduled_appointments_today',
      'confirmed_appointments_today',
      'active_ambulance_trips',
      'pending_ambulance_requests',
      'active_staff',
      'active_departments',
      'unassigned_active_staff',
    ],
    DashboardFamily.finance: [
      'outstanding_invoice_count',
      'outstanding_invoice_amount',
      'overdue_invoice_count',
      'overdue_invoice_amount',
      'successful_payments_today',
      'successful_payments_today_amount',
      'pending_payment_count',
    ],
    DashboardFamily.patient: [
      'upcoming_appointments',
      'active_prescriptions',
      'active_admissions',
      'outstanding_balance',
      'unread_notifications',
    ],
  };

  factory DashboardOverview.fromJson(
    Map<String, dynamic> json,
    DashboardFamily family,
  ) {
    const decimalFields = <String>{
      'outstanding_invoice_amount',
      'overdue_invoice_amount',
      'successful_payments_today_amount',
      'outstanding_balance',
    };
    final values = <String, DashboardNumber>{};
    for (final key in _requiredFields[family]!) {
      final value = json[key];
      if (!decimalFields.contains(key)) {
        // Pydantic Field(ge=0) with an int annotation must not silently
        // accept a string or fractional count from a drifting backend.
        _count(value, 'overview.$key');
      }
      values[key] = DashboardNumber.fromJson(value);
    }
    return DashboardOverview._(family, Map.unmodifiable(values));
  }
}

class DashboardTrend {
  const DashboardTrend({required this.direction, this.percentage});
  final String direction;
  final DashboardNumber? percentage;

  factory DashboardTrend.fromJson(Map<String, dynamic> json) {
    final direction = _string(json['direction'], 'metric.trend.direction');
    if (!const {'up', 'down', 'flat'}.contains(direction)) {
      throw const FormatException('Unknown dashboard trend direction.');
    }
    return DashboardTrend(
      direction: direction,
      percentage: json['percentage'] == null
          ? null
          : DashboardNumber.fromJson(json['percentage'], allowNegative: true),
    );
  }
}

class DashboardMetric {
  const DashboardMetric({
    required this.key,
    required this.label,
    required this.value,
    this.unit,
    this.trend,
  });

  final String key;
  final String label;
  final DashboardNumber value;
  final String? unit;
  final DashboardTrend? trend;

  factory DashboardMetric.fromJson(Map<String, dynamic> json) {
    return DashboardMetric(
      key: _string(json['key'], 'metric.key', maxLength: 100),
      label: _string(json['label'], 'metric.label', maxLength: 150),
      value: DashboardNumber.fromJson(json['value'], allowNegative: true),
      unit: _optionalString(json['unit'], 'metric.unit', maxLength: 50),
      trend: json['trend'] == null
          ? null
          : DashboardTrend.fromJson(_object(json['trend'], 'metric.trend')),
    );
  }
}

/// Dashboard severity is NOT the clinical-safety alert severity enum.
class DashboardNotice {
  const DashboardNotice({
    required this.key,
    required this.severity,
    required this.title,
    required this.count,
    this.description,
  });

  final String key;
  final String severity;
  final String title;
  final int count;
  final String? description;

  factory DashboardNotice.fromJson(Map<String, dynamic> json) {
    final severity = _string(json['severity'], 'alert.severity');
    if (!const {'info', 'warning', 'critical'}.contains(severity)) {
      throw const FormatException('Unknown dashboard alert severity.');
    }
    return DashboardNotice(
      key: _string(json['key'], 'alert.key', maxLength: 100),
      severity: severity,
      title: _string(json['title'], 'alert.title'),
      count: _count(json['count'], 'alert.count'),
      description: _optionalString(json['description'], 'alert.description'),
    );
  }
}

class DashboardActivity {
  const DashboardActivity({
    required this.entityType,
    required this.entityId,
    required this.action,
    required this.occurredAt,
  });

  final String entityType;
  final int entityId;
  final String action;
  final DateTime occurredAt;

  factory DashboardActivity.fromJson(Map<String, dynamic> json) {
    return DashboardActivity(
      entityType: _string(
        json['entity_type'],
        'activity.entity_type',
        maxLength: 100,
      ),
      entityId: _count(json['entity_id'], 'activity.entity_id', positive: true),
      action: _string(json['action'], 'activity.action', maxLength: 100),
      occurredAt: _timestamp(json['occurred_at'], 'activity.occurred_at'),
    );
  }
}

class DashboardChatSummary {
  DashboardChatSummary._(this.counts);
  final Map<String, int> counts;

  factory DashboardChatSummary.fromJson(Map<String, dynamic> json) {
    final counts = <String, int>{};
    for (final key in const [
      'unread_messages',
      'unread_conversations',
      'mentions',
      'priority_messages',
      'recent_messages',
    ]) {
      counts[key] = _count(json[key], 'chat.$key');
    }
    return DashboardChatSummary._(Map.unmodifiable(counts));
  }
}

class DashboardSnapshot {
  const DashboardSnapshot({
    required this.context,
    required this.period,
    required this.overview,
    required this.metrics,
    required this.alerts,
    required this.recentActivity,
    this.chat,
  });

  final DashboardContext context;
  final DashboardPeriod period;
  final DashboardOverview overview;
  final List<DashboardMetric> metrics;
  final List<DashboardNotice> alerts;
  final List<DashboardActivity> recentActivity;
  final DashboardChatSummary? chat;

  factory DashboardSnapshot.fromEnvelope(Map<String, dynamic> envelope) {
    if (envelope['success'] != true) {
      throw const FormatException('Dashboard response was not successful.');
    }
    final data = _object(envelope['data'], 'data');
    final context = DashboardContext.fromJson(
      _object(data['context'], 'context'),
    );
    final family = context.family;
    final requiresChat =
        family != DashboardFamily.system && family != DashboardFamily.patient;
    final rawChat = data['chat'];
    if (requiresChat && rawChat == null) {
      throw const FormatException('Dashboard chat summary is missing.');
    }
    if (family == DashboardFamily.patient &&
        (data.containsKey('alerts') || data.containsKey('recent_activity'))) {
      throw const FormatException(
        'Unexpected staff data on patient dashboard.',
      );
    }
    return DashboardSnapshot(
      context: context,
      period: DashboardPeriod.fromJson(_object(data['period'], 'period')),
      overview: DashboardOverview.fromJson(
        _object(data['overview'], 'overview'),
        family,
      ),
      metrics: List<DashboardMetric>.unmodifiable(
        _array(
          data['metrics'],
          'metrics',
        ).map((item) => DashboardMetric.fromJson(_object(item, 'metric'))),
      ),
      alerts: List<DashboardNotice>.unmodifiable(
        (family == DashboardFamily.patient
                ? const <dynamic>[]
                : _array(data['alerts'], 'alerts'))
            .map((item) => DashboardNotice.fromJson(_object(item, 'alert'))),
      ),
      recentActivity: List<DashboardActivity>.unmodifiable(
        (family == DashboardFamily.patient
                ? const <dynamic>[]
                : _array(data['recent_activity'], 'recent_activity'))
            .map(
              (item) => DashboardActivity.fromJson(_object(item, 'activity')),
            ),
      ),
      chat: rawChat == null
          ? null
          : DashboardChatSummary.fromJson(_object(rawChat, 'chat')),
    );
  }
}
