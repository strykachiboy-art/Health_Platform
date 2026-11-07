import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/patient_allergy.dart';

class PatientAllergySessionMismatchException implements Exception {
  const PatientAllergySessionMismatchException();

  @override
  String toString() => 'Patient allergy session unavailable.';
}

/// P23A.S01-A: GET only. No writes, retries, or persisted PHI snapshots.
class PatientAllergyRemoteDataSource {
  const PatientAllergyRemoteDataSource({
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
    'pharmacist',
    'lab_technician',
    'paramedic',
    'emt',
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
        throw const PatientAllergySessionMismatchException();
      }
      return session;
    } on PatientAllergySessionMismatchException {
      rethrow;
    } on Object {
      throw const PatientAllergySessionMismatchException();
    }
  }

  static bool _sameSession(AuthTokenResponse before, AuthTokenResponse after) =>
      before.userId == after.userId &&
      before.clinicContextId == after.clinicContextId &&
      before.role.trim().toLowerCase() == after.role.trim().toLowerCase() &&
      before.accessToken == after.accessToken &&
      before.refreshToken == after.refreshToken;

  Future<PatientAllergyPage> fetchAllergies({
    required int patientId,
    int page = 1,
    int perPage = 50,
    bool activeOnly = false,
    bool includeRefuted = true,
  }) async {
    if (patientId <= 0) {
      throw ArgumentError.value(patientId, 'patientId', 'Must be positive.');
    }
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
    if (!_current(revision)) {
      throw const PatientAllergySessionMismatchException();
    }

    final before = await _requireSession();

    if (!_current(revision)) {
      throw const PatientAllergySessionMismatchException();
    }

    final path = Uri(
      path: 'api/v1/patients/$patientId/allergies',
      queryParameters: {
        'page': '$page',
        'per_page': '$perPage',
        'active_only': '$activeOnly',
        'include_refuted': '$includeRefuted',
      },
    ).toString();

    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );

    if (!_current(revision)) {
      throw const PatientAllergySessionMismatchException();
    }

    final after = await _requireSession();

    if (!_current(revision) || !_sameSession(before, after)) {
      throw const PatientAllergySessionMismatchException();
    }

    if (response['success'] != true ||
        response['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid allergy API response.');
    }

    return PatientAllergyPage.fromJson(
      response['data'] as Map<String, dynamic>,
      expectedPatientId: patientId,
      expectedClinicId: before.clinicContextId!,
      requestedPage: page,
      requestedPerPage: perPage,
    );
  }
}
