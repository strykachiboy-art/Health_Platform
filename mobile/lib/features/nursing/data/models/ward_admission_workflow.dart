// P23E.C: strict server-owned reservation/transfer readback. No local PHI cache.
import 'package:clinic_system_pro/features/nursing/data/models/ward_overview.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';

enum WardReservationStatus { pending, cancelled, expired, fulfilled }

enum WardAdmissionAction {
  reserve,
  admitDirect,
  admitFromReservation,
  transfer,
}

int _id(Map<String, dynamic> data, String field) {
  final value = data[field];
  if (value is! int || value <= 0 || value > 2147483647) {
    throw FormatException('Invalid identifier: $field');
  }
  return value;
}

String? _reason(Map<String, dynamic> data, String field) {
  final raw = data[field];
  if (raw == null) return null;
  if (raw is! String || raw.length > 255) {
    throw FormatException('Invalid text: $field');
  }
  return raw;
}

DateTime? _date(
  Map<String, dynamic> data,
  String field, {
  bool mustExist = false,
}) {
  final value = data[field];
  if (value == null && !mustExist) return null;
  if (value is! String ||
      !RegExp(
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$',
      ).hasMatch(value)) {
    throw FormatException('Invalid timestamp: $field');
  }
  final result = DateTime.tryParse(value);
  if (result == null) throw FormatException('Invalid timestamp: $field');
  return result.toUtc();
}

class WardReservationRecord {
  const WardReservationRecord({
    required this.id,
    required this.patientId,
    required this.bedId,
    required this.status,
    required this.reservedAt,
    required this.expiresAt,
    required this.reason,
  });

  final int id;
  final int patientId;
  final int bedId;
  final WardReservationStatus status;
  final DateTime reservedAt;
  final DateTime? expiresAt;
  final String? reason;

  bool get isPendingAndUnexpired =>
      status == WardReservationStatus.pending &&
      (expiresAt == null || expiresAt!.isAfter(DateTime.now().toUtc()));

  factory WardReservationRecord.fromJson(
    Map<String, dynamic> json, {
    required int expectedPatientId,
  }) {
    final patientId = _id(json, 'patient_id');
    if (patientId != expectedPatientId) {
      throw const FormatException('Reservation patient scope mismatch.');
    }
    final status = switch (json['status']) {
      'pending' => WardReservationStatus.pending,
      'cancelled' => WardReservationStatus.cancelled,
      'expired' => WardReservationStatus.expired,
      'fulfilled' => WardReservationStatus.fulfilled,
      _ => throw const FormatException('Invalid reservation status.'),
    };
    return WardReservationRecord(
      id: _id(json, 'id'),
      patientId: patientId,
      bedId: _id(json, 'bed_id'),
      status: status,
      reservedAt: _date(json, 'reserved_at', mustExist: true)!,
      expiresAt: _date(json, 'expires_at'),
      reason: _reason(json, 'reason'),
    );
  }
}

class WardTransferRecord {
  const WardTransferRecord({
    required this.id,
    required this.admissionId,
    required this.fromBedId,
    required this.toBedId,
  });

  final int id;
  final int admissionId;
  final int fromBedId;
  final int toBedId;

  factory WardTransferRecord.fromJson(
    Map<String, dynamic> json, {
    required int expectedAdmissionId,
    required int expectedFromBedId,
    required int expectedToBedId,
  }) {
    final admissionId = _id(json, 'admission_id');
    final fromBedId = _id(json, 'from_bed_id');
    final toBedId = _id(json, 'to_bed_id');
    if (admissionId != expectedAdmissionId ||
        fromBedId != expectedFromBedId ||
        toBedId != expectedToBedId) {
      throw const FormatException('Transfer context mismatch.');
    }
    return WardTransferRecord(
      id: _id(json, 'id'),
      admissionId: admissionId,
      fromBedId: fromBedId,
      toBedId: toBedId,
    );
  }
}

/// Every snapshot is transient, scoped to one patient and one proposed bed.
class WardAdmissionSnapshot {
  const WardAdmissionSnapshot({
    required this.patientId,
    required this.ward,
    required this.bed,
    required this.activeAdmission,
    required this.activeReservation,
    required this.mayWrite,
  });

  final int patientId;
  final WardRecord ward;
  final WardBedRecord bed;
  final PatientAdmission? activeAdmission;
  final WardReservationRecord? activeReservation;
  final bool mayWrite;

  bool get canStartNew =>
      activeAdmission == null &&
      activeReservation == null &&
      bed.status == WardBedStatus.available;

  bool get canAdmitReservation =>
      activeAdmission == null &&
      activeReservation != null &&
      activeReservation!.isPendingAndUnexpired &&
      activeReservation!.bedId == bed.id &&
      bed.status == WardBedStatus.reserved;

  bool get canTransfer =>
      activeAdmission != null &&
      activeAdmission!.isActive &&
      activeAdmission!.bedId != bed.id &&
      bed.status == WardBedStatus.available;

  bool allows(WardAdmissionAction action) {
    if (!mayWrite) return false;
    return switch (action) {
      WardAdmissionAction.reserve => canStartNew,
      WardAdmissionAction.admitDirect => canStartNew,
      WardAdmissionAction.admitFromReservation => canAdmitReservation,
      WardAdmissionAction.transfer => canTransfer,
    };
  }

  bool samePreflight(WardAdmissionSnapshot other) =>
      patientId == other.patientId &&
      ward.id == other.ward.id &&
      ward.clinicId == other.ward.clinicId &&
      bed.id == other.bed.id &&
      bed.status == other.bed.status &&
      activeAdmission?.id == other.activeAdmission?.id &&
      activeAdmission?.bedId == other.activeAdmission?.bedId &&
      activeReservation?.id == other.activeReservation?.id &&
      activeReservation?.bedId == other.activeReservation?.bedId &&
      activeReservation?.status == other.activeReservation?.status;
}

/// A confirmation is returned only after both mutation response and GET readback.
class WardAdmissionReceipt {
  const WardAdmissionReceipt({
    required this.action,
    required this.referenceId,
    required this.patientId,
    required this.destinationBedId,
  });

  final WardAdmissionAction action;
  final int referenceId;
  final int patientId;
  final int destinationBedId;
}
