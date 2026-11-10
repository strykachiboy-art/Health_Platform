// P23F.S01 — strict, transient, server-authoritative eMAR read models.
// Never store administration or barcode events in a durable offline queue.

enum EmarAdministrationStatus {
  scheduled,
  due,
  administered,
  held,
  refused,
  missed,
  cancelled,
}

enum EmarRecordStatus { draft, finalised, amended }

enum EmarBarcodeResult { barcodeVerified, notBarcodeVerified }

enum EmarBarcodeFailure {
  unknownPatientBarcode,
  unknownMedicationBarcode,
  malformedPatientBarcode,
  malformedMedicationBarcode,
  crossClinicPatientBarcode,
  crossClinicMedicationBarcode,
  wrongPatient,
  wrongMedication,
  doseMismatch,
  routeMismatch,
  outsideTimeWindow,
  inactiveAdmission,
  inactiveOrder,
  orderCancelled,
  orderExpired,
  unauthorizedActor,
  staleVerification,
  noScheduledAdministration,
  ambiguousScheduledAdministration,
}

final RegExp _isoTimestamp = RegExp(
  r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$',
);
final RegExp _decimalNumber = RegExp(r'^\d+(?:\.\d+)?$');

int emarPositiveId(Map<String, dynamic> json, String field) {
  final value = json[field];
  if (value is! int || value <= 0 || value > 2147483647) {
    throw FormatException('Invalid eMAR identifier: $field');
  }
  return value;
}

int? _optionalId(Map<String, dynamic> json, String field) {
  if (json[field] == null) return null;
  return emarPositiveId(json, field);
}

String? _optionalString(
  Map<String, dynamic> json,
  String field, {
  int maxLength = 4000,
}) {
  final value = json[field];
  if (value == null) return null;
  if (value is! String || value.length > maxLength) {
    throw FormatException('Invalid eMAR text: $field');
  }
  return value;
}

DateTime? _utcTime(
  Map<String, dynamic> json,
  String field, {
  bool required = false,
}) {
  final raw = json[field];
  if (raw == null && !required) return null;
  if (raw is! String || !_isoTimestamp.hasMatch(raw)) {
    throw FormatException('Invalid eMAR timestamp: $field');
  }
  final parsed = DateTime.tryParse(raw);
  if (parsed == null) {
    throw FormatException('Invalid eMAR timestamp: $field');
  }
  return parsed.toUtc();
}

String? _decimal(Map<String, dynamic> json, String field) {
  final value = json[field];
  if (value == null) return null;
  // Preserve decimal precision; do not round clinical doses through double.
  if (value is! String || !_decimalNumber.hasMatch(value)) {
    throw FormatException('Invalid eMAR decimal: $field');
  }
  return value;
}

T _enumValue<T>(Object? value, Map<String, T> values, String field) {
  final result = values[value];
  if (result == null) throw FormatException('Invalid eMAR enum: $field');
  return result;
}

const _statuses = <String, EmarAdministrationStatus>{
  'scheduled': EmarAdministrationStatus.scheduled,
  'due': EmarAdministrationStatus.due,
  'administered': EmarAdministrationStatus.administered,
  'held': EmarAdministrationStatus.held,
  'refused': EmarAdministrationStatus.refused,
  'missed': EmarAdministrationStatus.missed,
  'cancelled': EmarAdministrationStatus.cancelled,
};

const _recordStatuses = <String, EmarRecordStatus>{
  'draft': EmarRecordStatus.draft,
  'final': EmarRecordStatus.finalised,
  'amended': EmarRecordStatus.amended,
};

const _barcodeResults = <String, EmarBarcodeResult>{
  'barcode_verified': EmarBarcodeResult.barcodeVerified,
  'not_barcode_verified': EmarBarcodeResult.notBarcodeVerified,
};

const _failureCodes = <String, EmarBarcodeFailure>{
  'unknown_patient_barcode': EmarBarcodeFailure.unknownPatientBarcode,
  'unknown_medication_barcode': EmarBarcodeFailure.unknownMedicationBarcode,
  'malformed_patient_barcode': EmarBarcodeFailure.malformedPatientBarcode,
  'malformed_medication_barcode': EmarBarcodeFailure.malformedMedicationBarcode,
  'cross_clinic_patient_barcode': EmarBarcodeFailure.crossClinicPatientBarcode,
  'cross_clinic_medication_barcode':
      EmarBarcodeFailure.crossClinicMedicationBarcode,
  'wrong_patient': EmarBarcodeFailure.wrongPatient,
  'wrong_medication': EmarBarcodeFailure.wrongMedication,
  'dose_mismatch': EmarBarcodeFailure.doseMismatch,
  'route_mismatch': EmarBarcodeFailure.routeMismatch,
  'outside_time_window': EmarBarcodeFailure.outsideTimeWindow,
  'inactive_admission': EmarBarcodeFailure.inactiveAdmission,
  'inactive_order': EmarBarcodeFailure.inactiveOrder,
  'order_cancelled': EmarBarcodeFailure.orderCancelled,
  'order_expired': EmarBarcodeFailure.orderExpired,
  'unauthorized_actor': EmarBarcodeFailure.unauthorizedActor,
  'stale_verification': EmarBarcodeFailure.staleVerification,
  'no_scheduled_administration': EmarBarcodeFailure.noScheduledAdministration,
  'ambiguous_scheduled_administration':
      EmarBarcodeFailure.ambiguousScheduledAdministration,
};

/// One server-owned eMAR row. It carries facts, not a client authorization grant.
class EmarAdministration {
  const EmarAdministration({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
    required this.prescriptionId,
    required this.prescriptionItemId,
    required this.status,
    required this.recordStatus,
    required this.recordVersion,
    required this.scheduledAt,
    required this.doseValue,
    required this.doseUnit,
    required this.routeCode,
    required this.administeredAt,
    required this.verificationResult,
    required this.verificationEventId,
    required this.sourceOrderVersion,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int admissionId;
  final int prescriptionId;
  final int prescriptionItemId;
  final EmarAdministrationStatus status;
  final EmarRecordStatus recordStatus;
  final int recordVersion;
  final DateTime? scheduledAt;
  final String? doseValue;
  final String? doseUnit;
  final String? routeCode;
  final DateTime? administeredAt;
  final EmarBarcodeResult? verificationResult;
  final int? verificationEventId;
  final int? sourceOrderVersion;

  bool get isPending =>
      status == EmarAdministrationStatus.scheduled ||
      status == EmarAdministrationStatus.due;

  factory EmarAdministration.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    int? expectedPatientId,
    int? expectedAdmissionId,
    int? expectedId,
  }) {
    final id = emarPositiveId(json, 'id');
    final clinicId = emarPositiveId(json, 'clinic_id');
    final patientId = emarPositiveId(json, 'patient_id');
    final admissionId = emarPositiveId(json, 'admission_id');
    if (clinicId != expectedClinicId ||
        (expectedPatientId != null && patientId != expectedPatientId) ||
        (expectedAdmissionId != null && admissionId != expectedAdmissionId) ||
        (expectedId != null && id != expectedId)) {
      throw const FormatException('eMAR record scope mismatch.');
    }
    final recordVersion = emarPositiveId(json, 'record_version');
    final rawVerification = json['verification_result'];
    return EmarAdministration(
      id: id,
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admissionId,
      prescriptionId: emarPositiveId(json, 'prescription_id'),
      prescriptionItemId: emarPositiveId(json, 'prescription_item_id'),
      status: _enumValue(json['status'], _statuses, 'status'),
      recordStatus: _enumValue(
        json['record_status'],
        _recordStatuses,
        'record_status',
      ),
      recordVersion: recordVersion,
      scheduledAt: _utcTime(json, 'scheduled_at'),
      doseValue: _decimal(json, 'dose_value'),
      doseUnit: _optionalString(json, 'dose_unit', maxLength: 100),
      routeCode: _optionalString(json, 'route_code', maxLength: 100),
      administeredAt: _utcTime(json, 'administered_at'),
      verificationResult: rawVerification == null
          ? null
          : _enumValue(rawVerification, _barcodeResults, 'verification_result'),
      verificationEventId: _optionalId(json, 'barcode_verification_event_id'),
      sourceOrderVersion: _optionalId(json, 'source_order_version'),
    );
  }
}

/// Raw backend envelope: {items,total,page,per_page}.
class EmarAdministrationPage {
  const EmarAdministrationPage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
  });

  final List<EmarAdministration> items;
  final int total;
  final int page;
  final int perPage;

  bool get hasNext => page * perPage < total;

  factory EmarAdministrationPage.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    required int expectedAdmissionId,
    int? expectedPatientId,
    required int requestedPage,
    required int requestedPerPage,
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
        rows.length > perPage ||
        total < rows.length ||
        (total == 0 && rows.isNotEmpty)) {
      throw const FormatException('Invalid eMAR pagination envelope.');
    }
    final seen = <int>{};
    final parsed = <EmarAdministration>[];
    for (final raw in rows) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Invalid eMAR list row.');
      }
      final row = EmarAdministration.fromJson(
        raw,
        expectedClinicId: expectedClinicId,
        expectedAdmissionId: expectedAdmissionId,
        expectedPatientId: expectedPatientId,
      );
      if (!seen.add(row.id)) {
        throw const FormatException('Duplicate eMAR administration ID.');
      }
      parsed.add(row);
    }
    return EmarAdministrationPage(
      items: List<EmarAdministration>.unmodifiable(parsed),
      total: total,
      page: page,
      perPage: perPage,
    );
  }
}

/// One durable server barcode verification event; never grants authority alone.
class EmarBarcodeEvent {
  const EmarBarcodeEvent({
    required this.id,
    required this.clinicId,
    required this.administrationId,
    required this.actorStaffId,
    required this.result,
    required this.failureCode,
    required this.failureReason,
    required this.patientMatch,
    required this.medicationMatch,
    required this.doseMatch,
    required this.routeMatch,
    required this.timeWindowMatch,
    required this.scanTimestamp,
    required this.verificationPolicyVersion,
  });

  final int id;
  final int clinicId;
  final int administrationId;
  final int actorStaffId;
  final EmarBarcodeResult result;
  final EmarBarcodeFailure? failureCode;
  final String? failureReason;
  final bool patientMatch;
  final bool medicationMatch;
  final bool doseMatch;
  final bool routeMatch;
  final bool timeWindowMatch;
  final DateTime scanTimestamp;
  final int verificationPolicyVersion;

  bool get isVerified =>
      result == EmarBarcodeResult.barcodeVerified &&
      failureCode == null &&
      failureReason == null &&
      patientMatch &&
      medicationMatch &&
      doseMatch &&
      routeMatch &&
      timeWindowMatch;

  factory EmarBarcodeEvent.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    required int expectedAdministrationId,
  }) {
    final clinicId = emarPositiveId(json, 'clinic_id');
    final administrationId = emarPositiveId(
      json,
      'medication_administration_id',
    );
    if (clinicId != expectedClinicId ||
        administrationId != expectedAdministrationId) {
      throw const FormatException('Barcode history scope mismatch.');
    }
    bool flag(String key) {
      final value = json[key];
      if (value is! bool) throw FormatException('Invalid barcode match: $key');
      return value;
    }

    final rawFailure = json['failure_code'];
    final failure = rawFailure == null
        ? null
        : _enumValue(rawFailure, _failureCodes, 'failure_code');
    final event = EmarBarcodeEvent(
      id: emarPositiveId(json, 'id'),
      clinicId: clinicId,
      administrationId: administrationId,
      actorStaffId: emarPositiveId(json, 'actor_staff_id'),
      result: _enumValue(
        json['verification_result'],
        _barcodeResults,
        'verification_result',
      ),
      failureCode: failure,
      failureReason: _optionalString(json, 'failure_reason'),
      patientMatch: flag('patient_match'),
      medicationMatch: flag('medication_match'),
      doseMatch: flag('dose_match'),
      routeMatch: flag('route_match'),
      timeWindowMatch: flag('time_window_match'),
      scanTimestamp: _utcTime(json, 'scan_timestamp', required: true)!,
      verificationPolicyVersion: emarPositiveId(
        json,
        'verification_policy_version',
      ),
    );
    if (event.result == EmarBarcodeResult.barcodeVerified &&
        !event.isVerified) {
      throw const FormatException('Inconsistent successful barcode event.');
    }
    return event;
  }
}

class EmarBarcodeHistory {
  const EmarBarcodeHistory(this.items);
  final List<EmarBarcodeEvent> items;

  factory EmarBarcodeHistory.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    required int expectedAdministrationId,
  }) {
    final history = json['history'];
    if (history is! Map<String, dynamic>) {
      throw const FormatException('Missing barcode history envelope.');
    }
    final raw = history['items'];
    final total = history['total'];
    if (raw is! List || total is! int || total != raw.length) {
      throw const FormatException('Invalid barcode event history.');
    }
    final seen = <int>{};
    final items = <EmarBarcodeEvent>[];
    for (final item in raw) {
      if (item is! Map<String, dynamic>) {
        throw const FormatException('Invalid barcode history row.');
      }
      final parsed = EmarBarcodeEvent.fromJson(
        item,
        expectedClinicId: expectedClinicId,
        expectedAdministrationId: expectedAdministrationId,
      );
      if (!seen.add(parsed.id)) {
        throw const FormatException('Duplicate barcode event ID.');
      }
      items.add(parsed);
    }
    return EmarBarcodeHistory(List<EmarBarcodeEvent>.unmodifiable(items));
  }
}
