// P23I.S07 — authorized ambulance lifecycle, one shot per adapter instance.
// The backend owns all clinical/dispatch transitions, clinic boundaries and audit.
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_lifecycle.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_read.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

class AmbulanceLifecycleUnauthorized implements Exception {
  const AmbulanceLifecycleUnauthorized();
}

class AmbulanceLifecycleEvidenceChanged implements Exception {
  const AmbulanceLifecycleEvidenceChanged();
}

class AmbulanceLifecycleAlreadyAttempted implements Exception {
  const AmbulanceLifecycleAlreadyAttempted();
}

class AmbulanceLifecycleOutcomeUnconfirmed implements Exception {
  const AmbulanceLifecycleOutcomeUnconfirmed();
}

class AmbulanceLifecycleRemoteDataSource {
  AmbulanceLifecycleRemoteDataSource({required this.reader});
  final AmbulanceReadRemoteDataSource reader;
  bool _reserved = false;
  bool _attempted = false;
  AmbulanceLifecycleInspection? _lastInspection;
  bool get writeAttempted => _attempted;

  static const _managers = <String>{
    'admin',
    'ambulance_coordinator',
    'ambulance_dispatcher',
  };
  static const _crew = <String>{
    'admin',
    'ambulance_coordinator',
    'ambulance_dispatcher',
    'driver',
    'paramedic',
    'emt',
  };
  static const _fleetManagers = <String>{'admin', 'ambulance_coordinator'};

  bool _current(int revision) =>
      reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      reader.sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _session(
    int revision,
    AmbulanceLifecycleKind kind,
  ) async {
    if (!_current(revision)) throw const AmbulanceLifecycleUnauthorized();
    try {
      final state = await reader.loadSession();
      final s = state.session;
      final allowed = switch (kind) {
        AmbulanceLifecycleKind.tripProgress ||
        AmbulanceLifecycleKind.completeTrip => _crew,
        AmbulanceLifecycleKind.vehicleStatus ||
        AmbulanceLifecycleKind.registerVehicle => _fleetManagers,
        _ => _managers,
      };
      if (state.disposition != AuthSessionDisposition.ready ||
          s == null ||
          s.userId <= 0 ||
          s.clinicContextId == null ||
          s.clinicContextId! <= 0 ||
          s.accessToken.isEmpty ||
          s.accessToken.trim() != s.accessToken ||
          !allowed.contains(s.role.trim().toLowerCase()) ||
          !_current(revision)) {
        throw const AmbulanceLifecycleUnauthorized();
      }
      return s;
    } on AmbulanceLifecycleUnauthorized {
      rethrow;
    } on Object {
      throw const AmbulanceLifecycleUnauthorized();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AmbulanceLifecycleInspection> inspect(
    AmbulanceLifecycleCommand command,
  ) async {
    command.validate();
    final revision = reader.sessionNavigationGuard.revision;
    final before = await _session(revision, command.kind);
    AmbulanceTripRead? trip;
    AmbulanceVehicleRead? vehicle;
    if (command.kind == AmbulanceLifecycleKind.vehicleStatus) {
      vehicle = await reader.fetchVehicleDetail(command.vehicleId!);
    } else if (command.kind != AmbulanceLifecycleKind.registerVehicle) {
      trip = await reader.fetchTripDetail(command.tripId!);
      if (trip.vehicleId != null) {
        vehicle = await reader.fetchVehicleDetail(trip.vehicleId!);
      }
    }
    if (!_same(before, await _session(revision, command.kind)) ||
        !_current(revision) ||
        (trip != null && trip.clinicId != before.clinicContextId) ||
        (vehicle != null && vehicle.clinicId != before.clinicContextId)) {
      throw const AmbulanceLifecycleUnauthorized();
    }
    _eligible(command, trip: trip, vehicle: vehicle);
    final result = AmbulanceLifecycleInspection(
      kind: command.kind,
      revision: revision,
      fingerprint: command.fingerprint,
      trip: trip,
      vehicle: vehicle,
    );
    _lastInspection = result;
    return result;
  }

  static const _nextStatus = <String, String>{
    'dispatched': 'en_route_to_pickup',
    'en_route_to_pickup': 'at_pickup',
    'at_pickup': 'patient_on_board',
    'patient_on_board': 'en_route_to_destination',
  };

  static void _eligible(
    AmbulanceLifecycleCommand command, {
    AmbulanceTripRead? trip,
    AmbulanceVehicleRead? vehicle,
  }) {
    switch (command.kind) {
      case AmbulanceLifecycleKind.tripProgress:
        if (trip == null ||
            _nextStatus[trip.status] != command.nextStatus ||
            trip.vehicleId == null ||
            vehicle?.status != 'on_trip' ||
            (command.nextStatus == 'patient_on_board' &&
                trip.patientId == null)) {
          throw const AmbulanceLifecycleEvidenceChanged();
        }
        return;
      case AmbulanceLifecycleKind.patientLink:
        if (trip == null ||
            trip.status == 'completed' ||
            trip.status == 'cancelled' ||
            trip.patientId != null) {
          throw const AmbulanceLifecycleEvidenceChanged();
        }
        return;
      case AmbulanceLifecycleKind.completeTrip:
        if (trip == null ||
            trip.status != 'en_route_to_destination' ||
            trip.patientId == null ||
            trip.vehicleId == null ||
            vehicle?.status != 'on_trip') {
          throw const AmbulanceLifecycleEvidenceChanged();
        }
        return;
      case AmbulanceLifecycleKind.cancelTrip:
        if (trip == null ||
            trip.status == 'completed' ||
            trip.status == 'cancelled' ||
            (trip.vehicleId != null && vehicle == null)) {
          throw const AmbulanceLifecycleEvidenceChanged();
        }
        return;
      case AmbulanceLifecycleKind.vehicleStatus:
        if (vehicle == null ||
            vehicle.status == 'on_trip' ||
            vehicle.status == command.nextStatus) {
          throw const AmbulanceLifecycleEvidenceChanged();
        }
        return;
      case AmbulanceLifecycleKind.registerVehicle:
        // No vehicle exists yet; backend enforces unique plate/clinic identity.
        break;
    }
  }

  Future<AmbulanceLifecycleConfirmation> applyOnce({
    required AmbulanceLifecycleCommand command,
    required AmbulanceLifecycleInspection inspection,
  }) async {
    if (_reserved || _attempted) {
      throw const AmbulanceLifecycleAlreadyAttempted();
    }
    command.validate();
    if (!identical(_lastInspection, inspection) ||
        inspection.kind != command.kind ||
        inspection.fingerprint != command.fingerprint ||
        inspection.revision != reader.sessionNavigationGuard.revision) {
      throw const AmbulanceLifecycleEvidenceChanged();
    }
    _reserved = true;
    final revision = reader.sessionNavigationGuard.revision;
    late final AuthTokenResponse before;
    try {
      before = await _session(revision, command.kind);
      final fresh = await inspect(command);
      if (!inspection.same(fresh) ||
          !_same(before, await _session(revision, command.kind)) ||
          !_current(revision)) {
        throw const AmbulanceLifecycleEvidenceChanged();
      }
    } on Object {
      _reserved = false;
      rethrow;
    }
    _attempted = true;
    try {
      final envelope =
          command.kind == AmbulanceLifecycleKind.tripProgress ||
              command.kind == AmbulanceLifecycleKind.vehicleStatus
          ? await reader.apiClient.patchJson(
              command.path,
              body: command.body,
              bearerToken: before.accessToken,
            )
          : await reader.apiClient.postJson(
              command.path,
              body: command.body,
              bearerToken: before.accessToken,
            );
      if (!_same(before, await _session(revision, command.kind)) ||
          !_current(revision)) {
        throw const AmbulanceLifecycleOutcomeUnconfirmed();
      }
      return _confirm(envelope, command, inspection, before.clinicContextId!);
    } on Object {
      // Never infer failure/rollback after a write might have reached the server.
      throw const AmbulanceLifecycleOutcomeUnconfirmed();
    }
  }

  static AmbulanceLifecycleConfirmation _confirm(
    Map<String, dynamic> envelope,
    AmbulanceLifecycleCommand command,
    AmbulanceLifecycleInspection inspection,
    int clinicId,
  ) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Missing ambulance server confirmation.');
    }
    final row = envelope['data'] as Map<String, dynamic>;
    if (command.kind == AmbulanceLifecycleKind.vehicleStatus ||
        command.kind == AmbulanceLifecycleKind.registerVehicle) {
      final vehicle = AmbulanceVehicleRead.fromJson(
        row,
        expectedClinicId: clinicId,
      );
      if (vehicle.status !=
              (command.kind == AmbulanceLifecycleKind.registerVehicle
                  ? 'available'
                  : command.nextStatus) ||
          (command.kind == AmbulanceLifecycleKind.registerVehicle &&
              (vehicle.plateNumber != command.plateNumber ||
                  vehicle.equipmentLevel != command.equipmentLevel ||
                  vehicle.capacity != command.capacity)) ||
          (command.kind == AmbulanceLifecycleKind.vehicleStatus &&
              (vehicle.id != command.vehicleId ||
                  vehicle.plateNumber != inspection.vehicle!.plateNumber))) {
        throw const FormatException('Vehicle change not confirmed.');
      }
      return AmbulanceLifecycleConfirmation(
        kind: command.kind,
        vehicle: vehicle,
      );
    }
    final trip = AmbulanceTripRead.fromJson(row, expectedClinicId: clinicId);
    final old = inspection.trip!;
    if (trip.id != command.tripId ||
        trip.clinicId != old.clinicId ||
        trip.tripType != old.tripType ||
        trip.vehicleId != old.vehicleId ||
        trip.driverId != old.driverId ||
        trip.paramedicId != old.paramedicId ||
        trip.admissionId != old.admissionId ||
        trip.invoiceId != old.invoiceId) {
      throw const FormatException('Trip identity changed unexpectedly.');
    }
    final confirmed = switch (command.kind) {
      AmbulanceLifecycleKind.tripProgress =>
        trip.status == command.nextStatus &&
            trip.patientId == old.patientId &&
            (trip.status != 'at_pickup' || trip.pickupAt != null),
      AmbulanceLifecycleKind.patientLink =>
        trip.patientId == command.patientId && trip.status == old.status,
      AmbulanceLifecycleKind.completeTrip =>
        trip.status == 'completed' &&
            trip.completedAt != null &&
            trip.patientId == old.patientId,
      AmbulanceLifecycleKind.cancelTrip =>
        trip.status == 'cancelled' &&
            trip.cancelledAt != null &&
            trip.cancellationReason == command.reason &&
            trip.patientId == old.patientId,
      _ => false,
    };
    if (!confirmed) {
      throw const FormatException('Trip change not confirmed.');
    }
    return AmbulanceLifecycleConfirmation(kind: command.kind, trip: trip);
  }
}
