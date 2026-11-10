// P23G.S01 — transient, server-authoritative medication reconciliation reads.
// Decisions and clinical completion are never inferred from a local gesture.

enum MedicationReconciliationEvent { admission, transfer, discharge }

enum MedicationReconciliationRecordStatus { draft, finalised, amended }

enum MedicationReconciliationDecision {
  continueMedication,
  hold,
  stop,
  replace,
  newMedication,
  verify,
  unknown,
}

extension MedicationReconciliationEventWire on MedicationReconciliationEvent {
  String get wireValue => name;
}

const _decisions = <String, MedicationReconciliationDecision>{
  'continue': MedicationReconciliationDecision.continueMedication,
  'hold': MedicationReconciliationDecision.hold,
  'stop': MedicationReconciliationDecision.stop,
  'replace': MedicationReconciliationDecision.replace,
  'new': MedicationReconciliationDecision.newMedication,
  'verify': MedicationReconciliationDecision.verify,
  'unknown': MedicationReconciliationDecision.unknown,
};

const _recordStatuses = <String, MedicationReconciliationRecordStatus>{
  'draft': MedicationReconciliationRecordStatus.draft,
  'final': MedicationReconciliationRecordStatus.finalised,
  'amended': MedicationReconciliationRecordStatus.amended,
};

const _events = <String, MedicationReconciliationEvent>{
  'admission': MedicationReconciliationEvent.admission,
  'transfer': MedicationReconciliationEvent.transfer,
  'discharge': MedicationReconciliationEvent.discharge,
};

final _awareTimestampPattern = RegExp(
  r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$',
);

int _positiveId(Map<String, dynamic> json, String name) {
  final raw = json[name];
  if (raw is! int || raw <= 0 || raw > 2147483647) {
    throw FormatException('Invalid medication reconciliation ID: $name');
  }
  return raw;
}

int? _optionalId(Map<String, dynamic> json, String name) =>
    json[name] == null ? null : _positiveId(json, name);

String _requiredText(
  Map<String, dynamic> json,
  String name, {
  required int maxLength,
}) {
  final raw = json[name];
  if (raw is! String ||
      raw.isEmpty ||
      raw.trim().isEmpty ||
      raw != raw.trim() ||
      raw.length > maxLength) {
    throw FormatException('Invalid medication reconciliation text: $name');
  }
  return raw;
}

String? _optionalText(
  Map<String, dynamic> json,
  String name, {
  required int maxLength,
}) {
  if (json[name] == null) return null;
  return _requiredText(json, name, maxLength: maxLength);
}

DateTime? _timestamp(
  Map<String, dynamic> json,
  String name, {
  bool required = false,
}) {
  final value = json[name];
  if (value == null && !required) return null;
  if (value is! String || !_awareTimestampPattern.hasMatch(value)) {
    throw FormatException('Invalid medication reconciliation timestamp: $name');
  }
  final parsed = DateTime.tryParse(value);
  if (parsed == null) {
    throw FormatException('Invalid medication reconciliation timestamp: $name');
  }
  return parsed.toUtc();
}

T _knownEnum<T>(Object? raw, Map<String, T> values, String field) {
  if (raw is! String || !values.containsKey(raw)) {
    throw FormatException('Unknown medication reconciliation enum: $field');
  }
  final value = values[raw];
  if (value == null) {
    throw FormatException('Unknown medication reconciliation enum: $field');
  }
  return value;
}

/// One backend-owned medication decision, not an actionable prescription.
class MedicationReconciliationItem {
  const MedicationReconciliationItem({
    required this.sourcePrescriptionItemId,
    required this.medicationName,
    required this.decision,
    required this.replacementMedicationName,
    required this.doseText,
    required this.routeText,
    required this.frequencyText,
    required this.instructions,
    required this.rationale,
  });

  final int? sourcePrescriptionItemId;
  final String medicationName;
  final MedicationReconciliationDecision decision;
  final String? replacementMedicationName;
  final String? doseText;
  final String? routeText;
  final String? frequencyText;
  final String? instructions;
  final String? rationale;

  bool get requiresResolution =>
      decision == MedicationReconciliationDecision.verify ||
      decision == MedicationReconciliationDecision.unknown;

  factory MedicationReconciliationItem.fromJson(Map<String, dynamic> json) {
    final decision = _knownEnum(json['decision'], _decisions, 'decision');
    final replacement = _optionalText(
      json,
      'replacement_medication_name',
      maxLength: 255,
    );
    if ((decision == MedicationReconciliationDecision.replace) !=
        (replacement != null)) {
      throw const FormatException('Invalid replacement medication decision.');
    }
    return MedicationReconciliationItem(
      sourcePrescriptionItemId: _optionalId(
        json,
        'source_prescription_item_id',
      ),
      medicationName: _requiredText(json, 'medication_name', maxLength: 255),
      decision: decision,
      replacementMedicationName: replacement,
      doseText: _optionalText(json, 'dose_text', maxLength: 255),
      routeText: _optionalText(json, 'route_text', maxLength: 128),
      frequencyText: _optionalText(json, 'frequency_text', maxLength: 255),
      instructions: _optionalText(json, 'instructions', maxLength: 1000),
      rationale: _optionalText(json, 'rationale', maxLength: 1000),
    );
  }
}

/// The effective document returned by the backend, including amended content.
class MedicationReconciliationRecord {
  const MedicationReconciliationRecord({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
    required this.wardTransferId,
    required this.event,
    required this.eventIdentity,
    required this.createdByStaffId,
    required this.noMedicationsReported,
    required this.notes,
    required this.items,
    required this.status,
    required this.version,
    required this.finalizedByStaffId,
    required this.finalizedAt,
    required this.latestAmendmentId,
    required this.createdAt,
    required this.updatedAt,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int admissionId;
  final int? wardTransferId;
  final MedicationReconciliationEvent event;
  final String eventIdentity;
  final int createdByStaffId;
  final bool noMedicationsReported;
  final String? notes;
  final List<MedicationReconciliationItem> items;
  final MedicationReconciliationRecordStatus status;
  final int version;
  final int? finalizedByStaffId;
  final DateTime? finalizedAt;
  final int? latestAmendmentId;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  bool get isClinicallyFinal =>
      status == MedicationReconciliationRecordStatus.finalised ||
      status == MedicationReconciliationRecordStatus.amended;

  bool get hasUnresolvedDecisions =>
      items.any((item) => item.requiresResolution);

  factory MedicationReconciliationRecord.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    int? expectedPatientId,
    int? expectedAdmissionId,
    int? expectedId,
    MedicationReconciliationEvent? expectedEvent,
    int? expectedWardTransferId,
  }) {
    final id = _positiveId(json, 'id');
    final clinic = _positiveId(json, 'clinic_id');
    final patient = _positiveId(json, 'patient_id');
    final admission = _positiveId(json, 'admission_id');
    final transfer = _optionalId(json, 'ward_transfer_id');
    final event = _knownEnum(json['event_type'], _events, 'event_type');
    final status = _knownEnum(
      json['record_status'],
      _recordStatuses,
      'record_status',
    );
    final version = _positiveId(json, 'record_version');
    if (clinic != expectedClinicId ||
        (expectedPatientId != null && expectedPatientId != patient) ||
        (expectedAdmissionId != null && expectedAdmissionId != admission) ||
        (expectedId != null && expectedId != id) ||
        (expectedEvent != null && expectedEvent != event) ||
        (expectedWardTransferId != null &&
            expectedWardTransferId != transfer)) {
      throw const FormatException('Medication reconciliation scope mismatch.');
    }
    if ((event == MedicationReconciliationEvent.transfer) !=
        (transfer != null)) {
      throw const FormatException(
        'Medication reconciliation transfer mismatch.',
      );
    }
    final identity = _requiredText(json, 'event_identity', maxLength: 255);
    final expectedIdentity = switch (event) {
      MedicationReconciliationEvent.admission => 'admission:$admission',
      MedicationReconciliationEvent.transfer => 'transfer:$transfer',
      MedicationReconciliationEvent.discharge => 'discharge:$admission',
    };
    if (identity != expectedIdentity) {
      throw const FormatException('Medication reconciliation event mismatch.');
    }
    final noMedications = json['no_medications_reported'];
    final rawItems = json['items'];
    if (noMedications is! bool || rawItems is! List || rawItems.length > 500) {
      throw const FormatException(
        'Invalid reconciliation medication document.',
      );
    }
    final seenSources = <int>{};
    final items = <MedicationReconciliationItem>[];
    for (final raw in rawItems) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Invalid reconciliation medication item.');
      }
      final item = MedicationReconciliationItem.fromJson(raw);
      if (item.sourcePrescriptionItemId != null &&
          !seenSources.add(item.sourcePrescriptionItemId!)) {
        throw const FormatException('Repeated source prescription item.');
      }
      items.add(item);
    }
    if (noMedications && items.isNotEmpty) {
      throw const FormatException('Contradictory no-medications declaration.');
    }
    final finalizedBy = _optionalId(json, 'finalized_by_staff_id');
    final finalizedAt = _timestamp(json, 'finalized_at');
    final amendment = _optionalId(json, 'latest_amendment_id');
    final isDraft = status == MedicationReconciliationRecordStatus.draft;
    final isAmended = status == MedicationReconciliationRecordStatus.amended;
    if ((isDraft &&
            (version != 1 ||
                finalizedBy != null ||
                finalizedAt != null ||
                amendment != null)) ||
        (!isDraft &&
            (finalizedBy == null ||
                finalizedAt == null ||
                (isAmended
                    ? version < 2 || amendment == null
                    : version != 1 || amendment != null) ||
                (!noMedications && items.isEmpty) ||
                items.any((item) => item.requiresResolution)))) {
      throw const FormatException('Inconsistent reconciliation finalization.');
    }
    return MedicationReconciliationRecord(
      id: id,
      clinicId: clinic,
      patientId: patient,
      admissionId: admission,
      wardTransferId: transfer,
      event: event,
      eventIdentity: identity,
      createdByStaffId: _positiveId(json, 'created_by_staff_id'),
      noMedicationsReported: noMedications,
      notes: _optionalText(json, 'notes', maxLength: 10000),
      items: List<MedicationReconciliationItem>.unmodifiable(items),
      status: status,
      version: version,
      finalizedByStaffId: finalizedBy,
      finalizedAt: finalizedAt,
      latestAmendmentId: amendment,
      createdAt: _timestamp(json, 'created_at'),
      updatedAt: _timestamp(json, 'updated_at'),
    );
  }
}

class MedicationReconciliationPage {
  const MedicationReconciliationPage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
  });

  final List<MedicationReconciliationRecord> items;
  final int total;
  final int page;
  final int perPage;

  bool get hasNext => page * perPage < total;

  factory MedicationReconciliationPage.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    required int expectedAdmissionId,
    int? expectedPatientId,
    required int requestedPage,
    required int requestedPerPage,
    MedicationReconciliationEvent? requestedEvent,
  }) {
    final rows = json['items'];
    final total = json['total'];
    final page = json['page'];
    final perPage = json['per_page'];
    if (rows is! List ||
        total is! int ||
        total < 0 ||
        page != requestedPage ||
        perPage != requestedPerPage ||
        rows.length > requestedPerPage ||
        total < rows.length ||
        (requestedPage - 1) * requestedPerPage + rows.length > total) {
      throw const FormatException(
        'Invalid reconciliation pagination envelope.',
      );
    }
    final seen = <int>{};
    final items = <MedicationReconciliationRecord>[];
    for (final raw in rows) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Invalid reconciliation list row.');
      }
      final record = MedicationReconciliationRecord.fromJson(
        raw,
        expectedClinicId: expectedClinicId,
        expectedAdmissionId: expectedAdmissionId,
        expectedPatientId: expectedPatientId,
        expectedEvent: requestedEvent,
      );
      if (!seen.add(record.id)) {
        throw const FormatException('Repeated medication reconciliation ID.');
      }
      items.add(record);
    }
    return MedicationReconciliationPage(
      items: List<MedicationReconciliationRecord>.unmodifiable(items),
      total: total,
      page: page,
      perPage: perPage,
    );
  }
}
