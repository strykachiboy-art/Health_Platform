enum PatientPrescriptionStatus { active, completed, cancelled, expired }

class PatientPrescriptionItem {
  const PatientPrescriptionItem({
    required this.id,
    required this.prescriptionId,
    required this.drugId,
    required this.dosage,
    required this.frequency,
    required this.duration,
    required this.quantity,
    required this.instructions,
  });

  final int id;
  final int prescriptionId;
  final int drugId;
  final String? dosage;
  final String? frequency;
  final String? duration;
  final int? quantity;
  final String? instructions;

  factory PatientPrescriptionItem.fromJson(
    Map<String, dynamic> json, {
    required int expectedPrescriptionId,
  }) {
    final prescriptionId = _positive(json, 'prescription_id');

    if (prescriptionId != expectedPrescriptionId) {
      throw const FormatException('Prescription item scope mismatch.');
    }

    final quantity = json['quantity'];
    if (quantity != null && (quantity is! int || quantity < 0)) {
      throw const FormatException('Invalid prescription quantity.');
    }

    return PatientPrescriptionItem(
      id: _positive(json, 'id'),
      prescriptionId: prescriptionId,
      drugId: _positive(json, 'drug_id'),
      dosage: _text(json, 'dosage'),
      frequency: _text(json, 'frequency'),
      duration: _text(json, 'duration'),
      quantity: quantity as int?,
      instructions: _text(json, 'instructions'),
    );
  }
}

class PatientPrescription {
  const PatientPrescription({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.consultationId,
    required this.prescribedById,
    required this.status,
    required this.notes,
    required this.issuedAt,
    required this.expiresAt,
    required this.items,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int? consultationId;
  final int prescribedById;
  final PatientPrescriptionStatus status;
  final String? notes;

  /// Preserve the backend timestamp verbatim; do not assume a timezone.
  final String? issuedAt;
  final String? expiresAt;
  final List<PatientPrescriptionItem> items;

  factory PatientPrescription.fromJson(
    Map<String, dynamic> json, {
    required int expectedPatientId,
    required int expectedClinicId,
  }) {
    final id = _positive(json, 'id');
    final patientId = _positive(json, 'patient_id');
    final clinicId = _positive(json, 'clinic_id');

    if (patientId != expectedPatientId || clinicId != expectedClinicId) {
      throw const FormatException('Prescription patient or clinic mismatch.');
    }

    final status = switch (json['status']) {
      'active' => PatientPrescriptionStatus.active,
      'completed' => PatientPrescriptionStatus.completed,
      'cancelled' => PatientPrescriptionStatus.cancelled,
      'expired' => PatientPrescriptionStatus.expired,
      _ => throw const FormatException('Unknown prescription status.'),
    };

    final rawItems = json['items'];
    if (rawItems is! List) {
      throw const FormatException('Invalid prescription items.');
    }

    final items = <PatientPrescriptionItem>[];
    final ids = <int>{};

    for (final raw in rawItems) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Invalid prescription item.');
      }

      final item = PatientPrescriptionItem.fromJson(
        raw,
        expectedPrescriptionId: id,
      );

      if (!ids.add(item.id)) {
        throw const FormatException('Duplicate prescription item.');
      }

      items.add(item);
    }

    return PatientPrescription(
      id: id,
      clinicId: clinicId,
      patientId: patientId,
      consultationId: _optionalPositive(json, 'consultation_id'),
      prescribedById: _positive(json, 'prescribed_by_id'),
      status: status,
      notes: _text(json, 'notes'),
      issuedAt: _timestamp(json, 'issued_at'),
      expiresAt: _timestamp(json, 'expires_at'),
      items: List<PatientPrescriptionItem>.unmodifiable(items),
    );
  }
}

class PatientPrescriptionPage {
  const PatientPrescriptionPage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
  });

  final List<PatientPrescription> items;
  final int total;
  final int page;
  final int perPage;

  factory PatientPrescriptionPage.fromEnvelope(
    Map<String, dynamic> envelope, {
    required int expectedPatientId,
    required int expectedClinicId,
    required int requestedPage,
    required int requestedPerPage,
  }) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid prescription response envelope.');
    }

    final data = envelope['data'] as Map<String, dynamic>;
    final rawItems = data['items'];

    if (rawItems is! List) {
      throw const FormatException('Invalid prescription list.');
    }

    final total = _integer(data, 'total', 0);
    final page = _integer(data, 'page', 1);
    final perPage = _integer(data, 'per_page', 1);

    if (page != requestedPage ||
        perPage != requestedPerPage ||
        perPage > 500 ||
        rawItems.length > total ||
        rawItems.length > perPage) {
      throw const FormatException('Invalid prescription pagination.');
    }

    final ids = <int>{};
    final rows = <PatientPrescription>[];

    for (final raw in rawItems) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Invalid prescription row.');
      }

      final row = PatientPrescription.fromJson(
        raw,
        expectedPatientId: expectedPatientId,
        expectedClinicId: expectedClinicId,
      );

      if (!ids.add(row.id)) {
        throw const FormatException('Duplicate prescription ID.');
      }

      rows.add(row);
    }

    return PatientPrescriptionPage(
      items: List<PatientPrescription>.unmodifiable(rows),
      total: total,
      page: page,
      perPage: perPage,
    );
  }
}

int _integer(Map<String, dynamic> json, String key, int min) {
  final value = json[key];
  if (value is! int || value < min) {
    throw FormatException('Invalid prescription field: $key');
  }
  return value;
}

int _positive(Map<String, dynamic> json, String key) => _integer(json, key, 1);

int? _optionalPositive(Map<String, dynamic> json, String key) =>
    json[key] == null ? null : _positive(json, key);

String? _text(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value == null) return null;
  if (value is! String) {
    throw FormatException('Invalid prescription text: $key');
  }
  return value;
}

String? _timestamp(Map<String, dynamic> json, String key) {
  final value = _text(json, key);
  if (value == null) return null;

  if (!RegExp(
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$',
      ).hasMatch(value) ||
      DateTime.tryParse(value) == null) {
    throw FormatException('Invalid prescription timestamp: $key');
  }

  return value;
}
