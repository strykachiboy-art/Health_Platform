import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/ward_overview.dart';

class WardDashboardSessionException implements Exception {
  const WardDashboardSessionException();

  @override
  String toString() => 'Ward dashboard session unavailable.';
}

/// P23E.B is intentionally GET-only. Bed assignment/admission and maintenance
/// writes remain server-owned and are not part of this readback bundle.
class WardDashboardRemoteDataSource {
  const WardDashboardRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _viewRoles = <String>{
    'admin',
    'doctor',
    'nurse',
    'receptionist',
  };

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _authorized() async {
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.trim().isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          !_viewRoles.contains(session.role.trim().toLowerCase())) {
        throw const WardDashboardSessionException();
      }
      return session;
    } on WardDashboardSessionException {
      rethrow;
    } on Object {
      throw const WardDashboardSessionException();
    }
  }

  bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<T> _get<T>(
    String path,
    T Function(Map<String, dynamic>, int) parse,
  ) async {
    final revision = sessionNavigationGuard.revision;
    if (!_current(revision)) {
      throw const WardDashboardSessionException();
    }
    final before = await _authorized();
    if (!_current(revision)) {
      throw const WardDashboardSessionException();
    }
    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    if (!_current(revision)) {
      throw const WardDashboardSessionException();
    }
    final after = await _authorized();
    if (!_current(revision) || !_same(before, after)) {
      throw const WardDashboardSessionException();
    }
    return parse(response, before.clinicContextId!);
  }

  static void _page(int page, int perPage) {
    if (page < 1 || perPage < 1 || perPage > 500) {
      throw ArgumentError('Page must be positive; perPage must be 1–500.');
    }
  }

  static void _id(int id, String name) {
    if (id <= 0 || id > 2147483647) {
      throw ArgumentError.value(id, name, 'Must be positive.');
    }
  }

  Future<WardPage<WardRecord>> fetchWards({
    int page = 1,
    int perPage = 20,
    WardKind? type,
  }) {
    _page(page, perPage);
    final path = Uri(
      path: 'api/v1/wards',
      queryParameters: <String, String>{
        'page': '$page',
        'per_page': '$perPage',
        if (type != null) 'ward_type': type.name,
      },
    ).toString();
    return _get(path, (json, clinicId) {
      return WardPage<WardRecord>.fromJson(
        json,
        requestedPage: page,
        requestedPerPage: perPage,
        parseRow: (row) => WardRecord.fromJson(row, expectedClinicId: clinicId),
      );
    });
  }

  Future<WardPage<WardBedRecord>> fetchBeds({
    required int wardId,
    int page = 1,
    int perPage = 30,
    WardBedStatus? status,
  }) {
    _id(wardId, 'wardId');
    _page(page, perPage);
    final path = Uri(
      path: 'api/v1/wards/$wardId/beds',
      queryParameters: <String, String>{
        'page': '$page',
        'per_page': '$perPage',
        if (status != null) 'status': status.name,
      },
    ).toString();
    return _get(path, (json, _) {
      return WardPage<WardBedRecord>.fromJson(
        json,
        requestedPage: page,
        requestedPerPage: perPage,
        parseRow: (row) => WardBedRecord.fromJson(row, expectedWardId: wardId),
      );
    });
  }

  Future<WardOccupancy> fetchOccupancy({required int wardId}) {
    _id(wardId, 'wardId');
    return _get('api/v1/wards/$wardId/occupancy', (json, clinicId) {
      return WardOccupancy.fromJson(
        json,
        expectedWardId: wardId,
        expectedClinicId: clinicId,
      );
    });
  }
}
