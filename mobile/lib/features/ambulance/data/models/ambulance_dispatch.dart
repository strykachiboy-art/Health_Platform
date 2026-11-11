// P23I.S06: server-owned ambulance trip request and dispatch evidence.
// There is no client-created clinic ID, simulated location, or status.
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_read.dart';

class AmbulanceTripDraft {
  const AmbulanceTripDraft({
    required this.tripType,
    this.patientId,
    this.admissionId,
    this.pickupAddress,
    this.destinationAddress,
    this.notes,
  });

  final String tripType;
  final int? patientId;
  final int? admissionId;
  final String? pickupAddress;
  final String? destinationAddress;
  final String? notes;

  static void validateId(int? value, String field) {
    if (value == null || value <= 0 || value > 2147483647) {
      throw FormatException('Invalid ambulance identifier: $field');
    }
  }

  static void _optionalText(String? value, String name, int limit) {
    if (value != null &&
        (value.isEmpty || value.trim() != value || value.length > limit)) {
      throw FormatException('Invalid ambulance text: $name');
    }
  }

  Map<String, dynamic> toRequestBody() {
    if (!ambulanceTripTypes.contains(tripType)) {
      throw const FormatException('Invalid ambulance trip type.');
    }
    if (patientId != null) {
      validateId(patientId, 'patient_id');
    }
    if (admissionId != null) {
      validateId(admissionId, 'admission_id');
    }
    if (const <String>{
          'discharge_transport',
          'inter_facility_transfer',
        }.contains(tripType) &&
        (patientId == null || admissionId == null)) {
      throw const FormatException(
        'Transfer/discharge requires patient and admission.',
      );
    }
    _optionalText(pickupAddress, 'pickup_address', 255);
    _optionalText(destinationAddress, 'destination_address', 255);
    _optionalText(notes, 'notes', 2000);
    return <String, dynamic>{
      'trip_type': tripType,
      if (patientId != null) 'patient_id': patientId,
      if (admissionId != null) 'admission_id': admissionId,
      if (pickupAddress != null) 'pickup_address': pickupAddress,
      if (destinationAddress != null) 'destination_address': destinationAddress,
      if (notes != null) 'notes': notes,
    };
  }
}

class AmbulanceDispatchInspection {
  const AmbulanceDispatchInspection({
    required this.trip,
    required this.vehicle,
    required this.sessionRevision,
  });

  final AmbulanceTripRead trip;
  final AmbulanceVehicleRead vehicle;
  final int sessionRevision;

  bool matches(
    AmbulanceTripRead currentTrip,
    AmbulanceVehicleRead currentVehicle,
  ) =>
      currentTrip.id == trip.id &&
      currentTrip.clinicId == trip.clinicId &&
      currentTrip.tripType == trip.tripType &&
      currentTrip.patientId == trip.patientId &&
      currentTrip.admissionId == trip.admissionId &&
      currentTrip.status == trip.status &&
      currentTrip.vehicleId == trip.vehicleId &&
      currentTrip.driverId == trip.driverId &&
      currentTrip.paramedicId == trip.paramedicId &&
      currentVehicle.id == vehicle.id &&
      currentVehicle.clinicId == vehicle.clinicId &&
      currentVehicle.status == vehicle.status &&
      currentVehicle.capacity == vehicle.capacity &&
      currentVehicle.plateNumber == vehicle.plateNumber;
}

class AmbulanceDispatchConfirmation {
  const AmbulanceDispatchConfirmation({
    required this.trip,
    required this.action,
  });

  final AmbulanceTripRead trip;
  final String action;

  static Map<String, dynamic> _data(Map<String, dynamic> envelope) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Unconfirmed ambulance write envelope.');
    }
    return envelope['data'] as Map<String, dynamic>;
  }

  factory AmbulanceDispatchConfirmation.created(
    Map<String, dynamic> envelope, {
    required int clinicId,
    required AmbulanceTripDraft requested,
  }) {
    final trip = AmbulanceTripRead.fromJson(
      _data(envelope),
      expectedClinicId: clinicId,
    );
    if (trip.status != 'requested' ||
        trip.tripType != requested.tripType ||
        trip.vehicleId != null ||
        trip.driverId != null ||
        trip.paramedicId != null ||
        (requested.patientId != null &&
            trip.patientId != requested.patientId) ||
        (requested.admissionId != null &&
            trip.admissionId != requested.admissionId) ||
        trip.pickupAddress != requested.pickupAddress ||
        trip.destinationAddress != requested.destinationAddress ||
        trip.notes != requested.notes) {
      throw const FormatException('Ambulance trip creation not confirmed.');
    }
    return AmbulanceDispatchConfirmation(trip: trip, action: 'created');
  }

  factory AmbulanceDispatchConfirmation.dispatched(
    Map<String, dynamic> envelope, {
    required AmbulanceDispatchInspection inspection,
    required int driverId,
    required int? paramedicId,
  }) {
    final trip = AmbulanceTripRead.fromJson(
      _data(envelope),
      expectedClinicId: inspection.trip.clinicId,
    );
    if (trip.id != inspection.trip.id ||
        trip.status != 'dispatched' ||
        trip.vehicleId != inspection.vehicle.id ||
        trip.driverId != driverId ||
        trip.paramedicId != paramedicId ||
        trip.tripType != inspection.trip.tripType ||
        trip.patientId != inspection.trip.patientId ||
        trip.admissionId != inspection.trip.admissionId ||
        trip.dispatchedAt == null) {
      throw const FormatException('Ambulance dispatch not confirmed.');
    }
    return AmbulanceDispatchConfirmation(trip: trip, action: 'dispatched');
  }
}
