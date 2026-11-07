import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/appointment_record.dart';

class AppointmentReadSessionMismatchException implements Exception {
  const AppointmentReadSessionMismatchException();

  @override
  String toString() => 'Appointment session is unavailable or changed.';
}

/// Read-only, online-only backend appointment lists.
/// No local PHI cache, scheduling mutation or implicit idempotency retry.
class AppointmentRemoteDataSource {
  const AppointmentRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _readerRoles = <String>{
    'admin',
    'doctor',
    'nurse',
    'receptionist',
  };

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _requireSession() async {
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.trim().isEmpty ||
          session.accessToken != session.accessToken.trim() ||
          !_readerRoles.contains(session.role.trim().toLowerCase())) {
        throw const AppointmentReadSessionMismatchException();
      }
      return session;
    } on AppointmentReadSessionMismatchException {
      rethrow;
    } on Object {
      throw const AppointmentReadSessionMismatchException();
    }
  }

  static bool _sameSession(AuthTokenResponse before, AuthTokenResponse after) =>
      before.userId == after.userId &&
      before.clinicContextId == after.clinicContextId &&
      before.role.trim().toLowerCase() == after.role.trim().toLowerCase() &&
      before.accessToken == after.accessToken &&
      before.refreshToken == after.refreshToken;

  Future<AppointmentPage> fetchForPatient({
    required int patientId,
    int page = 1,
    int perPage = 20,
  }) async {
    if (patientId <= 0) {
      throw ArgumentError.value(patientId, 'patientId', 'Must be positive.');
    }
    _validatePagination(page, perPage);
    return _fetch(
      path: 'api/v1/appointments/patient/$patientId',
      page: page,
      perPage: perPage,
      expectedPatientId: patientId,
    );
  }

  Future<AppointmentPage> fetchForStaff({
    required int staffId,
    String? date,
    int page = 1,
    int perPage = 20,
  }) async {
    if (staffId <= 0) {
      throw ArgumentError.value(staffId, 'staffId', 'Must be positive.');
    }
    _validatePagination(page, perPage);
    if (date != null) {
      final parsed = DateTime.tryParse(date);
      if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(date) ||
          parsed == null ||
          parsed.year.toString().padLeft(4, '0') != date.substring(0, 4) ||
          parsed.month.toString().padLeft(2, '0') != date.substring(5, 7) ||
          parsed.day.toString().padLeft(2, '0') != date.substring(8, 10)) {
        throw ArgumentError.value(date, 'date', 'Use a valid YYYY-MM-DD.');
      }
    }
    return _fetch(
      path: 'api/v1/appointments/staff/$staffId',
      page: page,
      perPage: perPage,
      expectedStaffId: staffId,
      date: date,
    );
  }

  static void _validatePagination(int page, int perPage) {
    if (page <= 0) {
      throw ArgumentError.value(page, 'page', 'Must be positive.');
    }
    if (perPage <= 0 || perPage > 500) {
      throw ArgumentError.value(perPage, 'perPage', 'Must be 1..500.');
    }
  }

  Future<AppointmentPage> _fetch({
    required String path,
    required int page,
    required int perPage,
    int? expectedPatientId,
    int? expectedStaffId,
    String? date,
  }) async {
    final revision = sessionNavigationGuard.revision;
    if (!_current(revision)) {
      throw const AppointmentReadSessionMismatchException();
    }
    final before = await _requireSession();
    if (!_current(revision)) {
      throw const AppointmentReadSessionMismatchException();
    }

    final query = <String, String>{
      'page': '$page',
      'per_page': '$perPage',
      'date': ?date,
    };
    final requestPath = Uri(path: path, queryParameters: query).toString();
    final response = await apiClient.getJson(
      requestPath,
      bearerToken: before.accessToken,
    );

    if (!_current(revision)) {
      throw const AppointmentReadSessionMismatchException();
    }
    final after = await _requireSession();
    if (!_current(revision) || !_sameSession(before, after)) {
      throw const AppointmentReadSessionMismatchException();
    }

    return AppointmentPage.fromEnvelope(
      response,
      expectedClinicId: before.clinicContextId!,
      expectedPatientId: expectedPatientId,
      expectedStaffId: expectedStaffId,
      requestedPage: page,
      requestedPerPage: perPage,
    );
  }
}
