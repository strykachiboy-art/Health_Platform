/// Strict, patient/admission/clinic-bound representation of one server order.
/// It is kept in memory only. No clinical-order PHI is cached locally.
class ImagingProcedureOrder {
  const ImagingProcedureOrder({
    required this.kind,
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
    required this.orderedByStaffId,
    required this.name,
    required this.category,
    required this.indication,
    required this.status,
    required this.priority,
    required this.version,
    required this.orderedAt,
    required this.consentRequired,
    required this.consentVerifiedAt,
    required this.completedAt,
    required this.cancelledAt,
  });

  static const kinds = <String>{'imaging', 'procedures'};
  static const statuses = <String>{
    'pending',
    'active',
    'in_progress',
    'on_hold',
    'completed',
    'cancelled',
  };
  static const priorities = <String>{'routine', 'urgent', 'stat'};
  static const modalities = <String>{
    'xray',
    'ct',
    'mri',
    'ultrasound',
    'mammography',
    'fluoroscopy',
    'nuclear_medicine',
    'other',
  };
  static const procedureTypes = <String>{
    'diagnostic',
    'therapeutic',
    'surgical',
    'bedside',
    'interventional',
    'other',
  };

  final String kind;
  final int id;
  final int clinicId;
  final int patientId;
  final int admissionId;
  final int orderedByStaffId;
  final String name;
  final String category;
  final String indication;
  final String status;
  final String priority;
  final int version;
  final String orderedAt;
  final bool consentRequired;
  final String? consentVerifiedAt;
  final String? completedAt;
  final String? cancelledAt;

  bool get isOpen => status != 'completed' && status != 'cancelled';

  static int _positive(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is! int || value <= 0) {
      throw FormatException('Invalid clinical order field: $field');
    }
    return value;
  }

  static String _text(Map<String, dynamic> json, String field, int max) {
    final value = json[field];
    if (value is! String ||
        value.isEmpty ||
        value.trim() != value ||
        value.length > max) {
      throw FormatException('Invalid clinical order field: $field');
    }
    return value;
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

  factory ImagingProcedureOrder.fromJson(
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
    final clinicId = _positive(json, 'clinic_id');
    final patientId = _positive(json, 'patient_id');
    final admissionId = _positive(json, 'admission_id');
    if (clinicId != expectedClinicId ||
        patientId != expectedPatientId ||
        admissionId != expectedAdmissionId ||
        (expectedId != null && expectedId != id)) {
      throw const FormatException('Clinical order identity or scope mismatch.');
    }
    final status = _text(json, 'status', 32);
    final priority = _text(json, 'priority', 32);
    if (!statuses.contains(status) || !priorities.contains(priority)) {
      throw const FormatException('Unknown clinical order status or priority.');
    }
    final completedAt = _timestamp(json, 'completed_at');
    final cancelledAt = _timestamp(json, 'cancelled_at');
    if ((status == 'completed' &&
            (completedAt == null || cancelledAt != null)) ||
        (status == 'cancelled' &&
            (cancelledAt == null || completedAt != null)) ||
        (status != 'completed' &&
            status != 'cancelled' &&
            (completedAt != null || cancelledAt != null))) {
      throw const FormatException('Inconsistent clinical order lifecycle.');
    }
    final category = _text(
      json,
      kind == 'imaging' ? 'modality' : 'procedure_type',
      32,
    );
    if (!(kind == 'imaging' ? modalities : procedureTypes).contains(category)) {
      throw const FormatException(
        'Unknown imaging modality or procedure type.',
      );
    }
    final consent = json['consent_required'];
    if (kind == 'procedures' && consent is! bool) {
      throw const FormatException('Invalid procedure consent flag.');
    }
    final verified = _timestamp(json, 'consent_verified_at');
    if (kind == 'procedures' && consent == false && verified != null) {
      throw const FormatException('Unexpected consent verification.');
    }
    return ImagingProcedureOrder(
      kind: kind,
      id: id,
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admissionId,
      orderedByStaffId: _positive(json, 'ordered_by_staff_id'),
      name: _text(
        json,
        kind == 'imaging' ? 'study_name' : 'procedure_name',
        255,
      ),
      category: category,
      indication: _text(json, 'indication', 10000),
      status: status,
      priority: priority,
      version: _positive(json, 'version'),
      orderedAt: _timestamp(json, 'ordered_at', required: true)!,
      consentRequired: kind == 'procedures' && consent == true,
      consentVerifiedAt: verified,
      completedAt: completedAt,
      cancelledAt: cancelledAt,
    );
  }
}

class ImagingProcedureOrderList {
  const ImagingProcedureOrderList(this.items);
  final List<ImagingProcedureOrder> items;

  factory ImagingProcedureOrderList.fromJson(
    Map<String, dynamic> response, {
    required String kind,
    required int expectedClinicId,
    required int expectedPatientId,
    required int expectedAdmissionId,
  }) {
    final raw = response['items'];
    final total = response['total'];
    if (raw is! List || total is! int || total < 0 || raw.length != total) {
      throw const FormatException('Invalid clinical order list envelope.');
    }
    final items = <ImagingProcedureOrder>[];
    final seen = <int>{};
    for (final value in raw) {
      if (value is! Map<String, dynamic>) {
        throw const FormatException('Invalid clinical order list item.');
      }
      final order = ImagingProcedureOrder.fromJson(
        value,
        kind: kind,
        expectedClinicId: expectedClinicId,
        expectedPatientId: expectedPatientId,
        expectedAdmissionId: expectedAdmissionId,
      );
      if (!seen.add(order.id)) {
        throw const FormatException('Duplicate clinical order ID.');
      }
      items.add(order);
    }
    return ImagingProcedureOrderList(
      List<ImagingProcedureOrder>.unmodifiable(items),
    );
  }
}
