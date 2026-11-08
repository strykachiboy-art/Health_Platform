/// Strict server-owned emergency blood issue and laboratory follow-up read model.
/// This is not compatibility clearance and does not record administration.
class EmergencyBloodTrace {
  const EmergencyBloodTrace({
    required this.requestedByStaffId,
    required this.requestedAt,
    required this.reviewOutcome,
    required this.reviewedByStaffId,
    required this.issuedUnits,
    required this.labFollowups,
  });

  final int requestedByStaffId;
  final String requestedAt;
  final String? reviewOutcome;
  final int? reviewedByStaffId;
  final List<EmergencyIssuedUnit> issuedUnits;
  final List<EmergencyLabFollowup> labFollowups;

  int get pendingCount =>
      labFollowups.where((item) => item.status == 'pending').length;
  int get recordedCount =>
      labFollowups.where((item) => item.status == 'evidence_recorded').length;
  int get escalatedCount => labFollowups
      .where((item) => item.status == 'discrepancy_escalated')
      .length;

  static int _id(Map<String, dynamic> json, String name) {
    final value = json[name];
    if (value is! int || value <= 0 || value > 2147483647) {
      throw FormatException('Invalid emergency trace field: $name');
    }
    return value;
  }

  static int? _optionalId(Map<String, dynamic> json, String name) =>
      json[name] == null ? null : _id(json, name);

  static String _string(Map<String, dynamic> json, String name, int max) {
    final value = json[name];
    if (value is! String ||
        value.isEmpty ||
        value.trim() != value ||
        value.length > max) {
      throw FormatException('Invalid emergency trace field: $name');
    }
    return value;
  }

  static String? _optionalString(
    Map<String, dynamic> json,
    String name,
    int max,
  ) => json[name] == null ? null : _string(json, name, max);

  static String _time(Map<String, dynamic> json, String name) {
    final value = _string(json, name, 60);
    if (!RegExp(
          r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$',
        ).hasMatch(value) ||
        DateTime.tryParse(value) == null) {
      throw FormatException('Invalid emergency trace timestamp: $name');
    }
    return value;
  }

  static String? _optionalTime(Map<String, dynamic> json, String name) =>
      json[name] == null ? null : _time(json, name);

  static List<Map<String, dynamic>> _objects(
    Map<String, dynamic> json,
    String name,
  ) {
    final value = json[name];
    if (value is! List || value.length > 100) {
      throw FormatException('Invalid emergency trace list: $name');
    }
    final result = <Map<String, dynamic>>[];
    for (final item in value) {
      if (item is! Map<String, dynamic>) {
        throw FormatException('Invalid emergency trace item: $name');
      }
      result.add(item);
    }
    return result;
  }

  /// Null only for old response contracts with none of these fields.
  /// Incomplete or conflicting partial evidence fails closed.
  static EmergencyBloodTrace? fromOrderJson(
    Map<String, dynamic> json, {
    required int clinicId,
    required int patientId,
    required int admissionId,
    required int orderId,
    required int unitsIssued,
    required String orderStatus,
    required String compatibilityStatus,
  }) {
    const indicators = <String>[
      'emergency_release_requested',
      'emergency_requested_by_staff_id',
      'issued_units',
      'lab_followups',
      'emergency_review_outcome',
    ];
    if (!indicators.any(json.containsKey)) {
      return null; // Legacy data may not supply provenance.
    }
    if (compatibilityStatus != 'emergency_release') {
      final issued = json['issued_units'];
      final followups = json['lab_followups'];
      if (issued is! List ||
          issued.isNotEmpty ||
          followups is! List ||
          followups.isNotEmpty ||
          json['emergency_review_outcome'] != null) {
        throw const FormatException('Emergency evidence on ordinary order.');
      }
      if (json['emergency_release_requested'] == false) return null;
      // Physician intent can exist before lab emergency verification.
      if (compatibilityStatus != 'pending' ||
          json['emergency_release_requested'] != true ||
          unitsIssued != 0) {
        throw const FormatException(
          'Invalid pre-verification emergency intent.',
        );
      }
    }
    if (json['emergency_release_requested'] != true) {
      throw const FormatException('Unsigned emergency order provenance.');
    }
    final requestedBy = _id(json, 'emergency_requested_by_staff_id');
    final requestedAt = _time(json, 'emergency_requested_at');
    _string(json, 'emergency_request_reason', 10000);
    final units = <EmergencyIssuedUnit>[];
    final seenIds = <int>{};
    final seenLabels = <String>{};
    for (final item in _objects(json, 'issued_units')) {
      final unit = EmergencyIssuedUnit._fromJson(
        item,
        clinicId: clinicId,
        patientId: patientId,
        admissionId: admissionId,
        orderId: orderId,
      );
      if (!seenIds.add(unit.id) || !seenLabels.add(unit.identifier)) {
        throw const FormatException('Duplicate emergency-issued unit.');
      }
      units.add(unit);
    }
    final followups = <EmergencyLabFollowup>[];
    final seenUnitIds = <int>{};
    final seenLabItems = <int>{};
    for (final item in _objects(json, 'lab_followups')) {
      final followup = EmergencyLabFollowup._fromJson(
        item,
        clinicId: clinicId,
        patientId: patientId,
        admissionId: admissionId,
        orderId: orderId,
      );
      if (!seenUnitIds.add(followup.issuedUnitId) ||
          !seenIds.contains(followup.issuedUnitId) ||
          (followup.labOrderItemId != null &&
              !seenLabItems.add(followup.labOrderItemId!))) {
        throw const FormatException('Unlinked or reused laboratory evidence.');
      }
      followups.add(followup);
    }
    if (units.length != unitsIssued ||
        followups.length != unitsIssued ||
        (unitsIssued > 0 && orderStatus != 'completed') ||
        (orderStatus == 'completed' && unitsIssued == 0)) {
      throw const FormatException('Incomplete emergency release traceability.');
    }
    String? reviewOutcome;
    int? reviewedBy;
    final rawReview = json['emergency_review_outcome'];
    if (rawReview != null) {
      if (rawReview != 'reviewed' && rawReview != 'escalated') {
        throw const FormatException('Invalid physician review outcome.');
      }
      reviewOutcome = rawReview as String;
      reviewedBy = _id(json, 'emergency_reviewed_by_staff_id');
      _time(json, 'emergency_reviewed_at');
      _string(json, 'emergency_review_note', 10000);
      if (reviewedBy == requestedBy || orderStatus != 'completed') {
        throw const FormatException('Non-independent emergency review.');
      }
    } else if (json['emergency_reviewed_by_staff_id'] != null ||
        json['emergency_reviewed_at'] != null ||
        json['emergency_review_note'] != null) {
      throw const FormatException('Partial physician review evidence.');
    }
    return EmergencyBloodTrace(
      requestedByStaffId: requestedBy,
      requestedAt: requestedAt,
      reviewOutcome: reviewOutcome,
      reviewedByStaffId: reviewedBy,
      issuedUnits: List<EmergencyIssuedUnit>.unmodifiable(units),
      labFollowups: List<EmergencyLabFollowup>.unmodifiable(followups),
    );
  }
}

class EmergencyIssuedUnit {
  const EmergencyIssuedUnit._({required this.id, required this.identifier});
  final int id;
  final String identifier;

  static EmergencyIssuedUnit _fromJson(
    Map<String, dynamic> json, {
    required int clinicId,
    required int patientId,
    required int admissionId,
    required int orderId,
  }) {
    if (EmergencyBloodTrace._id(json, 'clinic_id') != clinicId ||
        EmergencyBloodTrace._id(json, 'patient_id') != patientId ||
        EmergencyBloodTrace._id(json, 'admission_id') != admissionId ||
        EmergencyBloodTrace._id(json, 'order_id') != orderId) {
      throw const FormatException('Foreign emergency-issued unit scope.');
    }
    final label = EmergencyBloodTrace._string(json, 'unit_identifier', 128);
    if (label != label.toUpperCase()) {
      throw const FormatException('Unnormalized emergency unit identifier.');
    }
    EmergencyBloodTrace._string(json, 'product_blood_type', 16);
    EmergencyBloodTrace._id(json, 'issued_by_staff_id');
    EmergencyBloodTrace._time(json, 'issued_at');
    return EmergencyIssuedUnit._(
      id: EmergencyBloodTrace._id(json, 'id'),
      identifier: label,
    );
  }
}

class EmergencyLabFollowup {
  const EmergencyLabFollowup._({
    required this.issuedUnitId,
    required this.status,
    required this.labOrderItemId,
  });

  final int issuedUnitId;
  final String status;
  final int? labOrderItemId;

  static EmergencyLabFollowup _fromJson(
    Map<String, dynamic> json, {
    required int clinicId,
    required int patientId,
    required int admissionId,
    required int orderId,
  }) {
    if (EmergencyBloodTrace._id(json, 'clinic_id') != clinicId ||
        EmergencyBloodTrace._id(json, 'patient_id') != patientId ||
        EmergencyBloodTrace._id(json, 'admission_id') != admissionId ||
        EmergencyBloodTrace._id(json, 'order_id') != orderId) {
      throw const FormatException('Foreign laboratory follow-up scope.');
    }
    EmergencyBloodTrace._id(json, 'id');
    final unitId = EmergencyBloodTrace._id(json, 'issued_unit_id');
    final status = EmergencyBloodTrace._string(json, 'status', 32);
    if (!const <String>{
      'pending',
      'evidence_recorded',
      'discrepancy_escalated',
    }.contains(status)) {
      throw const FormatException('Invalid laboratory follow-up status.');
    }
    final labItem = EmergencyBloodTrace._optionalId(json, 'lab_order_item_id');
    final staff = EmergencyBloodTrace._optionalId(json, 'recorded_by_staff_id');
    final timestamp = EmergencyBloodTrace._optionalTime(json, 'recorded_at');
    final evidence = EmergencyBloodTrace._optionalString(
      json,
      'evidence_note',
      10000,
    );
    final escalation = EmergencyBloodTrace._optionalString(
      json,
      'escalation_note',
      10000,
    );
    if (status == 'pending' &&
        (labItem != null ||
            staff != null ||
            timestamp != null ||
            evidence != null ||
            escalation != null)) {
      throw const FormatException('Pending laboratory evidence was modified.');
    }
    if (status != 'pending' &&
        (labItem == null ||
            staff == null ||
            timestamp == null ||
            evidence == null)) {
      throw const FormatException('Incomplete laboratory evidence.');
    }
    if (status == 'discrepancy_escalated' && escalation == null ||
        status == 'evidence_recorded' && escalation != null) {
      throw const FormatException('Invalid laboratory escalation evidence.');
    }
    return EmergencyLabFollowup._(
      issuedUnitId: unitId,
      status: status,
      labOrderItemId: labItem,
    );
  }
}
