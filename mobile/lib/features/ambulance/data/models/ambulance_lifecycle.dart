import 'dart:convert';

// P23I.S07: backend-owned ambulance trip and fleet lifecycle commands.
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_read.dart';

enum AmbulanceLifecycleKind {
  tripProgress,
  patientLink,
  completeTrip,
  cancelTrip,
  vehicleStatus,
  registerVehicle,
}

class AmbulanceLifecycleCommand {
  const AmbulanceLifecycleCommand({
    required this.kind,
    this.tripId,
    this.vehicleId,
    this.nextStatus,
    this.patientId,
    this.reason,
    this.plateNumber,
    this.equipmentLevel,
    this.capacity,
  });

  final AmbulanceLifecycleKind kind;
  final int? tripId;
  final int? vehicleId;
  final String? nextStatus;
  final int? patientId;
  final String? reason;
  final String? plateNumber;
  final String? equipmentLevel;
  final int? capacity;

  static void validId(int? value, String label) {
    if (value == null || value <= 0 || value > 2147483647) {
      throw FormatException('Invalid $label.');
    }
  }

  void validate() {
    if (kind == AmbulanceLifecycleKind.registerVehicle) {
      final plate = plateNumber;
      if (tripId != null ||
          vehicleId != null ||
          nextStatus != null ||
          patientId != null ||
          reason != null ||
          plate == null ||
          plate.isEmpty ||
          plate != plate.trim() ||
          plate.length > 30 ||
          !RegExp(r'^[A-Z0-9][A-Z0-9 -]*$').hasMatch(plate) ||
          !ambulanceEquipmentLevels.contains(equipmentLevel) ||
          capacity == null ||
          capacity! < 1 ||
          capacity! > 2147483647) {
        throw const FormatException('Invalid vehicle registration.');
      }
      return;
    }
    if (plateNumber != null || equipmentLevel != null || capacity != null) {
      throw const FormatException('Unexpected vehicle registration fields.');
    }
    if (kind == AmbulanceLifecycleKind.vehicleStatus) {
      validId(vehicleId, 'vehicle ID');
      if (tripId != null ||
          patientId != null ||
          reason != null ||
          !const <String>{
            'available',
            'maintenance',
            'out_of_service',
          }.contains(nextStatus)) {
        throw const FormatException('Invalid vehicle status command.');
      }
      return;
    }
    validId(tripId, 'trip ID');
    if (vehicleId != null) {
      throw const FormatException('Unexpected vehicle ID.');
    }
    switch (kind) {
      case AmbulanceLifecycleKind.tripProgress:
        if (patientId != null ||
            reason != null ||
            !const <String>{
              'en_route_to_pickup',
              'at_pickup',
              'patient_on_board',
              'en_route_to_destination',
            }.contains(nextStatus)) {
          throw const FormatException('Invalid trip progress command.');
        }
        return;
      case AmbulanceLifecycleKind.patientLink:
        validId(patientId, 'patient ID');
        if (nextStatus != null || reason != null) {
          throw const FormatException('Invalid patient link command.');
        }
        return;
      case AmbulanceLifecycleKind.cancelTrip:
        if (patientId != null ||
            nextStatus != null ||
            reason == null ||
            reason!.isEmpty ||
            reason!.trim() != reason ||
            reason!.length > 255) {
          throw const FormatException('Invalid cancellation reason.');
        }
        return;
      case AmbulanceLifecycleKind.completeTrip:
        if (patientId != null || nextStatus != null || reason != null) {
          throw const FormatException('Invalid trip completion command.');
        }
        return;
      case AmbulanceLifecycleKind.vehicleStatus:
      case AmbulanceLifecycleKind.registerVehicle:
        throw StateError('Unexpected lifecycle action.');
    }
  }

  String get fingerprint => '$path::${jsonEncode(body)}';

  String get path {
    if (kind == AmbulanceLifecycleKind.registerVehicle) {
      return 'api/v1/ambulance/vehicles';
    }
    if (kind == AmbulanceLifecycleKind.vehicleStatus) {
      return 'api/v1/ambulance/vehicles/$vehicleId/status';
    }
    final base = 'api/v1/ambulance/trips/$tripId';
    switch (kind) {
      case AmbulanceLifecycleKind.tripProgress:
        return '$base/status';
      case AmbulanceLifecycleKind.patientLink:
        return '$base/patient';
      case AmbulanceLifecycleKind.completeTrip:
        return '$base/complete';
      case AmbulanceLifecycleKind.cancelTrip:
        return '$base/cancel';
      case AmbulanceLifecycleKind.vehicleStatus:
      case AmbulanceLifecycleKind.registerVehicle:
        throw StateError('Invalid trip operation.');
    }
  }

  Map<String, dynamic> get body {
    switch (kind) {
      case AmbulanceLifecycleKind.tripProgress:
      case AmbulanceLifecycleKind.vehicleStatus:
        return <String, dynamic>{'status': nextStatus};
      case AmbulanceLifecycleKind.patientLink:
        return <String, dynamic>{'patient_id': patientId};
      case AmbulanceLifecycleKind.completeTrip:
        return <String, dynamic>{};
      case AmbulanceLifecycleKind.cancelTrip:
        return <String, dynamic>{'reason': reason};
      case AmbulanceLifecycleKind.registerVehicle:
        return <String, dynamic>{
          'plate_number': plateNumber,
          'equipment_level': equipmentLevel,
          'capacity': capacity,
        };
    }
  }
}

/// Obtained from the protected GET adapter, never from route parameters alone.
class AmbulanceLifecycleInspection {
  const AmbulanceLifecycleInspection({
    required this.kind,
    required this.revision,
    required this.fingerprint,
    this.trip,
    this.vehicle,
  });
  final AmbulanceLifecycleKind kind;
  final int revision;
  final String fingerprint;
  final AmbulanceTripRead? trip;
  final AmbulanceVehicleRead? vehicle;

  bool same(AmbulanceLifecycleInspection other) {
    final t = trip;
    final ot = other.trip;
    final v = vehicle;
    final ov = other.vehicle;
    return kind == other.kind &&
        revision == other.revision &&
        fingerprint == other.fingerprint &&
        (t == null && ot == null ||
            t != null &&
                ot != null &&
                t.id == ot.id &&
                t.clinicId == ot.clinicId &&
                t.status == ot.status &&
                t.patientId == ot.patientId &&
                t.admissionId == ot.admissionId &&
                t.vehicleId == ot.vehicleId &&
                t.driverId == ot.driverId &&
                t.paramedicId == ot.paramedicId &&
                t.tripType == ot.tripType &&
                t.invoiceId == ot.invoiceId &&
                t.dispatchedAt == ot.dispatchedAt &&
                t.pickupAt == ot.pickupAt &&
                t.completedAt == ot.completedAt &&
                t.cancelledAt == ot.cancelledAt) &&
        (v == null && ov == null ||
            v != null &&
                ov != null &&
                v.id == ov.id &&
                v.clinicId == ov.clinicId &&
                v.status == ov.status &&
                v.plateNumber == ov.plateNumber &&
                v.capacity == ov.capacity &&
                v.equipmentLevel == ov.equipmentLevel &&
                v.updatedAt == ov.updatedAt);
  }
}

class AmbulanceLifecycleConfirmation {
  const AmbulanceLifecycleConfirmation({
    required this.kind,
    this.trip,
    this.vehicle,
  });
  final AmbulanceLifecycleKind kind;
  final AmbulanceTripRead? trip;
  final AmbulanceVehicleRead? vehicle;
}
