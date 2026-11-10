// P23F.S03.A — response from the real backend barcode/verify POST.
// HTTP 200 can contain a failed event with no administration association.
// This object is evidence of a scan, never authorization to administer.
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

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

final _awareTimestamp = RegExp(
  r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$',
);

int? _id(Map<String, dynamic> json, String name, {bool mustExist = false}) {
  final value = json[name];
  if (value == null && !mustExist) return null;
  if (value is! int || value <= 0 || value > 2147483647) {
    throw FormatException('Invalid verification identity: $name');
  }
  return value;
}

String _string(Map<String, dynamic> json, String name) {
  final value = json[name];
  if (value is! String ||
      value.isEmpty ||
      value.trim() != value ||
      value.length > 255) {
    throw FormatException('Invalid verification field: $name');
  }
  return value;
}

DateTime _timestamp(Map<String, dynamic> json, String name) {
  final raw = json[name];
  if (raw is! String || !_awareTimestamp.hasMatch(raw)) {
    throw FormatException('Invalid verification timestamp: $name');
  }
  final parsed = DateTime.tryParse(raw);
  if (parsed == null) {
    throw FormatException('Invalid verification timestamp: $name');
  }
  return parsed.toUtc();
}

bool _flag(Map<String, dynamic> json, String name) {
  final value = json[name];
  if (value is! bool) {
    throw FormatException('Invalid verification evidence: $name');
  }
  return value;
}

/// A single scan outcome. An unresolved failed barcode need not have patient,
/// admission, or medication-administration identifiers on the server.
class EmarBarcodeVerification {
  const EmarBarcodeVerification({
    required this.id,
    required this.clinicId,
    required this.administrationId,
    required this.patientId,
    required this.admissionId,
    required this.result,
    required this.failureCode,
    required this.patientMatch,
    required this.medicationMatch,
    required this.doseMatch,
    required this.routeMatch,
    required this.timeWindowMatch,
    required this.sourceOrderVersion,
    required this.policyVersion,
  });

  final int id;
  final int clinicId;
  final int? administrationId;
  final int? patientId;
  final int? admissionId;
  final EmarBarcodeResult result;
  final EmarBarcodeFailure? failureCode;
  final bool patientMatch;
  final bool medicationMatch;
  final bool doseMatch;
  final bool routeMatch;
  final bool timeWindowMatch;
  final int? sourceOrderVersion;
  final int policyVersion;

  bool get verified =>
      result == EmarBarcodeResult.barcodeVerified &&
      failureCode == null &&
      administrationId != null &&
      patientId != null &&
      admissionId != null &&
      patientMatch &&
      medicationMatch &&
      doseMatch &&
      routeMatch &&
      timeWindowMatch;

  static EmarBarcodeVerification fromEnvelope(
    Map<String, dynamic> response, {
    required EmarAdministration reviewed,
    required String operationKey,
    required String patientBarcode,
    required String medicationBarcode,
    required String scanDevice,
    required DateTime scanTimestamp,
  }) {
    final raw = response['verification'];
    if (raw is! Map<String, dynamic>) {
      throw const FormatException('Missing server barcode verification.');
    }
    if (_string(raw, 'operation_identity') != operationKey ||
        _string(raw, 'patient_barcode') != patientBarcode ||
        _string(raw, 'medication_barcode') != medicationBarcode ||
        _string(raw, 'scan_device') != scanDevice ||
        _timestamp(raw, 'scan_timestamp') != scanTimestamp.toUtc()) {
      throw const FormatException(
        'Barcode verification request echo mismatch.',
      );
    }
    final clinicId = _id(raw, 'clinic_id', mustExist: true)!;
    final administrationId = _id(raw, 'medication_administration_id');
    final patientId = _id(raw, 'patient_id');
    final admissionId = _id(raw, 'admission_id');
    if (clinicId != reviewed.clinicId ||
        (administrationId != null && administrationId != reviewed.id) ||
        (patientId != null && patientId != reviewed.patientId) ||
        (admissionId != null && admissionId != reviewed.admissionId)) {
      throw const FormatException('Barcode verification scope mismatch.');
    }
    _id(raw, 'actor_staff_id', mustExist: true);
    final result = switch (raw['verification_result']) {
      'barcode_verified' => EmarBarcodeResult.barcodeVerified,
      'not_barcode_verified' => EmarBarcodeResult.notBarcodeVerified,
      _ => throw const FormatException('Unknown barcode verification result.'),
    };
    final rawFailure = raw['failure_code'];
    final EmarBarcodeFailure? failure;
    if (rawFailure == null) {
      failure = null;
    } else {
      failure = _failureCodes[rawFailure];
      if (failure == null) {
        throw const FormatException('Unknown barcode verification failure.');
      }
    }
    final reason = raw['failure_reason'];
    if (reason != null && (reason is! String || reason.length > 4000)) {
      throw const FormatException('Malformed barcode failure reason.');
    }
    final event = EmarBarcodeVerification(
      id: _id(raw, 'id', mustExist: true)!,
      clinicId: clinicId,
      administrationId: administrationId,
      patientId: patientId,
      admissionId: admissionId,
      result: result,
      failureCode: failure,
      patientMatch: _flag(raw, 'patient_match'),
      medicationMatch: _flag(raw, 'medication_match'),
      doseMatch: _flag(raw, 'dose_match'),
      routeMatch: _flag(raw, 'route_match'),
      timeWindowMatch: _flag(raw, 'time_window_match'),
      sourceOrderVersion: _id(raw, 'source_order_version'),
      policyVersion: _id(raw, 'verification_policy_version', mustExist: true)!,
    );
    if (event.result == EmarBarcodeResult.barcodeVerified) {
      if (!event.verified ||
          reason != null ||
          event.sourceOrderVersion != reviewed.sourceOrderVersion) {
        throw const FormatException(
          'Incomplete successful barcode verification.',
        );
      }
    } else if (event.failureCode == null) {
      throw const FormatException('Missing failed-barcode reason code.');
    }
    return event;
  }
}
