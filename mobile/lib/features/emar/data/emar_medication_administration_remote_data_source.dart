// P23F.S04: one deliberate, online-only barcode-bound administration.
// Never replay an uncertain administration POST, even with idempotency.
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/emar_barcode_verification.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

class EmarAdministerScopeUnavailable implements Exception {
  const EmarAdministerScopeUnavailable();
}

class EmarAdministerEvidenceChanged implements Exception {
  const EmarAdministerEvidenceChanged();
}

class EmarAdministerOutcomeUnconfirmed implements Exception {
  const EmarAdministerOutcomeUnconfirmed();
}

/// One instance per selected administration, never router-global. Once POST
/// begins, all further submissions on this instance are blocked permanently.
class EmarMedicationAdministrationRemoteDataSource {
  EmarMedicationAdministrationRemoteDataSource({
    required this.apiClient,
    required this.readSource,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final EmarReadRemoteDataSource readSource;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  bool _started = false;
  bool get attemptStarted => _started;

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _nurse(int revision) async {
    if (!_current(revision)) throw const EmarAdministerScopeUnavailable();
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.role.trim().toLowerCase() != 'nurse' ||
          session.accessToken.isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          !_current(revision)) {
        throw const EmarAdministerScopeUnavailable();
      }
      return session;
    } on EmarAdministerScopeUnavailable {
      rethrow;
    } on Object {
      throw const EmarAdministerScopeUnavailable();
    }
  }

  static bool _sameSession(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  static bool _sameClinical(EmarAdministration a, EmarAdministration b) =>
      a.id == b.id &&
      a.clinicId == b.clinicId &&
      a.patientId == b.patientId &&
      a.admissionId == b.admissionId &&
      a.prescriptionId == b.prescriptionId &&
      a.prescriptionItemId == b.prescriptionItemId &&
      a.status == b.status &&
      a.recordVersion == b.recordVersion &&
      a.sourceOrderVersion == b.sourceOrderVersion &&
      a.doseValue == b.doseValue &&
      a.doseUnit == b.doseUnit &&
      a.routeCode == b.routeCode &&
      a.scheduledAt == b.scheduledAt;

  Future<EmarAdministration> administerOnce({
    required EmarAdministration reviewed,
    required EmarBarcodeVerification verified,
    required String operationKey,
    String? notes,
  }) async {
    if (_started) throw const EmarAdministerOutcomeUnconfirmed();
    if (operationKey.isEmpty ||
        operationKey.trim() != operationKey ||
        operationKey.length > 255 ||
        (notes != null && notes.length > 4000)) {
      throw const EmarAdministerEvidenceChanged();
    }
    if (!identical(apiClient, readSource.apiClient) ||
        !identical(sessionNavigationGuard, readSource.sessionNavigationGuard)) {
      throw const EmarAdministerScopeUnavailable();
    }
    if (!reviewed.isPending ||
        !verified.verified ||
        verified.clinicId != reviewed.clinicId ||
        verified.patientId != reviewed.patientId ||
        verified.admissionId != reviewed.admissionId ||
        verified.administrationId != reviewed.id ||
        verified.sourceOrderVersion != reviewed.sourceOrderVersion) {
      throw const EmarAdministerEvidenceChanged();
    }
    final revision = sessionNavigationGuard.revision;
    final before = await _nurse(revision);
    if (before.clinicContextId != reviewed.clinicId) {
      throw const EmarAdministerScopeUnavailable();
    }
    final current = await readSource.getAdministration(
      administrationId: reviewed.id,
      expectedPatientId: reviewed.patientId,
      expectedAdmissionId: reviewed.admissionId,
    );
    if (!_current(revision) ||
        !_sameClinical(reviewed, current) ||
        !current.isPending ||
        current.verificationEventId != null ||
        current.verificationResult != EmarBarcodeResult.barcodeVerified) {
      throw const EmarAdministerEvidenceChanged();
    }
    // The backend owns the latest-attempt and staff-actor checks. This GET
    // also prevents an obviously superseded verification from being submitted.
    final history = await readSource.getBarcodeHistory(
      administrationId: reviewed.id,
    );
    if (!_current(revision) ||
        history.items.isEmpty ||
        history.items.first.id != verified.id ||
        !history.items.first.isVerified) {
      throw const EmarAdministerEvidenceChanged();
    }
    final immediatelyBefore = await _nurse(revision);
    if (!_sameSession(before, immediatelyBefore) || !_current(revision)) {
      throw const EmarAdministerScopeUnavailable();
    }
    // The exact point of no retry: lock before invoking the transport.
    if (_started) throw const EmarAdministerOutcomeUnconfirmed();
    _started = true;
    try {
      final response = await apiClient.postJson(
        'api/v1/emar/${reviewed.id}/administer',
        body: <String, dynamic>{
          'barcode_verification_event_id': verified.id,
          if (notes != null && notes.trim().isNotEmpty) 'notes': notes.trim(),
        },
        bearerToken: immediatelyBefore.accessToken,
        idempotencyKey: operationKey,
      );
      final afterPost = await _nurse(revision);
      if (!_sameSession(immediatelyBefore, afterPost) || !_current(revision)) {
        throw const EmarAdministerOutcomeUnconfirmed();
      }
      final raw = response['administration'];
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Missing administration response.');
      }
      final server = EmarAdministration.fromJson(
        raw,
        expectedClinicId: reviewed.clinicId,
        expectedPatientId: reviewed.patientId,
        expectedAdmissionId: reviewed.admissionId,
        expectedId: reviewed.id,
      );
      if (server.status != EmarAdministrationStatus.administered ||
          server.verificationEventId != verified.id ||
          server.administeredAt == null) {
        throw const FormatException('Unconfirmed administration response.');
      }
      final readback = await readSource.getAdministration(
        administrationId: reviewed.id,
        expectedPatientId: reviewed.patientId,
        expectedAdmissionId: reviewed.admissionId,
      );
      final afterRead = await _nurse(revision);
      if (!_sameSession(immediatelyBefore, afterRead) ||
          !_current(revision) ||
          readback.status != EmarAdministrationStatus.administered ||
          readback.verificationEventId != verified.id ||
          readback.administeredAt == null ||
          readback.sourceOrderVersion != reviewed.sourceOrderVersion) {
        throw const EmarAdministerOutcomeUnconfirmed();
      }
      return readback;
    } on Object {
      // Timeout, HTTP error, malformed response, auth change and readback
      // disagreement all become terminal UNKNOWN. Never blindly retry.
      throw const EmarAdministerOutcomeUnconfirmed();
    }
  }
}
