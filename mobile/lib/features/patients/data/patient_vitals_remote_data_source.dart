import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/patient_vitals.dart';

class PatientVitalsSessionMismatchException implements Exception {
  const PatientVitalsSessionMismatchException();

  @override
  String toString() => 'Patient vitals session unavailable.';
}

/// P23A.S01-C1: online GET only, no PHI cache, no auto retry.
class PatientVitalsRemoteDataSource {
  const PatientVitalsRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _readerRoles = <String>{'admin', 'doctor', 'nurse'};

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
        throw const PatientVitalsSessionMismatchException();
      }

      return session;
    } on PatientVitalsSessionMismatchException {
      rethrow;
    } on Object {
      throw const PatientVitalsSessionMismatchException();
    }
  }

  static bool _sameSession(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> _start(int revision) async {
    if (!_current(revision)) {
      throw const PatientVitalsSessionMismatchException();
    }

    final before = await _requireSession();

    if (!_current(revision)) {
      throw const PatientVitalsSessionMismatchException();
    }

    return before;
  }

  Future<void> _verify(AuthTokenResponse before, int revision) async {
    if (!_current(revision)) {
      throw const PatientVitalsSessionMismatchException();
    }

    final after = await _requireSession();

    if (!_current(revision) || !_sameSession(before, after)) {
      throw const PatientVitalsSessionMismatchException();
    }
  }

  Future<PatientVitalsPage> fetchVitalsHistory({
    required int patientId,
    int page = 1,
    int perPage = 50,
  }) async {
    _validatePatient(patientId);

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
      path: 'api/v1/patients/$patientId/vitals',
      queryParameters: {'page': '$page', 'per_page': '$perPage'},
    ).toString();

    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );

    await _verify(before, revision);

    if (response['success'] != true ||
        response['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid vitals API response.');
    }

    return PatientVitalsPage.fromJson(
      response['data'] as Map<String, dynamic>,
      expectedPatientId: patientId,
      requestedPage: page,
      requestedPerPage: perPage,
    );
  }

  /// Returns null only for success:true and data:null.
  Future<PatientVitals?> fetchLatestVitals({required int patientId}) async {
    _validatePatient(patientId);

    final revision = sessionNavigationGuard.revision;
    final before = await _start(revision);

    final response = await apiClient.getJson(
      'api/v1/patients/$patientId/vitals/latest',
      bearerToken: before.accessToken,
    );

    await _verify(before, revision);

    if (response['success'] != true || !response.containsKey('data')) {
      throw const FormatException('Invalid latest vitals response.');
    }

    final data = response['data'];
    if (data == null) return null;

    if (data is! Map<String, dynamic>) {
      throw const FormatException('Invalid latest vitals record.');
    }

    return PatientVitals.fromJson(data, expectedPatientId: patientId);
  }

  static void _validatePatient(int patientId) {
    if (patientId <= 0) {
      throw ArgumentError.value(patientId, 'patientId', 'Must be positive.');
    }
  }
}
