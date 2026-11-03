enum EmergencyAccessStatus {
  requested,
  active,
  denied,
  revoked,
  expired;

  static EmergencyAccessStatus fromJson(Object? value) {
    if (value is! String) {
      throw const FormatException('Emergency access status must be a string.');
    }

    return switch (value) {
      'requested' => EmergencyAccessStatus.requested,
      'active' => EmergencyAccessStatus.active,
      'denied' => EmergencyAccessStatus.denied,
      'revoked' => EmergencyAccessStatus.revoked,
      'expired' => EmergencyAccessStatus.expired,
      _ => throw FormatException('Unsupported emergency access status: $value'),
    };
  }
}

class EmergencyAccessGrant {
  const EmergencyAccessGrant({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.requesterUserId,
    required this.requesterRole,
    required this.reason,
    required this.purpose,
    required this.scope,
    required this.status,
    required this.requestedAt,
    required this.grantedAt,
    required this.expiresAt,
    required this.revokedAt,
    required this.reviewedAt,
    required this.reviewedByUserId,
    required this.reviewNotes,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int requesterUserId;
  final String requesterRole;
  final String reason;
  final String purpose;
  final List<String> scope;
  final EmergencyAccessStatus status;
  final DateTime requestedAt;
  final DateTime? grantedAt;
  final DateTime? expiresAt;
  final DateTime? revokedAt;
  final DateTime? reviewedAt;
  final int? reviewedByUserId;
  final String? reviewNotes;

  factory EmergencyAccessGrant.fromJson(Map<String, dynamic> json) {
    final id = _positiveInt(json['id'], 'id');
    final clinicId = _positiveInt(json['clinic_id'], 'clinic_id');
    final patientId = _positiveInt(json['patient_id'], 'patient_id');
    final requesterUserId = _positiveInt(
      json['requester_user_id'],
      'requester_user_id',
    );

    final requesterRole = _requiredString(
      json['requester_role'],
      'requester_role',
    );
    final reason = _requiredString(json['reason'], 'reason');
    final purpose = _requiredString(json['purpose'], 'purpose');

    final rawScope = json['scope'];
    if (rawScope is! List || rawScope.isEmpty) {
      throw const FormatException(
        'Emergency access scope must be a non-empty list.',
      );
    }

    final scope = <String>[];
    for (final item in rawScope) {
      if (item is! String || item.trim().isEmpty) {
        throw const FormatException(
          'Emergency access scope items must be non-empty strings.',
        );
      }
      scope.add(item);
    }

    final reviewedBy = json['reviewed_by_user_id'];
    final reviewedByUserId = reviewedBy == null
        ? null
        : _positiveInt(reviewedBy, 'reviewed_by_user_id');

    final reviewNotesValue = json['review_notes'];
    if (reviewNotesValue != null && reviewNotesValue is! String) {
      throw const FormatException('review_notes must be a string or null.');
    }

    return EmergencyAccessGrant(
      id: id,
      clinicId: clinicId,
      patientId: patientId,
      requesterUserId: requesterUserId,
      requesterRole: requesterRole,
      reason: reason,
      purpose: purpose,
      scope: List<String>.unmodifiable(scope),
      status: EmergencyAccessStatus.fromJson(json['status']),
      requestedAt: _requiredDateTime(json['requested_at'], 'requested_at'),
      grantedAt: _optionalDateTime(json['granted_at'], 'granted_at'),
      expiresAt: _optionalDateTime(json['expires_at'], 'expires_at'),
      revokedAt: _optionalDateTime(json['revoked_at'], 'revoked_at'),
      reviewedAt: _optionalDateTime(json['reviewed_at'], 'reviewed_at'),
      reviewedByUserId: reviewedByUserId,
      reviewNotes: reviewNotesValue as String?,
    );
  }

  static int _positiveInt(Object? value, String field) {
    if (value is! int || value <= 0) {
      throw FormatException('$field must be a positive integer.');
    }
    return value;
  }

  static String _requiredString(Object? value, String field) {
    if (value is! String || value.trim().isEmpty) {
      throw FormatException('$field must be a non-empty string.');
    }
    return value;
  }

  static DateTime _requiredDateTime(Object? value, String field) {
    final parsed = _optionalDateTime(value, field);
    if (parsed == null) {
      throw FormatException('$field must not be null.');
    }
    return parsed;
  }

  static DateTime? _optionalDateTime(Object? value, String field) {
    if (value == null) return null;
    if (value is! String) {
      throw FormatException('$field must be an ISO-8601 string or null.');
    }

    final parsed = DateTime.tryParse(value);
    if (parsed == null) {
      throw FormatException('$field must be a valid ISO-8601 timestamp.');
    }

    return parsed.toUtc();
  }
}
