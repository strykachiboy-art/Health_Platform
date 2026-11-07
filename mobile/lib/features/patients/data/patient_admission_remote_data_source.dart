import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/patient_admission.dart';

class PatientAdmissionSessionMismatchException implements Exception {
  const PatientAdmissionSessionMismatchException();

  @override
  String toString() => 'Patient admission session unavailable.';
}

/// P23A.S01-B: GET-only, no persistent PHI cache or automatic retries.
class PatientAdmissionRemoteDataSource {
  const PatientAdmissionRemoteDataSource({
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
        throw const PatientAdmissionSessionMismatchException();
      }

      return session;
    } on PatientAdmissionSessionMismatchException {
      rethrow;
    } on Object {
      throw const PatientAdmissionSessionMismatchException();
    }
  }

  bool _sameSession(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> _start(int revision) async {
    if (!_current(revision)) {
      throw const PatientAdmissionSessionMismatchException();
    }

    final session = await _requireSession();

    if (!_current(revision)) {
      throw const PatientAdmissionSessionMismatchException();
    }

    return session;
  }

  Future<void> _verify(AuthTokenResponse before, int revision) async {
    if (!_current(revision)) {
      throw const PatientAdmissionSessionMismatchException();
    }

    final after = await _requireSession();

    if (!_current(revision) || !_sameSession(before, after)) {
      throw const PatientAdmissionSessionMismatchException();
    }
  }

  Future<PatientAdmissionPage> fetchAdmissionHistory({
    required int patientId,
    int page = 1,
    int perPage = 50,
  }) async {
    _validatePatientId(patientId);

    if (page < 1) {
      throw ArgumentError.value(page, 'page', 'Must be positive.');
    }

    if (perPage < 1 || perPage > 500) {
      throw ArgumentError.value(
        perPage,
        'perPage',
        'Must be between 1 and 500.',
      );
    }

    final revision = sessionNavigationGuard.revision;
    final before = await _start(revision);

    final path = Uri(
      path: 'api/v1/wards/patients/$patientId/admissions',
      queryParameters: {'page': '$page', 'per_page': '$perPage'},
    ).toString();

    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );

    await _verify(before, revision);

    return PatientAdmissionPage.fromJson(
      response,
      expectedPatientId: patientId,
      requestedPage: page,
      requestedPerPage: perPage,
    );
  }

  /// Returns null only for the backend's exact no-active-admission 404.
  /// Other 404s, denied reads, timeouts and malformed responses propagate.
  Future<PatientAdmission?> fetchCurrentAdmission({
    required int patientId,
  }) async {
    _validatePatientId(patientId);

    final revision = sessionNavigationGuard.revision;
    final before = await _start(revision);

    final Map<String, dynamic> response;
    try {
      response = await apiClient.getJson(
        'api/v1/wards/patients/$patientId/current-admission',
        bearerToken: before.accessToken,
      );
    } on ApiException catch (error) {
      if (error.kind == ApiFailureKind.httpStatus &&
          error.statusCode == 404 &&
          error.serverMessage == 'No active admission found') {
        await _verify(before, revision);
        return null;
      }
      rethrow;
    }

    await _verify(before, revision);

    final admission = PatientAdmission.fromJson(
      response,
      expectedPatientId: patientId,
    );

    if (admission.status != PatientAdmissionStatus.admitted) {
      throw const FormatException(
        'Current admission response is not admitted.',
      );
    }

    return admission;
  }

  static void _validatePatientId(int patientId) {
    if (patientId <= 0) {
      throw ArgumentError.value(patientId, 'patientId', 'Must be positive.');
    }
  }
}
