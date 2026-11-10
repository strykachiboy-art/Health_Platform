// P23F.S05: one-shot, online-only terminal eMAR disposition.
// The backend, not the device, decides whether a slot can be finalized.
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

enum EmarTerminalAction { hold, refuse, miss }

extension EmarTerminalActionContract on EmarTerminalAction {
  String get endpoint => switch (this) {
    EmarTerminalAction.hold => 'hold',
    EmarTerminalAction.refuse => 'refuse',
    EmarTerminalAction.miss => 'miss',
  };

  EmarAdministrationStatus get resultingStatus => switch (this) {
    EmarTerminalAction.hold => EmarAdministrationStatus.held,
    EmarTerminalAction.refuse => EmarAdministrationStatus.refused,
    EmarTerminalAction.miss => EmarAdministrationStatus.missed,
  };

  String get label => switch (this) {
    EmarTerminalAction.hold => 'Hold medication',
    EmarTerminalAction.refuse => 'Record refusal',
    EmarTerminalAction.miss => 'Record missed/omitted dose',
  };
}

class EmarTerminalScopeUnavailable implements Exception {
  const EmarTerminalScopeUnavailable();
}

class EmarTerminalEvidenceChanged implements Exception {
  const EmarTerminalEvidenceChanged();
}

class EmarTerminalOutcomeUnconfirmed implements Exception {
  const EmarTerminalOutcomeUnconfirmed();
}

/// Bind one UI confirmation to one selected clinical slot and one operation.
/// Once POST begins, even an HTTP/transport failure permanently locks this
/// instance. There is no background or offline retry.
class EmarTerminalOutcomeRemoteDataSource {
  EmarTerminalOutcomeRemoteDataSource({
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
    if (!_current(revision)) throw const EmarTerminalScopeUnavailable();
    try {
      final state = await loadSession();
      final s = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          s == null ||
          s.userId <= 0 ||
          s.clinicContextId == null ||
          s.clinicContextId! <= 0 ||
          s.role.trim().toLowerCase() != 'nurse' ||
          s.accessToken.isEmpty ||
          s.accessToken != s.accessToken.trim() ||
          !_current(revision)) {
        throw const EmarTerminalScopeUnavailable();
      }
      return s;
    } on EmarTerminalScopeUnavailable {
      rethrow;
    } on Object {
      throw const EmarTerminalScopeUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
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
      a.scheduledAt == b.scheduledAt &&
      a.doseValue == b.doseValue &&
      a.doseUnit == b.doseUnit &&
      a.routeCode == b.routeCode;

  Future<EmarAdministration> recordOnce({
    required EmarAdministration reviewed,
    required EmarTerminalAction action,
    required String reason,
    required String operationKey,
  }) async {
    if (_started) throw const EmarTerminalOutcomeUnconfirmed();
    final normalized = reason.trim();
    if (normalized.isEmpty ||
        normalized.length > 500 ||
        normalized != reason ||
        operationKey.trim().isEmpty ||
        operationKey != operationKey.trim() ||
        operationKey.length > 255 ||
        !reviewed.isPending) {
      throw const EmarTerminalEvidenceChanged();
    }
    if (!identical(apiClient, readSource.apiClient) ||
        !identical(sessionNavigationGuard, readSource.sessionNavigationGuard)) {
      throw const EmarTerminalScopeUnavailable();
    }

    final revision = sessionNavigationGuard.revision;
    final before = await _nurse(revision);
    if (before.clinicContextId != reviewed.clinicId) {
      throw const EmarTerminalScopeUnavailable();
    }
    final current = await readSource.getAdministration(
      administrationId: reviewed.id,
      expectedPatientId: reviewed.patientId,
      expectedAdmissionId: reviewed.admissionId,
    );
    if (!_current(revision) ||
        !_sameClinical(reviewed, current) ||
        !current.isPending) {
      throw const EmarTerminalEvidenceChanged();
    }
    final justBefore = await _nurse(revision);
    if (!_same(before, justBefore) || !_current(revision)) {
      throw const EmarTerminalScopeUnavailable();
    }

    // No client-side due/late inference. The server validates event timing.
    if (_started) throw const EmarTerminalOutcomeUnconfirmed();
    _started = true;
    try {
      final response = await apiClient.postJson(
        'api/v1/emar/${reviewed.id}/${action.endpoint}',
        body: <String, dynamic>{'reason': normalized},
        bearerToken: justBefore.accessToken,
        idempotencyKey: operationKey,
      );
      final afterPost = await _nurse(revision);
      if (!_same(justBefore, afterPost) || !_current(revision)) {
        throw const EmarTerminalOutcomeUnconfirmed();
      }
      final raw = response['administration'];
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Missing terminal eMAR response.');
      }
      final server = EmarAdministration.fromJson(
        raw,
        expectedClinicId: reviewed.clinicId,
        expectedPatientId: reviewed.patientId,
        expectedAdmissionId: reviewed.admissionId,
        expectedId: reviewed.id,
      );
      if (server.status != action.resultingStatus ||
          server.recordStatus != EmarRecordStatus.finalised ||
          server.terminalReason != normalized ||
          server.terminalAt == null) {
        throw const FormatException('Incomplete terminal eMAR response.');
      }
      final verified = await readSource.getAdministration(
        administrationId: reviewed.id,
        expectedPatientId: reviewed.patientId,
        expectedAdmissionId: reviewed.admissionId,
      );
      final afterRead = await _nurse(revision);
      if (!_same(justBefore, afterRead) ||
          !_current(revision) ||
          verified.status != action.resultingStatus ||
          verified.recordStatus != EmarRecordStatus.finalised ||
          verified.terminalReason != normalized ||
          verified.terminalAt == null ||
          verified.sourceOrderVersion != reviewed.sourceOrderVersion) {
        throw const EmarTerminalOutcomeUnconfirmed();
      }
      return verified;
    } on Object {
      // After the POST begins all errors are terminal uncertainty.
      throw const EmarTerminalOutcomeUnconfirmed();
    }
  }
}
