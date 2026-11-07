import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert_page.dart';

class PatientClinicalAlertSessionMismatchException implements Exception {
  const PatientClinicalAlertSessionMismatchException();

  @override
  String toString() => 'Patient clinical alert session unavailable.';
}

/// Patient-filtered GET only.
///
/// The server owns alert status, severity, action and authorization.
/// This adapter adds patient/clinic response checks but does not
/// acknowledge, resolve, retry or persist alerts.
class PatientClinicalAlertRemoteDataSource {
  const PatientClinicalAlertRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _readerRoles = <String>{
    'admin',
    'super_admin',
    'doctor',
    'nurse',
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
        throw const PatientClinicalAlertSessionMismatchException();
      }

      return session;
    } on PatientClinicalAlertSessionMismatchException {
      rethrow;
    } on Object {
      throw const PatientClinicalAlertSessionMismatchException();
    }
  }

  static bool _sameSession(AuthTokenResponse before, AuthTokenResponse after) =>
      before.userId == after.userId &&
      before.clinicContextId == after.clinicContextId &&
      before.role.trim().toLowerCase() == after.role.trim().toLowerCase() &&
      before.accessToken == after.accessToken &&
      before.refreshToken == after.refreshToken;

  Future<ClinicalAlertPage> fetchPatientAlerts({
    required int patientId,
    int page = 1,
    int perPage = 20,
    ClinicalAlertStatus? status,
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
      throw const PatientClinicalAlertSessionMismatchException();
    }

    final before = await _requireSession();

    if (!_current(revision)) {
      throw const PatientClinicalAlertSessionMismatchException();
    }

    final query = <String, String>{
      'patient_id': '$patientId',
      'page': '$page',
      'per_page': '$perPage',
    };

    if (status != null) {
      query['status'] = status.name;
    }

    final path = Uri(
      path: 'api/v1/clinical-safety/alerts',
      queryParameters: query,
    ).toString();

    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );

    if (!_current(revision)) {
      throw const PatientClinicalAlertSessionMismatchException();
    }

    final after = await _requireSession();

    if (!_current(revision) || !_sameSession(before, after)) {
      throw const PatientClinicalAlertSessionMismatchException();
    }

    // Unlike prescriptions, this endpoint returns a flat envelope.
    final result = ClinicalAlertPage.fromJson(response);

    if (result.page != page ||
        result.perPage != perPage ||
        result.items.length > result.total) {
      throw const FormatException('Invalid patient alert pagination.');
    }

    final seen = <int>{};

    for (final alert in result.items) {
      if (alert.patientId != patientId ||
          alert.clinicId != before.clinicContextId ||
          !seen.add(alert.id) ||
          (status != null && alert.status != status)) {
        throw const FormatException('Patient alert response scope mismatch.');
      }
    }

    return result;
  }
}
