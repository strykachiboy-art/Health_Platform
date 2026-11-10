// P23F.S03.A — one-shot, online-only server barcode verification.
// It does NOT administer a medication and does NOT capture camera barcodes.
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/emar_barcode_verification.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

class EmarVerificationSessionUnavailable implements Exception {
  const EmarVerificationSessionUnavailable();
}

class EmarVerificationPreflightChanged implements Exception {
  const EmarVerificationPreflightChanged();
}

class EmarVerificationOutcomeUnconfirmed implements Exception {
  const EmarVerificationOutcomeUnconfirmed();
}

/// Instantiate for ONE human-confirmed scan verification. A started POST locks
/// this instance even on timeout, denial, unexpected response or readback loss.
/// New scanning requires a fresh server review and a new operation identity.
class EmarBarcodeVerificationRemoteDataSource {
  EmarBarcodeVerificationRemoteDataSource({
    required this.apiClient,
    required this.readSource,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final EmarReadRemoteDataSource readSource;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  bool _attempted = false;
  bool get attemptStarted => _attempted;

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _nurse(int revision) async {
    if (!_current(revision)) throw const EmarVerificationSessionUnavailable();
    try {
      final state = await loadSession();
      final s = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          s == null ||
          s.userId <= 0 ||
          s.clinicContextId == null ||
          s.clinicContextId! <= 0 ||
          s.accessToken.trim().isEmpty ||
          s.accessToken != s.accessToken.trim() ||
          s.role.trim().toLowerCase() != 'nurse' ||
          !_current(revision)) {
        throw const EmarVerificationSessionUnavailable();
      }
      return s;
    } on EmarVerificationSessionUnavailable {
      rethrow;
    } on Object {
      throw const EmarVerificationSessionUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  static String _scanText(String value, String name) {
    if (value.isEmpty || value.trim() != value || value.length > 255) {
      throw ArgumentError.value(name, 'field', 'Invalid scan field.');
    }
    return value;
  }

  static bool _sameClinicalSnapshot(
    EmarAdministration reviewed,
    EmarAdministration latest,
  ) =>
      reviewed.id == latest.id &&
      reviewed.clinicId == latest.clinicId &&
      reviewed.patientId == latest.patientId &&
      reviewed.admissionId == latest.admissionId &&
      reviewed.prescriptionItemId == latest.prescriptionItemId &&
      reviewed.status == latest.status &&
      reviewed.recordVersion == latest.recordVersion &&
      reviewed.sourceOrderVersion == latest.sourceOrderVersion &&
      reviewed.doseValue == latest.doseValue &&
      reviewed.doseUnit == latest.doseUnit &&
      reviewed.routeCode == latest.routeCode &&
      reviewed.scheduledAt == latest.scheduledAt;

  Future<EmarBarcodeVerification> verifyOnce({
    required EmarAdministration reviewed,
    required String patientBarcode,
    required String medicationBarcode,
    required String scanDevice,
    required DateTime scanTimestamp,
    required String operationKey,
  }) async {
    if (_attempted) throw const EmarVerificationOutcomeUnconfirmed();
    _scanText(patientBarcode, 'patientBarcode');
    _scanText(medicationBarcode, 'medicationBarcode');
    _scanText(scanDevice, 'scanDevice');
    _scanText(operationKey, 'operationKey');
    if (!reviewed.isPending) {
      throw const EmarVerificationPreflightChanged();
    }
    if (!identical(readSource.apiClient, apiClient) ||
        !identical(readSource.sessionNavigationGuard, sessionNavigationGuard)) {
      throw const EmarVerificationSessionUnavailable();
    }
    final revision = sessionNavigationGuard.revision;
    final before = await _nurse(revision);
    if (before.clinicContextId != reviewed.clinicId) {
      throw const EmarVerificationSessionUnavailable();
    }
    final fresh = await readSource.getAdministration(
      administrationId: reviewed.id,
      expectedPatientId: reviewed.patientId,
      expectedAdmissionId: reviewed.admissionId,
    );
    if (!_current(revision) ||
        !_sameClinicalSnapshot(reviewed, fresh) ||
        !fresh.isPending) {
      throw const EmarVerificationPreflightChanged();
    }
    final still = await _nurse(revision);
    if (!_same(before, still)) throw const EmarVerificationSessionUnavailable();
    // Lock synchronously before first network write; no automatic retry.
    if (_attempted) throw const EmarVerificationOutcomeUnconfirmed();
    _attempted = true;
    try {
      final body = <String, dynamic>{
        'patient_barcode': patientBarcode,
        'medication_barcode': medicationBarcode,
        'scan_timestamp': scanTimestamp.toUtc().toIso8601String(),
        'scan_device': scanDevice,
      };
      final response = await apiClient.postJson(
        'api/v1/emar/barcode/verify',
        body: body,
        bearerToken: still.accessToken,
        idempotencyKey: operationKey,
      );
      final after = await _nurse(revision);
      if (!_same(still, after) || !_current(revision)) {
        throw const EmarVerificationOutcomeUnconfirmed();
      }
      final event = EmarBarcodeVerification.fromEnvelope(
        response,
        reviewed: fresh,
        operationKey: operationKey,
        patientBarcode: patientBarcode,
        medicationBarcode: medicationBarcode,
        scanDevice: scanDevice,
        scanTimestamp: scanTimestamp,
      );
      if (!event.verified) return event; // Durable failed scan: never advance.
      final readback = await readSource.getAdministration(
        administrationId: fresh.id,
        expectedPatientId: fresh.patientId,
        expectedAdmissionId: fresh.admissionId,
      );
      final history = await readSource.getBarcodeHistory(
        administrationId: fresh.id,
      );
      final finalSession = await _nurse(revision);
      if (!_same(still, finalSession) ||
          !_current(revision) ||
          readback.verificationEventId != null ||
          readback.verificationResult != EmarBarcodeResult.barcodeVerified ||
          !readback.isPending ||
          readback.sourceOrderVersion != fresh.sourceOrderVersion ||
          history.items.isEmpty || history.items.first.id != event.id ||
          !history.items.first.isVerified) {
        throw const EmarVerificationOutcomeUnconfirmed();
      }
      return event;
    } on Object {
      // Includes transport timeout, 4xx/5xx, invalid response, and readback.
      // All post-start uncertainty is terminal for this instance.
      throw const EmarVerificationOutcomeUnconfirmed();
    }
  }
}
