import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/ambulance_read.dart';

class AmbulanceReadSessionUnavailable implements Exception {
  const AmbulanceReadSessionUnavailable();
}

/// P23I.S05: read-only, active-staff ambulance vehicle and trip visibility.
/// All authority belongs to the backend; no dispatch or status calls exist here.
class AmbulanceReadRemoteDataSource {
  const AmbulanceReadRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const allowedRoles = <String>{
    'admin',
    'ambulance_coordinator',
    'ambulance_dispatcher',
    'driver',
    'paramedic',
    'emt',
  };

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _staff(int revision) async {
    if (!_current(revision)) throw const AmbulanceReadSessionUnavailable();
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          !allowedRoles.contains(session.role.trim().toLowerCase()) ||
          !_current(revision)) {
        throw const AmbulanceReadSessionUnavailable();
      }
      return session;
    } on AmbulanceReadSessionUnavailable {
      rethrow;
    } on Object {
      throw const AmbulanceReadSessionUnavailable();
    }
  }

  static bool _same(AuthTokenResponse first, AuthTokenResponse second) =>
      first.userId == second.userId &&
      first.clinicContextId == second.clinicContextId &&
      first.role.trim().toLowerCase() == second.role.trim().toLowerCase() &&
      first.accessToken == second.accessToken &&
      first.refreshToken == second.refreshToken;

  Future<T> _read<T>(
    String path,
    T Function(Map<String, dynamic>, int) parse,
  ) async {
    final revision = sessionNavigationGuard.revision;
    final before = await _staff(revision);
    final envelope = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    final after = await _staff(revision);
    if (!_same(before, after) || !_current(revision)) {
      throw const AmbulanceReadSessionUnavailable();
    }
    return parse(envelope, before.clinicContextId!);
  }

  static void _pageArgs(int page, int perPage) {
    if (page < 1 || page > 2147483647 || perPage < 1 || perPage > 100) {
      throw ArgumentError('Invalid ambulance pagination.');
    }
  }

  static void _status(String? status, Set<String> allowed) {
    if (status != null && !allowed.contains(status)) {
      throw ArgumentError.value(status, 'status', 'Unsupported status filter.');
    }
  }

  Future<AmbulanceReadPage<AmbulanceVehicleRead>> fetchVehicles({
    int page = 1,
    int perPage = 20,
    String? status,
  }) {
    _pageArgs(page, perPage);
    _status(status, ambulanceVehicleStatuses);
    final query =
        '?page=$page&per_page=$perPage'
        '${status == null ? '' : '&status=$status'}';
    return _read(
      'api/v1/ambulance/vehicles$query',
      (envelope, clinicId) => parseAmbulanceReadPage<AmbulanceVehicleRead>(
        envelope,
        requestedPage: page,
        requestedPerPage: perPage,
        decode: (row) =>
            AmbulanceVehicleRead.fromJson(row, expectedClinicId: clinicId),
        identity: (vehicle) => vehicle.id,
      ),
    );
  }

  Future<AmbulanceReadPage<AmbulanceTripRead>> fetchTrips({
    int page = 1,
    int perPage = 20,
    String? status,
  }) {
    _pageArgs(page, perPage);
    _status(status, ambulanceTripStatuses);
    final query =
        '?page=$page&per_page=$perPage'
        '${status == null ? '' : '&status=$status'}';
    return _read(
      'api/v1/ambulance/trips$query',
      (envelope, clinicId) => parseAmbulanceReadPage<AmbulanceTripRead>(
        envelope,
        requestedPage: page,
        requestedPerPage: perPage,
        decode: (row) =>
            AmbulanceTripRead.fromJson(row, expectedClinicId: clinicId),
        identity: (trip) => trip.id,
      ),
    );
  }

  Future<AmbulanceVehicleRead> fetchVehicleDetail(int vehicleId) {
    if (vehicleId <= 0 || vehicleId > 2147483647) {
      throw ArgumentError.value(vehicleId, 'vehicleId', 'Invalid vehicle.');
    }
    return _read('api/v1/ambulance/vehicles/$vehicleId', (envelope, clinicId) {
      final row = _single(envelope);
      final vehicle = AmbulanceVehicleRead.fromJson(
        row,
        expectedClinicId: clinicId,
      );
      if (vehicle.id != vehicleId) {
        throw const FormatException('Vehicle detail identity mismatch.');
      }
      return vehicle;
    });
  }

  Future<AmbulanceTripRead> fetchTripDetail(int tripId) {
    if (tripId <= 0 || tripId > 2147483647) {
      throw ArgumentError.value(tripId, 'tripId', 'Invalid trip.');
    }
    return _read('api/v1/ambulance/trips/$tripId', (envelope, clinicId) {
      final row = _single(envelope);
      final trip = AmbulanceTripRead.fromJson(row, expectedClinicId: clinicId);
      if (trip.id != tripId) {
        throw const FormatException('Trip detail identity mismatch.');
      }
      return trip;
    });
  }

  static Map<String, dynamic> _single(Map<String, dynamic> envelope) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid ambulance detail envelope.');
    }
    return envelope['data'] as Map<String, dynamic>;
  }
}
