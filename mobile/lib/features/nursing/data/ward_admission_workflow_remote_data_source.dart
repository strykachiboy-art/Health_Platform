import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';

import 'models/ward_admission_workflow.dart';
import 'models/ward_overview.dart';

class WardAdmissionSessionException implements Exception {
  const WardAdmissionSessionException();
  @override
  String toString() => 'Ward admission session unavailable.';
}

class WardAdmissionPreflightException implements Exception {
  const WardAdmissionPreflightException();
  @override
  String toString() => 'Admission or destination changed. Review again.';
}

class WardAdmissionOutcomeUnconfirmed implements Exception {
  const WardAdmissionOutcomeUnconfirmed();
  @override
  String toString() =>
      'Outcome not confirmed. Reconcile with server before any new action.';
}

/// Online admission/reservation/transfer, never queued or automatically replayed.
/// Backend does NOT require/advertise an idempotency key for these POST routes.
/// A started POST irrevocably locks this adapter for its screen lifetime.
class WardAdmissionWorkflowRemoteDataSource {
  WardAdmissionWorkflowRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  }) : admissions = PatientAdmissionRemoteDataSource(
         apiClient: apiClient,
         loadSession: loadSession,
         sessionNavigationGuard: sessionNavigationGuard,
       );

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  final PatientAdmissionRemoteDataSource admissions;
  bool _writeAttempted = false;
  bool get writeAttempted => _writeAttempted;

  static const _readRoles = <String>{
    'admin',
    'doctor',
    'nurse',
    'receptionist',
  };
  static const _writeRoles = <String>{'admin', 'doctor', 'nurse'};

  static void _positive(int value, String name) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, name, 'Must be a positive identifier.');
    }
  }

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _session(int revision) async {
    if (!_current(revision)) throw const WardAdmissionSessionException();
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
          !_readRoles.contains(s.role.trim().toLowerCase()) ||
          !_current(revision)) {
        throw const WardAdmissionSessionException();
      }
      return s;
    } on WardAdmissionSessionException {
      rethrow;
    } on Object {
      throw const WardAdmissionSessionException();
    }
  }

  bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<Map<String, dynamic>> _get(String path) async {
    final rev = sessionNavigationGuard.revision;
    final before = await _session(rev);
    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    final after = await _session(rev);
    if (!_same(before, after) || !_current(rev)) {
      throw const WardAdmissionSessionException();
    }
    return response;
  }

  Future<WardReservationRecord?> _activeReservation(int patientId) async {
    try {
      final json = await _get('api/v1/wards/patients/$patientId/reservation');
      final reservation = WardReservationRecord.fromJson(
        json,
        expectedPatientId: patientId,
      );
      if (!reservation.isPendingAndUnexpired) {
        throw const WardAdmissionPreflightException();
      }
      return reservation;
    } on ApiException catch (e) {
      if (e.kind == ApiFailureKind.httpStatus &&
          e.statusCode == 404 &&
          e.serverMessage == 'No active reservation found') {
        return null;
      }
      rethrow;
    }
  }

  /// GET-only review. A snapshot never grants permission to bypass server RBAC.
  Future<WardAdmissionSnapshot> inspect({
    required int patientId,
    required int bedId,
  }) async {
    _positive(patientId, 'patientId');
    _positive(bedId, 'bedId');
    final rev = sessionNavigationGuard.revision;
    final before = await _session(rev);
    final PatientAdmission? current = await admissions.fetchCurrentAdmission(
      patientId: patientId,
    );
    final reservation = await _activeReservation(patientId);
    final bedJson = await _get('api/v1/wards/beds/$bedId');
    final wardId = bedJson['ward_id'];
    if (wardId is! int || wardId <= 0) {
      throw const FormatException('Destination bed ward is invalid.');
    }
    final bed = WardBedRecord.fromJson(bedJson, expectedWardId: wardId);
    if (bed.id != bedId) {
      throw const FormatException('Destination bed mismatch.');
    }
    final wardJson = await _get('api/v1/wards/$wardId');
    final ward = WardRecord.fromJson(
      wardJson,
      expectedClinicId: before.clinicContextId!,
    );
    if (ward.id != wardId) {
      throw const FormatException('Destination ward mismatch.');
    }
    final after = await _session(rev);
    if (!_same(before, after) || !_current(rev)) {
      throw const WardAdmissionSessionException();
    }
    return WardAdmissionSnapshot(
      patientId: patientId,
      ward: ward,
      bed: bed,
      activeAdmission: current,
      activeReservation: reservation,
      mayWrite: _writeRoles.contains(before.role.trim().toLowerCase()),
    );
  }

  static String? _cleanReason(String? reason) {
    if (reason == null || reason.isEmpty) return null;
    if (reason.trim() != reason || reason.length > 255) {
      throw ArgumentError.value(
        reason,
        'reason',
        'Must be trimmed and at most 255 characters.',
      );
    }
    return reason;
  }

  static Map<String, dynamic> _child(Map<String, dynamic> json, String key) {
    final child = json[key];
    if (child is! Map<String, dynamic>) {
      throw FormatException('Invalid mutation readback: $key');
    }
    return child;
  }

  /// User-confirmed mutation. No retry path. Verify fresh preconditions before POST.
  Future<WardAdmissionReceipt> perform({
    required WardAdmissionSnapshot reviewed,
    required WardAdmissionAction action,
    String? reason,
  }) async {
    if (_writeAttempted) throw const WardAdmissionOutcomeUnconfirmed();
    final cleanReason = _cleanReason(reason);
    if (!reviewed.allows(action)) throw const WardAdmissionPreflightException();
    final revision = sessionNavigationGuard.revision;
    final before = await _session(revision);
    if (!_writeRoles.contains(before.role.trim().toLowerCase()) ||
        before.clinicContextId != reviewed.ward.clinicId) {
      throw const WardAdmissionSessionException();
    }
    final fresh = await inspect(
      patientId: reviewed.patientId,
      bedId: reviewed.bed.id,
    );
    if (!reviewed.samePreflight(fresh) || !fresh.allows(action)) {
      throw const WardAdmissionPreflightException();
    }
    final still = await _session(revision);
    if (!_same(before, still) || !_current(revision)) {
      throw const WardAdmissionSessionException();
    }
    final body = <String, dynamic>{'reason': ?cleanReason};
    final path = switch (action) {
      WardAdmissionAction.reserve => 'api/v1/wards/reservations',
      WardAdmissionAction.admitDirect => 'api/v1/wards/admissions',
      WardAdmissionAction.admitFromReservation =>
        'api/v1/wards/reservations/${fresh.activeReservation!.id}/admit',
      WardAdmissionAction.transfer =>
        'api/v1/wards/admissions/${fresh.activeAdmission!.id}/transfer',
    };
    if (action == WardAdmissionAction.reserve ||
        action == WardAdmissionAction.admitDirect) {
      body['patient_id'] = reviewed.patientId;
      body['bed_id'] = reviewed.bed.id;
    } else if (action == WardAdmissionAction.transfer) {
      body['to_bed_id'] = reviewed.bed.id;
    }
    // Close concurrent preflight races as well as accidental UI double taps.
    if (_writeAttempted) throw const WardAdmissionOutcomeUnconfirmed();
    // Terminal lock happens immediately before the first network write.
    _writeAttempted = true;
    try {
      final response = await apiClient.postJson(
        path,
        body: body,
        bearerToken: still.accessToken,
      );
      final postSession = await _session(revision);
      if (!_same(still, postSession)) {
        throw const WardAdmissionSessionException();
      }
      final receipt = switch (action) {
        WardAdmissionAction.reserve => _reservationReceipt(
          response,
          reviewed.patientId,
          reviewed.bed.id,
        ),
        WardAdmissionAction.admitDirect ||
        WardAdmissionAction.admitFromReservation => _admissionReceipt(
          response,
          reviewed,
          action,
        ),
        WardAdmissionAction.transfer => _transferReceipt(response, reviewed),
      };
      final verified = await inspect(
        patientId: reviewed.patientId,
        bedId: reviewed.bed.id,
      );
      if (action == WardAdmissionAction.reserve) {
        if (verified.activeReservation?.id != receipt.referenceId ||
            verified.activeReservation?.bedId != reviewed.bed.id ||
            verified.activeAdmission != null) {
          throw const FormatException('Reservation not confirmed by readback.');
        }
      } else if (action == WardAdmissionAction.admitDirect ||
          action == WardAdmissionAction.admitFromReservation) {
        if (verified.activeAdmission?.id != receipt.referenceId ||
            verified.activeAdmission?.bedId != reviewed.bed.id) {
          throw const FormatException('Admission not confirmed by readback.');
        }
      } else {
        if (verified.activeAdmission?.id != reviewed.activeAdmission!.id ||
            verified.activeAdmission?.bedId != reviewed.bed.id) {
          throw const FormatException('Transfer not confirmed by readback.');
        }
      }
      return receipt;
    } on Object {
      // 409, 422, timeout, unexpected success shape, and readback failure all
      // require an explicit separate reconciliation; never auto-replay the POST.
      throw const WardAdmissionOutcomeUnconfirmed();
    }
  }

  static WardAdmissionReceipt _reservationReceipt(
    Map<String, dynamic> response,
    int patientId,
    int bedId,
  ) {
    final record = WardReservationRecord.fromJson(
      _child(response, 'reservation'),
      expectedPatientId: patientId,
    );
    if (record.bedId != bedId || !record.isPendingAndUnexpired) {
      throw const FormatException('Reservation response mismatch.');
    }
    return WardAdmissionReceipt(
      action: WardAdmissionAction.reserve,
      referenceId: record.id,
      patientId: patientId,
      destinationBedId: bedId,
    );
  }

  static WardAdmissionReceipt _admissionReceipt(
    Map<String, dynamic> response,
    WardAdmissionSnapshot reviewed,
    WardAdmissionAction action,
  ) {
    final admission = PatientAdmission.fromJson(
      _child(response, 'admission'),
      expectedPatientId: reviewed.patientId,
    );
    if (!admission.isActive ||
        admission.bedId != reviewed.bed.id ||
        (action == WardAdmissionAction.admitFromReservation &&
            admission.reservationId != reviewed.activeReservation!.id)) {
      throw const FormatException('Admission response mismatch.');
    }
    return WardAdmissionReceipt(
      action: action,
      referenceId: admission.id,
      patientId: reviewed.patientId,
      destinationBedId: reviewed.bed.id,
    );
  }

  static WardAdmissionReceipt _transferReceipt(
    Map<String, dynamic> response,
    WardAdmissionSnapshot reviewed,
  ) {
    final transfer = WardTransferRecord.fromJson(
      _child(response, 'transfer'),
      expectedAdmissionId: reviewed.activeAdmission!.id,
      expectedFromBedId: reviewed.activeAdmission!.bedId,
      expectedToBedId: reviewed.bed.id,
    );
    return WardAdmissionReceipt(
      action: WardAdmissionAction.transfer,
      referenceId: transfer.id,
      patientId: reviewed.patientId,
      destinationBedId: reviewed.bed.id,
    );
  }
}
