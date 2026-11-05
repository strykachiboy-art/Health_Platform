/// Backend-owned acknowledgement outcome. This is an audit record, not proof
/// that the alert is resolved or that a blocked clinical action is permitted.
enum ClinicalAlertAcknowledgementType {
  acknowledged,
  overridden;

  String get apiValue => switch (this) {
    ClinicalAlertAcknowledgementType.acknowledged => 'acknowledged',
    ClinicalAlertAcknowledgementType.overridden => 'overridden',
  };

  static ClinicalAlertAcknowledgementType fromJson(Object? value) =>
      switch (value) {
        'acknowledged' => ClinicalAlertAcknowledgementType.acknowledged,
        'overridden' => ClinicalAlertAcknowledgementType.overridden,
        _ => throw const FormatException(
          'Invalid clinical alert acknowledgement type.',
        ),
      };
}

/// Strictly parsed representation of AlertAcknowledgementResponseSchema.
class ClinicalAlertAcknowledgement {
  const ClinicalAlertAcknowledgement({
    required this.id,
    required this.alertId,
    required this.acknowledgedByUserId,
    required this.type,
    required this.justification,
    required this.acknowledgedAt,
    required this.createdAt,
    required this.updatedAt,
  });

  final int id;
  final int alertId;
  final int acknowledgedByUserId;
  final ClinicalAlertAcknowledgementType type;
  final String? justification;
  final DateTime acknowledgedAt;
  final DateTime createdAt;
  final DateTime updatedAt;

  factory ClinicalAlertAcknowledgement.fromJson(Map<String, dynamic> json) {
    final justification = json['justification'];
    if (justification != null && justification is! String) {
      throw const FormatException(
        'Clinical acknowledgement justification must be text or null.',
      );
    }

    return ClinicalAlertAcknowledgement(
      id: _positiveInt(json['id'], 'id'),
      alertId: _positiveInt(json['alert_id'], 'alert_id'),
      acknowledgedByUserId: _positiveInt(
        json['acknowledged_by_user_id'],
        'acknowledged_by_user_id',
      ),
      type: ClinicalAlertAcknowledgementType.fromJson(
        json['acknowledgement_type'],
      ),
      justification: justification as String?,
      acknowledgedAt: _timestamp(json['acknowledged_at'], 'acknowledged_at'),
      createdAt: _timestamp(json['created_at'], 'created_at'),
      updatedAt: _timestamp(json['updated_at'], 'updated_at'),
    );
  }

  static int _positiveInt(Object? value, String field) {
    if (value is! int || value <= 0) {
      throw FormatException('Clinical acknowledgement $field is invalid.');
    }
    return value;
  }

  static DateTime _timestamp(Object? value, String field) {
    if (value is! String) {
      throw FormatException('Clinical acknowledgement $field is invalid.');
    }
    final parsed = DateTime.tryParse(value);
    if (parsed == null) {
      throw FormatException('Clinical acknowledgement $field is invalid.');
    }
    return parsed.toUtc();
  }
}
