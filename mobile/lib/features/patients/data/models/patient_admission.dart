enum PatientAdmissionStatus { admitted, discharged, transferred }

/// A single backend admission. The response does not include clinic_id.
/// Clinic authorization is enforced by the backend and session boundary.
class PatientAdmission {
  const PatientAdmission({
    required this.id,
    required this.patientId,
    required this.bedId,
    required this.admittedById,
    required this.reservationId,
    required this.status,
    required this.reason,
    required this.admittedAt,
    required this.dischargedAt,
    required this.createdAt,
    required this.updatedAt,
  });

  final int id;
  final int patientId;
  final int bedId;
  final int admittedById;
  final int? reservationId;
  final PatientAdmissionStatus status;
  final String? reason;
  final DateTime? admittedAt;
  final DateTime? dischargedAt;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  bool get isActive => status == PatientAdmissionStatus.admitted;

  factory PatientAdmission.fromJson(
    Map<String, dynamic> json, {
    required int expectedPatientId,
  }) {
    final patientId = _positive(json, 'patient_id');
    if (patientId != expectedPatientId) {
      throw const FormatException('Admission patient scope mismatch.');
    }

    return PatientAdmission(
      id: _positive(json, 'id'),
      patientId: patientId,
      bedId: _positive(json, 'bed_id'),
      admittedById: _positive(json, 'admitted_by_id'),
      reservationId: _optionalPositive(json, 'reservation_id'),
      status: _status(json['status']),
      reason: _optionalString(json, 'reason'),
      admittedAt: _timestamp(json, 'admitted_at'),
      dischargedAt: _timestamp(json, 'discharged_at'),
      createdAt: _timestamp(json, 'created_at'),
      updatedAt: _timestamp(json, 'updated_at'),
    );
  }

  static int _positive(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is! int || value <= 0) {
      throw FormatException('Invalid admission field: $field');
    }
    return value;
  }

  static int? _optionalPositive(Map<String, dynamic> json, String field) {
    if (json[field] == null) return null;
    return _positive(json, field);
  }

  static String? _optionalString(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value == null) return null;
    if (value is! String || value.length > 255) {
      throw FormatException('Invalid admission field: $field');
    }
    return value;
  }

  static PatientAdmissionStatus _status(Object? value) {
    return switch (value) {
      'admitted' => PatientAdmissionStatus.admitted,
      'discharged' => PatientAdmissionStatus.discharged,
      'transferred' => PatientAdmissionStatus.transferred,
      _ => throw const FormatException('Unknown admission status.'),
    };
  }

  static DateTime? _timestamp(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value == null) return null;
    if (value is! String ||
        !RegExp(
          r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$',
        ).hasMatch(value)) {
      throw FormatException('Invalid admission timestamp: $field');
    }
    final result = DateTime.tryParse(value);
    if (result == null) {
      throw FormatException('Invalid admission timestamp: $field');
    }
    return result.toUtc();
  }
}

/// The admissions list is a raw paginated object, not a success/data envelope.
class PatientAdmissionPage {
  const PatientAdmissionPage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
  });

  final List<PatientAdmission> items;
  final int total;
  final int page;
  final int perPage;

  factory PatientAdmissionPage.fromJson(
    Map<String, dynamic> json, {
    required int expectedPatientId,
    required int requestedPage,
    required int requestedPerPage,
  }) {
    final rawItems = json['items'];
    if (rawItems is! List) {
      throw const FormatException('Admission page items must be an array.');
    }

    final total = _pageInt(json, 'total', 0);
    final page = _pageInt(json, 'page', 1);
    final perPage = _pageInt(json, 'per_page', 1);

    if (page != requestedPage ||
        perPage != requestedPerPage ||
        perPage > 500 ||
        rawItems.length > perPage ||
        rawItems.length > total) {
      throw const FormatException('Invalid admission pagination.');
    }

    final items = <PatientAdmission>[];
    final seen = <int>{};

    for (final item in rawItems) {
      if (item is! Map<String, dynamic>) {
        throw const FormatException('Admission page row is invalid.');
      }

      final admission = PatientAdmission.fromJson(
        item,
        expectedPatientId: expectedPatientId,
      );

      if (!seen.add(admission.id)) {
        throw const FormatException('Duplicate admission ID.');
      }

      items.add(admission);
    }

    return PatientAdmissionPage(
      items: List<PatientAdmission>.unmodifiable(items),
      total: total,
      page: page,
      perPage: perPage,
    );
  }

  static int _pageInt(Map<String, dynamic> json, String field, int minimum) {
    final value = json[field];
    if (value is! int || value < minimum) {
      throw FormatException('Invalid admission pagination: $field');
    }
    return value;
  }
}
