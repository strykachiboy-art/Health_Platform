// P23I.S06: management-only, online-only, one-shot trip create and dispatch.
// Cache this adapter for the entire workflow lifetime. Never automatically replay.
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_dispatch.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

class AmbulanceDispatchScopeUnavailable implements Exception {
  const AmbulanceDispatchScopeUnavailable();
}

class AmbulanceDispatchEvidenceStale implements Exception {
  const AmbulanceDispatchEvidenceStale();
}

class AmbulanceDispatchAlreadyAttempted implements Exception {
  const AmbulanceDispatchAlreadyAttempted();
}

class AmbulanceDispatchOutcomeUnconfirmed implements Exception {
  const AmbulanceDispatchOutcomeUnconfirmed();
}

class AmbulanceDispatchRemoteDataSource {
  AmbulanceDispatchRemoteDataSource({required this.reader});

  final AmbulanceReadRemoteDataSource reader;
  bool _createReserved = false;
  bool _dispatchReserved = false;
  bool _createWriteAttempted = false;
  bool _dispatchWriteAttempted = false;

  bool get createWriteAttempted => _createWriteAttempted;
  bool get dispatchWriteAttempted => _dispatchWriteAttempted;

  static const managementRoles = <String>{
    'admin',
    'ambulance_coordinator',
    'ambulance_dispatcher',
  };

  bool _current(int revision) =>
      reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      reader.sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _manager(int revision) async {
    if (!_current(revision)) {
      throw const AmbulanceDispatchScopeUnavailable();
    }
    try {
      final state = await reader.loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          !managementRoles.contains(session.role.trim().toLowerCase()) ||
          !_current(revision)) {
        throw const AmbulanceDispatchScopeUnavailable();
      }
      return session;
    } on AmbulanceDispatchScopeUnavailable {
      rethrow;
    } on Object {
      throw const AmbulanceDispatchScopeUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AmbulanceDispatchConfirmation> createTripOnce(
    AmbulanceTripDraft draft,
  ) async {
    if (_createReserved || _createWriteAttempted) {
      throw const AmbulanceDispatchAlreadyAttempted();
    }
    final body = draft.toRequestBody();
    _createReserved = true;
    final revision = reader.sessionNavigationGuard.revision;
    late final AuthTokenResponse before;
    try {
      before = await _manager(revision);
    } on Object {
      _createReserved = false;
      throw const AmbulanceDispatchScopeUnavailable();
    }
    _createWriteAttempted = true;
    try {
      final envelope = await reader.apiClient.postJson(
        'api/v1/ambulance/trips',
        body: body,
        bearerToken: before.accessToken,
      );
      if (!_same(before, await _manager(revision)) || !_current(revision)) {
        throw const AmbulanceDispatchOutcomeUnconfirmed();
      }
      return AmbulanceDispatchConfirmation.created(
        envelope,
        clinicId: before.clinicContextId!,
        requested: draft,
      );
    } on Object {
      // A transport error or malformed response cannot prove POST was undone.
      throw const AmbulanceDispatchOutcomeUnconfirmed();
    }
  }

  Future<AmbulanceDispatchInspection> inspectDispatch({
    required int tripId,
    required int vehicleId,
  }) async {
    AmbulanceTripDraft.validateId(tripId, 'trip_id');
    AmbulanceTripDraft.validateId(vehicleId, 'vehicle_id');
    final revision = reader.sessionNavigationGuard.revision;
    final before = await _manager(revision);
    final trip = await reader.fetchTripDetail(tripId);
    final vehicle = await reader.fetchVehicleDetail(vehicleId);
    if (!_same(before, await _manager(revision)) || !_current(revision)) {
      throw const AmbulanceDispatchScopeUnavailable();
    }
    if (trip.clinicId != before.clinicContextId ||
        vehicle.clinicId != before.clinicContextId ||
        trip.status != 'requested' ||
        trip.vehicleId != null ||
        trip.driverId != null ||
        trip.paramedicId != null ||
        vehicle.status != 'available') {
      throw const AmbulanceDispatchEvidenceStale();
    }
    return AmbulanceDispatchInspection(
      trip: trip,
      vehicle: vehicle,
      sessionRevision: revision,
    );
  }

  Future<AmbulanceDispatchConfirmation> dispatchOnce({
    required AmbulanceDispatchInspection inspection,
    required int driverId,
    int? paramedicId,
  }) async {
    if (_dispatchReserved || _dispatchWriteAttempted) {
      throw const AmbulanceDispatchAlreadyAttempted();
    }
    AmbulanceTripDraft.validateId(driverId, 'driver_id');
    if (paramedicId != null) {
      AmbulanceTripDraft.validateId(paramedicId, 'paramedic_id');
      if (driverId == paramedicId) {
        throw const FormatException('Driver and paramedic must differ.');
      }
    }
    final revision = reader.sessionNavigationGuard.revision;
    if (inspection.sessionRevision != revision) {
      throw const AmbulanceDispatchEvidenceStale();
    }
    _dispatchReserved = true;
    late final AuthTokenResponse before;
    try {
      before = await _manager(revision);
      final fresh = await inspectDispatch(
        tripId: inspection.trip.id,
        vehicleId: inspection.vehicle.id,
      );
      if (!inspection.matches(fresh.trip, fresh.vehicle) ||
          inspection.trip.clinicId != before.clinicContextId ||
          !_same(before, await _manager(revision)) ||
          !_current(revision)) {
        throw const AmbulanceDispatchEvidenceStale();
      }
    } on Object {
      _dispatchReserved = false;
      rethrow;
    }
    _dispatchWriteAttempted = true;
    try {
      final envelope = await reader.apiClient.postJson(
        'api/v1/ambulance/trips/${inspection.trip.id}/dispatch',
        body: <String, dynamic>{
          'vehicle_id': inspection.vehicle.id,
          'driver_id': driverId,
          'paramedic_id': ?paramedicId,
        },
        bearerToken: before.accessToken,
      );
      if (!_same(before, await _manager(revision)) || !_current(revision)) {
        throw const AmbulanceDispatchOutcomeUnconfirmed();
      }
      return AmbulanceDispatchConfirmation.dispatched(
        envelope,
        inspection: inspection,
        driverId: driverId,
        paramedicId: paramedicId,
      );
    } on Object {
      throw const AmbulanceDispatchOutcomeUnconfirmed();
    }
  }
}
