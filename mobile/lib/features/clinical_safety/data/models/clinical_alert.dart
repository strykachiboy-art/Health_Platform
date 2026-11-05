/// Backend-authoritative Clinical Safety alert state. Never compute clinical
/// decisions or override permissions from these presentation values.
enum ClinicalAlertSeverity {
  info,
  low,
  moderate,
  high,
  critical;

  static ClinicalAlertSeverity fromJson(Object? value) => switch (value) {
    'info' => ClinicalAlertSeverity.info,
    'low' => ClinicalAlertSeverity.low,
    'moderate' => ClinicalAlertSeverity.moderate,
    'high' => ClinicalAlertSeverity.high,
    'critical' => ClinicalAlertSeverity.critical,
    _ => throw const FormatException('Invalid clinical alert severity.'),
  };
}

enum ClinicalAlertAction {
  inform,
  alert,
  requireAcknowledgement,
  requireJustification,
  block;

  static ClinicalAlertAction fromJson(Object? value) => switch (value) {
    'inform' => ClinicalAlertAction.inform,
    'alert' => ClinicalAlertAction.alert,
    'require_acknowledgement' => ClinicalAlertAction.requireAcknowledgement,
    'require_justification' => ClinicalAlertAction.requireJustification,
    'block' => ClinicalAlertAction.block,
    _ => throw const FormatException('Invalid clinical alert action.'),
  };
}

enum ClinicalAlertStatus {
  open,
  acknowledged,
  overridden,
  resolved,
  expired;

  static ClinicalAlertStatus fromJson(Object? value) => switch (value) {
    'open' => ClinicalAlertStatus.open,
    'acknowledged' => ClinicalAlertStatus.acknowledged,
    'overridden' => ClinicalAlertStatus.overridden,
    'resolved' => ClinicalAlertStatus.resolved,
    'expired' => ClinicalAlertStatus.expired,
    _ => throw const FormatException('Invalid clinical alert status.'),
  };
}

/// Typed view of the authoritative backend ClinicalAlertResponseSchema.
/// Retained in memory only; it is not written to Drift or another cache.
class ClinicalAlert {
  const ClinicalAlert({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.ruleId,
    required this.ruleVersion,
    required this.severity,
    required this.action,
    required this.status,
    required this.title,
    required this.message,
    required this.context,
    required this.sourceType,
    required this.sourceId,
    required this.deduplicationKey,
    required this.generatedAt,
    required this.resolvedAt,
    required this.createdAt,
    required this.updatedAt,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int ruleId;
  final int ruleVersion;
  final ClinicalAlertSeverity severity;
  final ClinicalAlertAction action;
  final ClinicalAlertStatus status;
  final String title;
  final String message;
  final Map<String, dynamic> context;
  final String sourceType;
  final int? sourceId;
  final String? deduplicationKey;
  final DateTime generatedAt;
  final DateTime? resolvedAt;
  final DateTime createdAt;
  final DateTime updatedAt;

  factory ClinicalAlert.fromJson(Map<String, dynamic> json) {
    final context = json['context'];
    if (context is! Map<String, dynamic>) {
      throw const FormatException('Clinical alert context must be an object.');
    }

    return ClinicalAlert(
      id: _positiveInt(json['id'], 'id'),
      clinicId: _positiveInt(json['clinic_id'], 'clinic_id'),
      patientId: _positiveInt(json['patient_id'], 'patient_id'),
      ruleId: _positiveInt(json['rule_id'], 'rule_id'),
      ruleVersion: _positiveInt(json['rule_version'], 'rule_version'),
      severity: ClinicalAlertSeverity.fromJson(json['severity']),
      action: ClinicalAlertAction.fromJson(json['action']),
      status: ClinicalAlertStatus.fromJson(json['status']),
      title: _requiredText(json['title'], 'title'),
      message: _requiredText(json['message'], 'message'),
      context: Map<String, dynamic>.unmodifiable(context),
      sourceType: _requiredText(json['source_type'], 'source_type'),
      sourceId: _optionalPositiveInt(json['source_id'], 'source_id'),
      deduplicationKey: _optionalText(
        json['deduplication_key'],
        'deduplication_key',
      ),
      generatedAt: _requiredTimestamp(json['generated_at'], 'generated_at'),
      resolvedAt: _optionalTimestamp(json['resolved_at'], 'resolved_at'),
      createdAt: _requiredTimestamp(json['created_at'], 'created_at'),
      updatedAt: _requiredTimestamp(json['updated_at'], 'updated_at'),
    );
  }

  static int _positiveInt(Object? value, String field) {
    if (value is! int || value <= 0) {
      throw FormatException('Clinical alert $field must be a positive int.');
    }
    return value;
  }

  static int? _optionalPositiveInt(Object? value, String field) {
    if (value == null) return null;
    return _positiveInt(value, field);
  }

  static String _requiredText(Object? value, String field) {
    if (value is! String || value.trim().isEmpty) {
      throw FormatException('Clinical alert $field must be nonempty text.');
    }
    return value;
  }

  static String? _optionalText(Object? value, String field) {
    if (value == null) return null;
    if (value is! String) {
      throw FormatException('Clinical alert $field must be text or null.');
    }
    return value;
  }

  static DateTime _requiredTimestamp(Object? value, String field) {
    final parsed = _optionalTimestamp(value, field);
    if (parsed == null) {
      throw FormatException('Clinical alert $field must not be null.');
    }
    return parsed;
  }

  static DateTime? _optionalTimestamp(Object? value, String field) {
    if (value == null) return null;
    if (value is! String) {
      throw FormatException('Clinical alert $field must be a timestamp.');
    }
    final parsed = DateTime.tryParse(value);
    if (parsed == null) {
      throw FormatException('Clinical alert $field has an invalid timestamp.');
    }
    return parsed.toUtc();
  }
}
