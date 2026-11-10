import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/patient_prescription.dart';
import 'models/patient_prescription_detail.dart';

class PatientPrescriptionSessionMismatchException implements Exception {
  const PatientPrescriptionSessionMismatchException();

  @override
  String toString() => 'Patient prescription session unavailable.';
}

/// GET-only patient prescription history and server-authoritative detail.
///
/// Normal readers: doctor and pharmacist.
/// Emergency access is not inferred from a patient-detail route.
/// No persistent PHI cache, clinical writes or automatic retries.
class PatientPrescriptionRemoteDataSource {
  const PatientPrescriptionRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _readerRoles = <String>{'doctor', 'pharmacist'};

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
        throw const PatientPrescriptionSessionMismatchException();
      }

      return session;
    } on PatientPrescriptionSessionMismatchException {
      rethrow;
    } on Object {
      throw const PatientPrescriptionSessionMismatchException();
    }
  }

  static bool _sameSession(AuthTokenResponse before, AuthTokenResponse after) =>
      before.userId == after.userId &&
      before.clinicContextId == after.clinicContextId &&
      before.role.trim().toLowerCase() == after.role.trim().toLowerCase() &&
      before.accessToken == after.accessToken &&
      before.refreshToken == after.refreshToken;

  /// P23H.S01: a second authenticated GET, never using a list snapshot as
  /// evidence for a specific medication order. The server confirms every
  /// patient, clinic, prescription and medication item identity.
  Future<PatientPrescriptionDetail> fetchPrescriptionDetail({
    required int prescriptionId,
    required int expectedPatientId,
  }) async {
    if (prescriptionId <= 0 ||
        prescriptionId > 2147483647 ||
        expectedPatientId <= 0 ||
        expectedPatientId > 2147483647) {
      throw ArgumentError('Invalid prescription detail identity.');
    }
    final revision = sessionNavigationGuard.revision;
    if (!_current(revision)) {
      throw const PatientPrescriptionSessionMismatchException();
    }
    final before = await _requireSession();
    if (!_current(revision)) {
      throw const PatientPrescriptionSessionMismatchException();
    }
    final response = await apiClient.getJson(
      'api/v1/prescriptions/$prescriptionId',
      bearerToken: before.accessToken,
    );
    if (!_current(revision)) {
      throw const PatientPrescriptionSessionMismatchException();
    }
    final after = await _requireSession();
    if (!_current(revision) || !_sameSession(before, after)) {
      throw const PatientPrescriptionSessionMismatchException();
    }
    return PatientPrescriptionDetail.fromEnvelope(
      response,
      expectedClinicId: before.clinicContextId!,
      expectedPatientId: expectedPatientId,
      expectedPrescriptionId: prescriptionId,
    );
  }

  Future<PatientPrescriptionPage> fetchPrescriptions({
    required int patientId,
    int page = 1,
    int perPage = 20,
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

    final revision = sessionNavigationGuard.revision;
    if (!_current(revision)) {
      throw const PatientPrescriptionSessionMismatchException();
    }

    final before = await _requireSession();
    if (!_current(revision)) {
      throw const PatientPrescriptionSessionMismatchException();
    }

    final path = Uri(
      path: 'api/v1/prescriptions/patients/$patientId',
      queryParameters: {'page': '$page', 'per_page': '$perPage'},
    ).toString();

    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );

    if (!_current(revision)) {
      throw const PatientPrescriptionSessionMismatchException();
    }

    final after = await _requireSession();
    if (!_current(revision) || !_sameSession(before, after)) {
      throw const PatientPrescriptionSessionMismatchException();
    }

    return PatientPrescriptionPage.fromEnvelope(
      response,
      expectedPatientId: patientId,
      expectedClinicId: before.clinicContextId!,
      requestedPage: page,
      requestedPerPage: perPage,
    );
  }
}
