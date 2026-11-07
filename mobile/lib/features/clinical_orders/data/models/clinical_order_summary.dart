/// One server-owned clinical order shown in the read-only unified timeline.
/// The response is never written to an offline database or operation queue.
class ClinicalOrderSummary {
  const ClinicalOrderSummary({
    required this.orderType,
    required this.sourceEntityType,
    required this.sourceEntityId,
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
    required this.consultationId,
    required this.orderedByStaffId,
    required this.orderedAt,
    required this.priority,
    required this.status,
    required this.sourceStatus,
    required this.displayName,
    required this.instructions,
    required this.scheduledAt,
    required this.dueAt,
    required this.completedAt,
    required this.cancelledAt,
  });

  static const allowedTypes = <String>{
    'medication',
    'laboratory',
    'imaging',
    'procedure',
    'blood_product',
    'diet',
    'nursing',
  };
  static const allowedStatuses = <String>{
    'pending',
    'active',
    'in_progress',
    'on_hold',
    'completed',
    'cancelled',
  };
  static const allowedPriorities = <String>{'routine', 'urgent', 'stat'};

  final String orderType;
  final String sourceEntityType;
  final int sourceEntityId;
  final int clinicId;
  final int patientId;
  final int? admissionId;
  final int? consultationId;
  final int orderedByStaffId;
  final String orderedAt;
  final String priority;
  final String status;
  final String sourceStatus;
  final String displayName;
  final String? instructions;
  final String? scheduledAt;
  final String? dueAt;
  final String? completedAt;
  final String? cancelledAt;

  static int _positive(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is! int || value <= 0) {
      throw FormatException('Invalid clinical order ID: $field');
    }
    return value;
  }

  static int? _optionalPositive(Map<String, dynamic> json, String field) {
    if (json[field] == null) {
      return null;
    }
    return _positive(json, field);
  }

  static String _requiredString(
    Map<String, dynamic> json,
    String field,
    int limit,
  ) {
    final value = json[field];
    if (value is! String ||
        value.isEmpty ||
        value.trim() != value ||
        value.length > limit) {
      throw FormatException('Invalid clinical order text: $field');
    }
    return value;
  }

  static String? _optionalText(
    Map<String, dynamic> json,
    String field,
    int limit,
  ) {
    if (json[field] == null) {
      return null;
    }
    return _requiredString(json, field, limit);
  }

  static String? _timestamp(
    Map<String, dynamic> json,
    String field, {
    bool required = false,
  }) {
    final value = json[field];
    if (value == null && !required) {
      return null;
    }
    if (value is! String ||
        !RegExp(
          r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$',
        ).hasMatch(value) ||
        DateTime.tryParse(value) == null) {
      throw FormatException('Invalid clinical order timestamp: $field');
    }
    return value;
  }

  factory ClinicalOrderSummary.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    required int expectedPatientId,
  }) {
    final clinicId = _positive(json, 'clinic_id');
    final patientId = _positive(json, 'patient_id');
    if (clinicId != expectedClinicId || patientId != expectedPatientId) {
      throw const FormatException(
        'Clinical order clinic/patient scope mismatch.',
      );
    }
    final type = _requiredString(json, 'order_type', 32);
    final status = _requiredString(json, 'status', 32);
    final priority = _requiredString(json, 'priority', 32);
    if (!allowedTypes.contains(type) ||
        !allowedStatuses.contains(status) ||
        !allowedPriorities.contains(priority)) {
      throw const FormatException(
        'Unknown clinical order type, status or priority.',
      );
    }
    final completed = _timestamp(json, 'completed_at');
    final cancelled = _timestamp(json, 'cancelled_at');
    if ((status == 'completed' && (completed == null || cancelled != null)) ||
        (status == 'cancelled' && (cancelled == null || completed != null)) ||
        (status != 'completed' &&
            status != 'cancelled' &&
            (completed != null || cancelled != null))) {
      throw const FormatException('Inconsistent clinical order lifecycle.');
    }
    final admissionId = _optionalPositive(json, 'admission_id');
    if (const <String>{
          'imaging',
          'procedure',
          'blood_product',
          'diet',
          'nursing',
        }.contains(type) &&
        admissionId == null) {
      throw const FormatException(
        'Admission-scoped clinical order has no admission.',
      );
    }
    return ClinicalOrderSummary(
      orderType: type,
      sourceEntityType: _requiredString(json, 'source_entity_type', 100),
      sourceEntityId: _positive(json, 'source_entity_id'),
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admissionId,
      consultationId: _optionalPositive(json, 'consultation_id'),
      orderedByStaffId: _positive(json, 'ordered_by_staff_id'),
      orderedAt: _timestamp(json, 'ordered_at', required: true)!,
      priority: priority,
      status: status,
      sourceStatus: _requiredString(json, 'source_status', 64),
      displayName: _requiredString(json, 'display_name', 255),
      instructions: _optionalText(json, 'instructions', 4000),
      scheduledAt: _timestamp(json, 'scheduled_at'),
      dueAt: _timestamp(json, 'due_at'),
      completedAt: completed,
      cancelledAt: cancelled,
    );
  }
}

class ClinicalOrderTimeline {
  const ClinicalOrderTimeline(this.items, this.total);
  final List<ClinicalOrderSummary> items;
  final int total;

  factory ClinicalOrderTimeline.fromJson(
    Map<String, dynamic> response, {
    required int expectedClinicId,
    required int expectedPatientId,
    String? type,
    String? status,
  }) {
    if (response['items'] is! List || response['total'] is! int) {
      throw const FormatException('Invalid clinical order list envelope.');
    }
    final raw = response['items'] as List;
    final total = response['total'] as int;
    if (total < 0 || raw.length != total) {
      throw const FormatException('Inconsistent clinical order total.');
    }
    final seen = <String>{};
    final parsed = <ClinicalOrderSummary>[];
    for (final item in raw) {
      if (item is! Map<String, dynamic>) {
        throw const FormatException('Invalid clinical order row.');
      }
      final order = ClinicalOrderSummary.fromJson(
        item,
        expectedClinicId: expectedClinicId,
        expectedPatientId: expectedPatientId,
      );
      if ((type != null && order.orderType != type) ||
          (status != null && order.status != status) ||
          !seen.add('${order.orderType}:${order.sourceEntityId}')) {
        throw const FormatException('Clinical order filter/identity mismatch.');
      }
      parsed.add(order);
    }
    return ClinicalOrderTimeline(
      List<ClinicalOrderSummary>.unmodifiable(parsed),
      total,
    );
  }
}
