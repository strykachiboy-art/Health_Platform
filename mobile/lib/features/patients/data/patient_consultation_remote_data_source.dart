import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/patient_consultation.dart';

class PatientConsultationSessionMismatchException implements Exception {
  const PatientConsultationSessionMismatchException();

  @override
  String toString() => 'Patient consultation session unavailable.';
}

/// P23A.S01-C2: online GET-only consultation history.
class PatientConsultationRemoteDataSource {
  const PatientConsultationRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _roles = <String>{'admin', 'doctor', 'nurse'};

  static const _types = <String>{
    'general',
    'follow_up',
    'specialist',
    'emergency',
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
          !_roles.contains(session.role.trim().toLowerCase())) {
        throw const PatientConsultationSessionMismatchException();
      }

      return session;
    } on PatientConsultationSessionMismatchException {
      rethrow;
    } on Object {
      throw const PatientConsultationSessionMismatchException();
    }
  }

  static bool _sameSession(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<PatientConsultationPage> fetchConsultationHistory({
    required int patientId,
    int page = 1,
    int perPage = 50,
    String? consultationType,
  }) async {
    if (patientId <= 0) {
      throw ArgumentError.value(patientId, 'patientId');
    }
    if (page < 1) {
      throw ArgumentError.value(page, 'page');
    }
    if (perPage < 1 || perPage > 500) {
      throw ArgumentError.value(perPage, 'perPage');
    }
    if (consultationType != null && !_types.contains(consultationType)) {
      throw ArgumentError.value(consultationType, 'consultationType');
    }

    final revision = sessionNavigationGuard.revision;
    if (!_current(revision)) {
      throw const PatientConsultationSessionMismatchException();
    }

    final before = await _requireSession();
    if (!_current(revision)) {
      throw const PatientConsultationSessionMismatchException();
    }

    final path = Uri(
      path: 'api/v1/consultations/patient/$patientId',
      queryParameters: {
        'page': '$page',
        'per_page': '$perPage',
        'consultation_type': ?consultationType,
      },
    ).toString();

    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );

    if (!_current(revision)) {
      throw const PatientConsultationSessionMismatchException();
    }

    final after = await _requireSession();
    if (!_current(revision) || !_sameSession(before, after)) {
      throw const PatientConsultationSessionMismatchException();
    }

    return PatientConsultationPage.fromEnvelope(
      response,
      expectedPatientId: patientId,
      expectedClinicId: before.clinicContextId!,
      requestedPage: page,
      requestedPerPage: perPage,
    );
  }
}
