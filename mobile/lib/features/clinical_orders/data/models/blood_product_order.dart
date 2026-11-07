/// A backend-owned blood-product order. Never persist this record locally.
/// This model validates scope and workflow state but does not determine whether
/// blood is compatible with a patient. That decision belongs to laboratory staff.
class BloodProductOrder {
  const BloodProductOrder({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
    required this.orderedByStaffId,
    required this.productType,
    required this.unitsRequested,
    required this.unitsIssued,
    required this.indication,
    required this.priority,
    required this.status,
    required this.compatibilityStatus,
    required this.patientBloodTypeAtOrder,
    required this.patientBloodTypeVerified,
    required this.productBloodType,
    required this.crossmatchReference,
    required this.emergencyReleaseReason,
    required this.compatibilityVerifiedAt,
    required this.version,
    required this.orderedAt,
    required this.completedAt,
    required this.cancelledAt,
  });

  static const productTypes = <String>{
    'packed_red_blood_cells',
    'whole_blood',
    'platelets',
    'fresh_frozen_plasma',
    'cryoprecipitate',
  };
  static const bloodTypes = <String>{
    'A+',
    'A-',
    'B+',
    'B-',
    'AB+',
    'AB-',
    'O+',
    'O-',
    'unknown',
  };
  static const statuses = <String>{
    'pending',
    'active',
    'in_progress',
    'on_hold',
    'completed',
    'cancelled',
  };
  static const priorities = <String>{'routine', 'urgent', 'stat'};
  static const compatibilityStates = <String>{
    'pending',
    'compatible',
    'emergency_release',
  };

  final int id;
  final int clinicId;
  final int patientId;
  final int admissionId;
  final int orderedByStaffId;
  final String productType;
  final int unitsRequested;
  final int unitsIssued;
  final String indication;
  final String priority;
  final String status;
  final String compatibilityStatus;
  final String patientBloodTypeAtOrder;
  final String? patientBloodTypeVerified;
  final String? productBloodType;
  final String? crossmatchReference;
  final String? emergencyReleaseReason;
  final String? compatibilityVerifiedAt;
  final int version;
  final String orderedAt;
  final String? completedAt;
  final String? cancelledAt;

  bool get isOpen => status != 'completed' && status != 'cancelled';
  bool get canVerify => isOpen && compatibilityStatus == 'pending';
  bool get canFulfill => isOpen && compatibilityStatus != 'pending';

  static int _int(Map<String, dynamic> json, String key, {int min = 1}) {
    final v = json[key];
    if (v is! int || v < min || v > 2147483647) {
      throw FormatException('Invalid blood-product field: $key');
    }
    return v;
  }

  static int? _optionalInt(Map<String, dynamic> json, String key) {
    return json[key] == null ? null : _int(json, key);
  }

  static String _text(Map<String, dynamic> json, String key, int max) {
    final v = json[key];
    if (v is! String || v.trim().isEmpty || v != v.trim() || v.length > max) {
      throw FormatException('Invalid blood-product field: $key');
    }
    return v;
  }

  static String? _optionalText(Map<String, dynamic> json, String key, int max) {
    return json[key] == null ? null : _text(json, key, max);
  }

  static String? _time(
    Map<String, dynamic> json,
    String key, {
    bool required = false,
  }) {
    final value = json[key];
    if (value == null && !required) return null;
    if (value is! String ||
        !RegExp(
          r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$',
        ).hasMatch(value) ||
        DateTime.tryParse(value) == null) {
      throw FormatException('Invalid blood-product timestamp: $key');
    }
    return value;
  }

  factory BloodProductOrder.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    int? expectedPatientId,
    int? expectedAdmissionId,
    int? expectedId,
  }) {
    final id = _int(json, 'id');
    final clinic = _int(json, 'clinic_id');
    final patient = _int(json, 'patient_id');
    final admission = _int(json, 'admission_id');
    if (clinic != expectedClinicId ||
        (expectedPatientId != null && patient != expectedPatientId) ||
        (expectedAdmissionId != null && admission != expectedAdmissionId) ||
        (expectedId != null && id != expectedId)) {
      throw const FormatException('Blood-product identity or scope mismatch.');
    }
    final type = _text(json, 'product_type', 40);
    final units = _int(json, 'units_requested');
    final issued = _int(json, 'units_issued', min: 0);
    final priority = _text(json, 'priority', 32);
    final status = _text(json, 'status', 32);
    final compatibility = _text(json, 'compatibility_status', 32);
    if (!productTypes.contains(type) ||
        units > 100 ||
        issued > units ||
        !priorities.contains(priority) ||
        !statuses.contains(status) ||
        !compatibilityStates.contains(compatibility)) {
      throw const FormatException('Invalid blood-product order state.');
    }
    final atOrder = _text(json, 'patient_blood_type_at_order', 16);
    final verifiedType = _optionalText(json, 'patient_blood_type_verified', 16);
    final productBloodType = _optionalText(json, 'product_blood_type', 16);
    if (!bloodTypes.contains(atOrder) ||
        (verifiedType != null && !bloodTypes.contains(verifiedType)) ||
        (productBloodType != null && !bloodTypes.contains(productBloodType))) {
      throw const FormatException('Unrecognized recorded blood type.');
    }
    final verifiedAt = _time(json, 'compatibility_verified_at');
    final verifier = _optionalInt(json, 'compatibility_verified_by_staff_id');
    final crossmatch = _optionalText(json, 'crossmatch_reference', 128);
    final reason = _optionalText(json, 'emergency_release_reason', 10000);
    if (compatibility == 'pending') {
      if (verifiedAt != null ||
          verifier != null ||
          verifiedType != null ||
          crossmatch != null ||
          reason != null ||
          issued != 0) {
        throw const FormatException(
          'Pending blood compatibility was modified.',
        );
      }
    } else if (verifiedAt == null ||
        verifier == null ||
        verifiedType == null ||
        (compatibility == 'compatible' &&
            (crossmatch == null ||
                reason != null ||
                verifiedType == 'unknown')) ||
        (compatibility == 'emergency_release' && reason == null)) {
      throw const FormatException('Unverified blood compatibility state.');
    }
    final completedAt = _time(json, 'completed_at');
    final cancelledAt = _time(json, 'cancelled_at');
    final fulfilledBy = _optionalInt(json, 'fulfilled_by_staff_id');
    final cancelledBy = _optionalInt(json, 'cancelled_by_staff_id');
    if ((status == 'completed' &&
            (completedAt == null ||
                cancelledAt != null ||
                fulfilledBy == null ||
                compatibility == 'pending' ||
                issued == 0)) ||
        (status == 'cancelled' &&
            (cancelledAt == null ||
                completedAt != null ||
                cancelledBy == null ||
                issued != 0)) ||
        (status != 'completed' &&
            status != 'cancelled' &&
            (completedAt != null || cancelledAt != null || issued != 0))) {
      throw const FormatException('Inconsistent blood-product lifecycle.');
    }
    return BloodProductOrder(
      id: id,
      clinicId: clinic,
      patientId: patient,
      admissionId: admission,
      orderedByStaffId: _int(json, 'ordered_by_staff_id'),
      productType: type,
      unitsRequested: units,
      unitsIssued: issued,
      indication: _text(json, 'indication', 10000),
      priority: priority,
      status: status,
      compatibilityStatus: compatibility,
      patientBloodTypeAtOrder: atOrder,
      patientBloodTypeVerified: verifiedType,
      productBloodType: productBloodType,
      crossmatchReference: crossmatch,
      emergencyReleaseReason: reason,
      compatibilityVerifiedAt: verifiedAt,
      version: _int(json, 'version'),
      orderedAt: _time(json, 'ordered_at', required: true)!,
      completedAt: completedAt,
      cancelledAt: cancelledAt,
    );
  }
}

class BloodProductOrderList {
  const BloodProductOrderList(this.items);
  final List<BloodProductOrder> items;

  factory BloodProductOrderList.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    required int expectedPatientId,
    required int expectedAdmissionId,
  }) {
    final raw = json['items'];
    final total = json['total'];
    if (raw is! List || total is! int || total != raw.length || total < 0) {
      throw const FormatException('Invalid blood-product list envelope.');
    }
    final seen = <int>{};
    final items = <BloodProductOrder>[];
    for (final value in raw) {
      if (value is! Map<String, dynamic>) {
        throw const FormatException('Invalid blood-product list row.');
      }
      final order = BloodProductOrder.fromJson(
        value,
        expectedClinicId: expectedClinicId,
        expectedPatientId: expectedPatientId,
        expectedAdmissionId: expectedAdmissionId,
      );
      if (!seen.add(order.id)) {
        throw const FormatException('Duplicate blood-product order ID.');
      }
      items.add(order);
    }
    return BloodProductOrderList(List<BloodProductOrder>.unmodifiable(items));
  }
}
