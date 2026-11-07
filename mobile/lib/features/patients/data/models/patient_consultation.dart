enum PatientConsultationType { general, followUp, specialist, emergency }

enum PatientConsultationStatus { inProgress, completed, cancelled }

enum PatientClinicalRecordStatus { draft, finalized, amended }

/// Backend-owned clinical consultation. No PHI persistence is performed.
class PatientConsultation {
  const PatientConsultation({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.staffId,
    required this.appointmentId,
    required this.consultationType,
    required this.status,
    required this.recordStatus,
    required this.recordVersion,
    required this.finalizedByStaffId,
    required this.finalizedAt,
    required this.latestAmendmentId,
    required this.icd10Code,
    required this.chiefComplaint,
    required this.symptoms,
    required this.diagnosis,
    required this.treatmentPlan,
    required this.notes,
    required this.voiceNoteUrl,
    required this.transcribedText,
    required this.templateId,
    required this.startedAt,
    required this.endedAt,
    required this.createdAt,
    required this.updatedAt,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int staffId;
  final int? appointmentId;
  final PatientConsultationType consultationType;
  final PatientConsultationStatus status;
  final PatientClinicalRecordStatus recordStatus;
  final int recordVersion;
  final int? finalizedByStaffId;
  final String? finalizedAt;
  final int? latestAmendmentId;

  final String? icd10Code;
  final String? chiefComplaint;
  final String? symptoms;
  final String? diagnosis;
  final String? treatmentPlan;
  final String? notes;
  final String? voiceNoteUrl;
  final String? transcribedText;
  final int? templateId;

  /// Preserve backend timestamps without inventing a timezone.
  final String? startedAt;
  final String? endedAt;
  final String? createdAt;
  final String? updatedAt;

  factory PatientConsultation.fromJson(
    Map<String, dynamic> json, {
    required int expectedPatientId,
    required int expectedClinicId,
  }) {
    final patientId = _positive(json, 'patient_id');
    final clinicId = _positive(json, 'clinic_id');

    if (patientId != expectedPatientId || clinicId != expectedClinicId) {
      throw const FormatException('Consultation scope mismatch.');
    }

    final recordStatus = switch (json['record_status']) {
      'draft' => PatientClinicalRecordStatus.draft,
      'final' => PatientClinicalRecordStatus.finalized,
      'amended' => PatientClinicalRecordStatus.amended,
      _ => throw const FormatException('Unknown clinical record status.'),
    };

    final version = _positive(json, 'record_version');
    final finalizedBy = _optionalPositive(json, 'finalized_by_staff_id');
    final finalizedAt = _timestamp(json, 'finalized_at');
    final amendmentId = _optionalPositive(json, 'latest_amendment_id');

    final consistent = switch (recordStatus) {
      PatientClinicalRecordStatus.draft =>
        version == 1 &&
            finalizedBy == null &&
            finalizedAt == null &&
            amendmentId == null,
      PatientClinicalRecordStatus.finalized =>
        version == 1 &&
            finalizedBy != null &&
            finalizedAt != null &&
            amendmentId == null,
      PatientClinicalRecordStatus.amended =>
        version >= 2 &&
            finalizedBy != null &&
            finalizedAt != null &&
            amendmentId != null,
    };

    if (!consistent) {
      throw const FormatException('Inconsistent clinical record version.');
    }

    return PatientConsultation(
      id: _positive(json, 'id'),
      clinicId: clinicId,
      patientId: patientId,
      staffId: _positive(json, 'staff_id'),
      appointmentId: _optionalPositive(json, 'appointment_id'),
      consultationType: switch (json['consultation_type']) {
        'general' => PatientConsultationType.general,
        'follow_up' => PatientConsultationType.followUp,
        'specialist' => PatientConsultationType.specialist,
        'emergency' => PatientConsultationType.emergency,
        _ => throw const FormatException('Unknown consultation type.'),
      },
      status: switch (json['status']) {
        'in_progress' => PatientConsultationStatus.inProgress,
        'completed' => PatientConsultationStatus.completed,
        'cancelled' => PatientConsultationStatus.cancelled,
        _ => throw const FormatException('Unknown consultation status.'),
      },
      recordStatus: recordStatus,
      recordVersion: version,
      finalizedByStaffId: finalizedBy,
      finalizedAt: finalizedAt,
      latestAmendmentId: amendmentId,
      icd10Code: _optionalString(json, 'icd10_code'),
      chiefComplaint: _optionalString(json, 'chief_complaint'),
      symptoms: _optionalString(json, 'symptoms'),
      diagnosis: _optionalString(json, 'diagnosis'),
      treatmentPlan: _optionalString(json, 'treatment_plan'),
      notes: _optionalString(json, 'notes'),
      voiceNoteUrl: _optionalString(json, 'voice_note_url'),
      transcribedText: _optionalString(json, 'transcribed_text'),
      templateId: _optionalPositive(json, 'template_id'),
      startedAt: _timestamp(json, 'started_at'),
      endedAt: _timestamp(json, 'ended_at'),
      createdAt: _timestamp(json, 'created_at'),
      updatedAt: _timestamp(json, 'updated_at'),
    );
  }

  static int _positive(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is! int || value <= 0) {
      throw FormatException('Invalid consultation field: $field');
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
    if (value is! String) {
      throw FormatException('Invalid consultation field: $field');
    }
    return value;
  }

  static String? _timestamp(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value == null) return null;

    // Python's isoformat() can serialize a naive datetime without
    // Z or a numeric timezone. Validate without assuming one.
    if (value is! String ||
        !RegExp(
          r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$',
        ).hasMatch(value) ||
        DateTime.tryParse(value) == null) {
      throw FormatException('Invalid consultation timestamp: $field');
    }

    return value;
  }
}

/// The consultation API uses data[] plus separate pagination metadata.
class PatientConsultationPage {
  const PatientConsultationPage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
    required this.pages,
    required this.hasNext,
    required this.hasPrev,
    required this.nextPage,
    required this.prevPage,
  });

  final List<PatientConsultation> items;
  final int total;
  final int page;
  final int perPage;
  final int pages;
  final bool hasNext;
  final bool hasPrev;
  final int? nextPage;
  final int? prevPage;

  factory PatientConsultationPage.fromEnvelope(
    Map<String, dynamic> envelope, {
    required int expectedPatientId,
    required int expectedClinicId,
    required int requestedPage,
    required int requestedPerPage,
  }) {
    if (envelope['success'] != true ||
        envelope['data'] is! List ||
        envelope['pagination'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid consultation list envelope.');
    }

    final rawItems = envelope['data'] as List;
    final meta = envelope['pagination'] as Map<String, dynamic>;

    final total = _int(meta, 'total', 0);
    final page = _int(meta, 'page', 1);
    final perPage = _int(meta, 'per_page', 1);
    final pages = _int(meta, 'pages', 0);

    final hasNext = meta['has_next'];
    final hasPrev = meta['has_prev'];
    if (hasNext is! bool || hasPrev is! bool) {
      throw const FormatException('Invalid consultation navigation flags.');
    }

    final nextPage = _optionalPositive(meta, 'next_page');
    final prevPage = _optionalPositive(meta, 'prev_page');
    final expectedPages = (total + perPage - 1) ~/ perPage;

    if (page != requestedPage ||
        perPage != requestedPerPage ||
        perPage > 500 ||
        pages != expectedPages ||
        rawItems.length > total ||
        rawItems.length > perPage ||
        hasNext != (page < pages) ||
        hasPrev != (page > 1) ||
        nextPage != (hasNext ? page + 1 : null) ||
        prevPage != (hasPrev ? page - 1 : null)) {
      throw const FormatException('Inconsistent consultation pagination.');
    }

    final seen = <int>{};
    final items = <PatientConsultation>[];

    for (final raw in rawItems) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Invalid consultation row.');
      }

      final record = PatientConsultation.fromJson(
        raw,
        expectedPatientId: expectedPatientId,
        expectedClinicId: expectedClinicId,
      );

      if (!seen.add(record.id)) {
        throw const FormatException('Duplicate consultation ID.');
      }

      items.add(record);
    }

    return PatientConsultationPage(
      items: List<PatientConsultation>.unmodifiable(items),
      total: total,
      page: page,
      perPage: perPage,
      pages: pages,
      hasNext: hasNext,
      hasPrev: hasPrev,
      nextPage: nextPage,
      prevPage: prevPage,
    );
  }

  static int _int(Map<String, dynamic> json, String field, int minimum) {
    final value = json[field];
    if (value is! int || value < minimum) {
      throw FormatException('Invalid consultation pagination: $field');
    }
    return value;
  }

  static int? _optionalPositive(Map<String, dynamic> json, String field) {
    if (json[field] == null) return null;
    return _int(json, field, 1);
  }
}
