/// Backend-owned vital-sign observation.
///
/// Decimal measurements are preserved as strings so the mobile
/// client never silently rounds a clinical measurement.
class PatientVitals {
  const PatientVitals({
    required this.id,
    required this.patientId,
    required this.consultationId,
    required this.recordedById,
    required this.temperature,
    required this.bloodPressureSystolic,
    required this.bloodPressureDiastolic,
    required this.heartRate,
    required this.respiratoryRate,
    required this.oxygenSaturation,
    required this.weight,
    required this.height,
    required this.recordedAt,
  });

  final int id;
  final int patientId;
  final int? consultationId;
  final int? recordedById;

  final String? temperature;
  final int? bloodPressureSystolic;
  final int? bloodPressureDiastolic;
  final int? heartRate;
  final int? respiratoryRate;
  final String? oxygenSaturation;
  final String? weight;
  final String? height;
  final DateTime recordedAt;

  factory PatientVitals.fromJson(
    Map<String, dynamic> json, {
    required int expectedPatientId,
  }) {
    final patientId = _positive(json, 'patient_id');
    if (patientId != expectedPatientId) {
      throw const FormatException('Vitals patient scope mismatch.');
    }

    return PatientVitals(
      id: _positive(json, 'id'),
      patientId: patientId,
      consultationId: _optionalPositive(json, 'consultation_id'),
      recordedById: _optionalPositive(json, 'recorded_by_id'),
      temperature: _decimal(json, 'temperature'),
      bloodPressureSystolic: _optionalInt(json, 'blood_pressure_systolic'),
      bloodPressureDiastolic: _optionalInt(json, 'blood_pressure_diastolic'),
      heartRate: _optionalInt(json, 'heart_rate'),
      respiratoryRate: _optionalInt(json, 'respiratory_rate'),
      oxygenSaturation: _decimal(json, 'oxygen_saturation'),
      weight: _decimal(json, 'weight'),
      height: _decimal(json, 'height'),
      recordedAt: _recordedAt(json),
    );
  }

  static int _positive(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is! int || value <= 0) {
      throw FormatException('Invalid vitals field: $field');
    }
    return value;
  }

  static int? _optionalPositive(Map<String, dynamic> json, String field) {
    if (json[field] == null) return null;
    return _positive(json, field);
  }

  static int? _optionalInt(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value == null) return null;
    if (value is! int) {
      throw FormatException('Invalid vitals field: $field');
    }
    return value;
  }

  static String? _decimal(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value == null) return null;

    // Pydantic model_dump(mode="json") represents Decimal as a
    // JSON string. Reject numeric JSON to avoid losing precision.
    if (value is! String ||
        !RegExp(r'^-?(?:0|[1-9]\d*)(?:\.\d+)?$').hasMatch(value)) {
      throw FormatException('Invalid decimal vitals field: $field');
    }

    return value;
  }

  static DateTime _recordedAt(Map<String, dynamic> json) {
    final value = json['recorded_at'];

    if (value is! String ||
        !RegExp(
          r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$',
        ).hasMatch(value)) {
      throw const FormatException('Invalid vitals recorded_at.');
    }

    final parsed = DateTime.tryParse(value);
    if (parsed == null) {
      throw const FormatException('Invalid vitals recorded_at.');
    }
    return parsed.toUtc();
  }
}

/// One server-owned page of patient vitals.
class PatientVitalsPage {
  const PatientVitalsPage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
  });

  final List<PatientVitals> items;
  final int total;
  final int page;
  final int perPage;

  factory PatientVitalsPage.fromJson(
    Map<String, dynamic> json, {
    required int expectedPatientId,
    required int requestedPage,
    required int requestedPerPage,
  }) {
    final rawItems = json['items'];
    if (rawItems is! List) {
      throw const FormatException('Invalid vitals page items.');
    }

    final total = _pageInt(json, 'total', 0);
    final page = _pageInt(json, 'page', 1);
    final perPage = _pageInt(json, 'per_page', 1);

    if (page != requestedPage ||
        perPage != requestedPerPage ||
        perPage > 500 ||
        rawItems.length > total ||
        rawItems.length > perPage) {
      throw const FormatException('Invalid vitals pagination.');
    }

    final seenIds = <int>{};
    final items = <PatientVitals>[];

    for (final raw in rawItems) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Invalid vitals page row.');
      }

      final observation = PatientVitals.fromJson(
        raw,
        expectedPatientId: expectedPatientId,
      );

      if (!seenIds.add(observation.id)) {
        throw const FormatException('Duplicate vitals observation.');
      }

      items.add(observation);
    }

    return PatientVitalsPage(
      items: List<PatientVitals>.unmodifiable(items),
      total: total,
      page: page,
      perPage: perPage,
    );
  }

  static int _pageInt(Map<String, dynamic> json, String field, int minimum) {
    final value = json[field];
    if (value is! int || value < minimum) {
      throw FormatException('Invalid vitals pagination: $field');
    }
    return value;
  }
}
