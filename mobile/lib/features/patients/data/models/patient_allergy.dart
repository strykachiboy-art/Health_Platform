enum PatientAllergyType { drug, food, environmental, latex, biologic, other }

enum PatientAllergySeverity { unknown, mild, moderate, severe, lifeThreatening }

enum PatientAllergyClinicalStatus { active, inactive, resolved }

enum PatientAllergyVerificationStatus { unconfirmed, confirmed, refuted }

/// A server-owned structured allergy record, not legacy patient allergy text.
class PatientAllergy {
  const PatientAllergy({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.allergenName,
    required this.allergenCode,
    required this.allergenType,
    required this.reaction,
    required this.severity,
    required this.clinicalStatus,
    required this.verificationStatus,
    required this.onsetAt,
    required this.recordedAt,
    required this.recordedById,
    required this.notes,
    required this.createdAt,
    required this.updatedAt,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final String allergenName;
  final String? allergenCode;
  final PatientAllergyType allergenType;
  final String? reaction;
  final PatientAllergySeverity severity;
  final PatientAllergyClinicalStatus clinicalStatus;
  final PatientAllergyVerificationStatus verificationStatus;
  final DateTime? onsetAt;
  final DateTime recordedAt;
  final int? recordedById;
  final String? notes;
  final DateTime createdAt;
  final DateTime updatedAt;

  factory PatientAllergy.fromJson(Map<String, dynamic> json) {
    return PatientAllergy(
      id: _positive(json, 'id'),
      clinicId: _positive(json, 'clinic_id'),
      patientId: _positive(json, 'patient_id'),
      allergenName: _requiredString(json, 'allergen_name'),
      allergenCode: _optionalString(json, 'allergen_code'),
      allergenType: _enumValue(json, 'allergen_type', const {
        'drug': PatientAllergyType.drug,
        'food': PatientAllergyType.food,
        'environmental': PatientAllergyType.environmental,
        'latex': PatientAllergyType.latex,
        'biologic': PatientAllergyType.biologic,
        'other': PatientAllergyType.other,
      }),
      reaction: _optionalString(json, 'reaction'),
      severity: _enumValue(json, 'severity', const {
        'unknown': PatientAllergySeverity.unknown,
        'mild': PatientAllergySeverity.mild,
        'moderate': PatientAllergySeverity.moderate,
        'severe': PatientAllergySeverity.severe,
        'life_threatening': PatientAllergySeverity.lifeThreatening,
      }),
      clinicalStatus: _enumValue(json, 'clinical_status', const {
        'active': PatientAllergyClinicalStatus.active,
        'inactive': PatientAllergyClinicalStatus.inactive,
        'resolved': PatientAllergyClinicalStatus.resolved,
      }),
      verificationStatus: _enumValue(json, 'verification_status', const {
        'unconfirmed': PatientAllergyVerificationStatus.unconfirmed,
        'confirmed': PatientAllergyVerificationStatus.confirmed,
        'refuted': PatientAllergyVerificationStatus.refuted,
      }),
      onsetAt: _timestamp(json, 'onset_at', optional: true),
      recordedAt: _timestamp(json, 'recorded_at')!,
      recordedById: _optionalPositive(json, 'recorded_by_id'),
      notes: _optionalString(json, 'notes'),
      createdAt: _timestamp(json, 'created_at')!,
      updatedAt: _timestamp(json, 'updated_at')!,
    );
  }

  static int _positive(Map<String, dynamic> json, String name) {
    final value = json[name];
    if (value is! int || value <= 0) {
      throw FormatException('Invalid allergy field: $name');
    }
    return value;
  }

  static int? _optionalPositive(Map<String, dynamic> json, String name) {
    if (json[name] == null) return null;
    return _positive(json, name);
  }

  static String _requiredString(Map<String, dynamic> json, String name) {
    final value = json[name];
    if (value is! String || value.trim().isEmpty || value != value.trim()) {
      throw FormatException('Invalid allergy field: $name');
    }
    return value;
  }

  static String? _optionalString(Map<String, dynamic> json, String name) {
    final value = json[name];
    if (value == null) return null;
    if (value is! String) {
      throw FormatException('Invalid allergy field: $name');
    }
    return value;
  }

  static T _enumValue<T extends Enum>(
    Map<String, dynamic> json,
    String name,
    Map<String, T> values,
  ) {
    final value = json[name];
    if (value is! String || !values.containsKey(value)) {
      throw FormatException('Invalid allergy enum: $name');
    }
    return values[value]!;
  }

  static DateTime? _timestamp(
    Map<String, dynamic> json,
    String name, {
    bool optional = false,
  }) {
    final value = json[name];
    if (optional && value == null) return null;

    if (value is! String ||
        !RegExp(
          r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$',
        ).hasMatch(value)) {
      throw FormatException('Invalid allergy timestamp: $name');
    }

    final parsed = DateTime.tryParse(value);
    if (parsed == null) {
      throw FormatException('Invalid allergy timestamp: $name');
    }
    return parsed.toUtc();
  }
}

/// One backend page. Empty pages are not proof of no allergies overall.
class PatientAllergyPage {
  const PatientAllergyPage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
  });

  final List<PatientAllergy> items;
  final int total;
  final int page;
  final int perPage;

  factory PatientAllergyPage.fromJson(
    Map<String, dynamic> json, {
    required int expectedPatientId,
    required int expectedClinicId,
    required int requestedPage,
    required int requestedPerPage,
  }) {
    final rawItems = json['items'];
    if (rawItems is! List) {
      throw const FormatException('Invalid allergy page items.');
    }

    final total = _pageInt(json, 'total', 0);
    final page = _pageInt(json, 'page', 1);
    final perPage = _pageInt(json, 'per_page', 1);

    if (page != requestedPage ||
        perPage != requestedPerPage ||
        perPage > 500 ||
        rawItems.length > perPage ||
        rawItems.length > total) {
      throw const FormatException('Invalid allergy page metadata.');
    }

    final items = <PatientAllergy>[];
    final seen = <int>{};

    for (final raw in rawItems) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Invalid allergy page row.');
      }

      final allergy = PatientAllergy.fromJson(raw);

      if (allergy.patientId != expectedPatientId ||
          allergy.clinicId != expectedClinicId ||
          !seen.add(allergy.id)) {
        throw const FormatException('Allergy record scope mismatch.');
      }

      items.add(allergy);
    }

    return PatientAllergyPage(
      items: List<PatientAllergy>.unmodifiable(items),
      total: total,
      page: page,
      perPage: perPage,
    );
  }

  static int _pageInt(Map<String, dynamic> json, String name, int minimum) {
    final value = json[name];
    if (value is! int || value < minimum) {
      throw FormatException('Invalid allergy pagination: $name');
    }
    return value;
  }
}
