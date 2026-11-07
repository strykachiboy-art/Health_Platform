/// Server-owned diet and nursing orders. No local persistence or PHI caching.
class DietNursingOrder {
  const DietNursingOrder({
    required this.kind,
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
    required this.orderedByStaffId,
    required this.category,
    required this.description,
    required this.status,
    required this.priority,
    required this.version,
    required this.orderedAt,
    required this.completedAt,
    required this.cancelledAt,
    this.feedingRoute,
    this.frequency,
    this.customFrequency,
    this.assignedToStaffId,
  });

  static const kinds = <String>{'diets', 'nursing-orders'};
  static const statuses = <String>{
    'pending',
    'active',
    'in_progress',
    'on_hold',
    'completed',
    'cancelled',
  };
  static const priorities = <String>{'routine', 'urgent', 'stat'};
  static const dietTypes = <String>{
    'regular',
    'clear_liquid',
    'full_liquid',
    'soft',
    'diabetic',
    'renal',
    'cardiac',
    'low_sodium',
    'high_protein',
    'npo',
    'enteral',
    'custom',
  };
  static const feedingRoutes = <String>{'oral', 'enteral', 'parenteral'};
  static const nursingCategories = <String>{
    'monitoring',
    'activity',
    'positioning',
    'wound_care',
    'intake_output',
    'safety',
    'specimen',
    'respiratory',
    'catheter',
    'hygiene',
    'communication',
    'other',
  };
  static const frequencies = <String>{
    'once',
    'q1h',
    'q2h',
    'q4h',
    'q6h',
    'q8h',
    'q12h',
    'daily',
    'prn',
    'continuous',
    'custom',
  };

  final String kind;
  final int id;
  final int clinicId;
  final int patientId;
  final int admissionId;
  final int orderedByStaffId;
  final String category;
  final String? description;
  final String status;
  final String priority;
  final int version;
  final String orderedAt;
  final String? completedAt;
  final String? cancelledAt;
  final String? feedingRoute;
  final String? frequency;
  final String? customFrequency;
  final int? assignedToStaffId;

  bool get isOpen => status != 'completed' && status != 'cancelled';
  bool get canDoctorComplete =>
      isOpen && (kind == 'diets' || frequency != 'once');
  bool get canExecute =>
      kind == 'nursing-orders' &&
      (status == 'active' || status == 'in_progress');

  static int _positive(Map<String, dynamic> json, String field) {
    final v = json[field];
    if (v is! int || v <= 0 || v > 2147483647) {
      throw FormatException('Invalid clinical order field: $field');
    }
    return v;
  }

  static int? _optionalPositive(Map<String, dynamic> json, String field) {
    if (json[field] == null) {
      return null;
    }
    return _positive(json, field);
  }

  static String _requiredText(
    Map<String, dynamic> json,
    String field,
    int max,
  ) {
    final v = json[field];
    if (v is! String || v.trim().isEmpty || v.trim() != v || v.length > max) {
      throw FormatException('Invalid clinical order field: $field');
    }
    return v;
  }

  static String? _optionalText(
    Map<String, dynamic> json,
    String field,
    int max,
  ) {
    final v = json[field];
    if (v == null) {
      return null;
    }
    return _requiredText(json, field, max);
  }

  static String? _time(
    Map<String, dynamic> json,
    String field, {
    bool required = false,
  }) {
    final v = json[field];
    if (v == null && !required) {
      return null;
    }
    if (v is! String ||
        !RegExp(
          r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$',
        ).hasMatch(v) ||
        DateTime.tryParse(v) == null) {
      throw FormatException('Invalid clinical order timestamp: $field');
    }
    return v;
  }

  factory DietNursingOrder.fromJson(
    Map<String, dynamic> json, {
    required String kind,
    required int expectedClinicId,
    required int expectedPatientId,
    required int expectedAdmissionId,
    int? expectedId,
  }) {
    if (!kinds.contains(kind)) {
      throw ArgumentError.value(kind, 'kind');
    }
    final id = _positive(json, 'id');
    final clinic = _positive(json, 'clinic_id');
    final patient = _positive(json, 'patient_id');
    final admission = _positive(json, 'admission_id');
    if (clinic != expectedClinicId ||
        patient != expectedPatientId ||
        admission != expectedAdmissionId ||
        (expectedId != null && id != expectedId)) {
      throw const FormatException('Clinical order scope or identity mismatch.');
    }
    final status = _requiredText(json, 'status', 32);
    final priority = _requiredText(json, 'priority', 32);
    if (!statuses.contains(status) || !priorities.contains(priority)) {
      throw const FormatException(
        'Unknown clinical order lifecycle or priority.',
      );
    }
    final completedAt = _time(json, 'completed_at');
    final cancelledAt = _time(json, 'cancelled_at');
    if ((status == 'completed' &&
            (completedAt == null || cancelledAt != null)) ||
        (status == 'cancelled' &&
            (cancelledAt == null || completedAt != null)) ||
        (status != 'completed' &&
            status != 'cancelled' &&
            (completedAt != null || cancelledAt != null))) {
      throw const FormatException(
        'Inconsistent clinical order terminal timestamps.',
      );
    }
    final category = _requiredText(
      json,
      kind == 'diets' ? 'diet_type' : 'category',
      32,
    );
    if (!(kind == 'diets' ? dietTypes : nursingCategories).contains(category)) {
      throw const FormatException('Invalid diet type or nursing category.');
    }
    String? description;
    String? feedingRoute;
    String? frequency;
    String? customFrequency;
    int? assigned;
    if (kind == 'diets') {
      description = _optionalText(json, 'custom_description', 1000);
      feedingRoute = _optionalText(json, 'feeding_route', 24);
      if (category == 'custom' && description == null) {
        throw const FormatException('Custom diet requires description.');
      }
      if (category == 'npo') {
        if (feedingRoute != null) {
          throw const FormatException('NPO diet cannot have a feeding route.');
        }
      } else if (feedingRoute == null ||
          !feedingRoutes.contains(feedingRoute)) {
        throw const FormatException('Invalid non-NPO feeding route.');
      }
    } else {
      description = _requiredText(json, 'order_text', 10000);
      frequency = _requiredText(json, 'frequency', 24);
      customFrequency = _optionalText(json, 'custom_frequency', 255);
      assigned = _optionalPositive(json, 'assigned_to_staff_id');
      if (!frequencies.contains(frequency) ||
          (frequency == 'custom') != (customFrequency != null)) {
        throw const FormatException('Invalid nursing order frequency.');
      }
    }
    return DietNursingOrder(
      kind: kind,
      id: id,
      clinicId: clinic,
      patientId: patient,
      admissionId: admission,
      orderedByStaffId: _positive(json, 'ordered_by_staff_id'),
      category: category,
      description: description,
      feedingRoute: feedingRoute,
      frequency: frequency,
      customFrequency: customFrequency,
      assignedToStaffId: assigned,
      status: status,
      priority: priority,
      version: _positive(json, 'version'),
      orderedAt: _time(json, 'ordered_at', required: true)!,
      completedAt: completedAt,
      cancelledAt: cancelledAt,
    );
  }
}

class DietNursingOrderList {
  const DietNursingOrderList(this.items);
  final List<DietNursingOrder> items;

  factory DietNursingOrderList.fromJson(
    Map<String, dynamic> json, {
    required String kind,
    required int expectedClinicId,
    required int expectedPatientId,
    required int expectedAdmissionId,
  }) {
    final raw = json['items'];
    final total = json['total'];
    if (raw is! List || total is! int || raw.length != total || total < 0) {
      throw const FormatException('Invalid diet/nursing list envelope.');
    }
    final seen = <int>{};
    final orders = <DietNursingOrder>[];
    for (final entry in raw) {
      if (entry is! Map<String, dynamic>) {
        throw const FormatException('Invalid diet/nursing list entry.');
      }
      final order = DietNursingOrder.fromJson(
        entry,
        kind: kind,
        expectedClinicId: expectedClinicId,
        expectedPatientId: expectedPatientId,
        expectedAdmissionId: expectedAdmissionId,
      );
      if (!seen.add(order.id)) {
        throw const FormatException('Duplicate clinical order ID.');
      }
      orders.add(order);
    }
    return DietNursingOrderList(List<DietNursingOrder>.unmodifiable(orders));
  }
}

class NursingOrderExecution {
  const NursingOrderExecution({
    required this.id,
    required this.orderId,
    required this.performedByStaffId,
    required this.performedAt,
    required this.resultNote,
  });
  final int id;
  final int orderId;
  final int performedByStaffId;
  final String performedAt;
  final String resultNote;

  factory NursingOrderExecution.fromJson(
    Map<String, dynamic> json, {
    required int expectedOrderId,
    required int expectedClinicId,
    required int expectedPatientId,
    required int expectedAdmissionId,
  }) {
    final orderId = DietNursingOrder._positive(json, 'nursing_order_id');
    if (orderId != expectedOrderId ||
        DietNursingOrder._positive(json, 'clinic_id') != expectedClinicId ||
        DietNursingOrder._positive(json, 'patient_id') != expectedPatientId ||
        DietNursingOrder._positive(json, 'admission_id') !=
            expectedAdmissionId) {
      throw const FormatException('Nursing execution scope mismatch.');
    }
    return NursingOrderExecution(
      id: DietNursingOrder._positive(json, 'id'),
      orderId: orderId,
      performedByStaffId: DietNursingOrder._positive(
        json,
        'performed_by_staff_id',
      ),
      performedAt: DietNursingOrder._time(
        json,
        'performed_at',
        required: true,
      )!,
      resultNote: DietNursingOrder._requiredText(json, 'result_note', 10000),
    );
  }
}

class NursingOrderExecutionList {
  const NursingOrderExecutionList(this.items);
  final List<NursingOrderExecution> items;

  factory NursingOrderExecutionList.fromJson(
    Map<String, dynamic> json, {
    required int expectedOrderId,
    required int expectedClinicId,
    required int expectedPatientId,
    required int expectedAdmissionId,
  }) {
    final raw = json['items'];
    final total = json['total'];
    if (raw is! List || total is! int || total < 0 || raw.length != total) {
      throw const FormatException('Invalid nursing execution list.');
    }
    final seen = <int>{};
    final items = <NursingOrderExecution>[];
    for (final entry in raw) {
      if (entry is! Map<String, dynamic>) {
        throw const FormatException('Invalid nursing execution item.');
      }
      final execution = NursingOrderExecution.fromJson(
        entry,
        expectedOrderId: expectedOrderId,
        expectedClinicId: expectedClinicId,
        expectedPatientId: expectedPatientId,
        expectedAdmissionId: expectedAdmissionId,
      );
      if (!seen.add(execution.id)) {
        throw const FormatException('Duplicate nursing execution ID.');
      }
      items.add(execution);
    }
    return NursingOrderExecutionList(
      List<NursingOrderExecution>.unmodifiable(items),
    );
  }
}
