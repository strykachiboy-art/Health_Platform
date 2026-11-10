// P23G.S01 — scoped, online-only medication reconciliation GET adapter.
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/models/medication_reconciliation.dart';

class MedicationReconciliationReadSessionException implements Exception {
  const MedicationReconciliationReadSessionException();

  @override
  String toString() =>
      'Medication reconciliation session unavailable or changed.';
}

/// Transient authenticated GET adapter; no cached clinical truth or writes.
class MedicationReconciliationReadRemoteDataSource {
  const MedicationReconciliationReadRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _readRoles = <String>{'doctor', 'pharmacist', 'nurse'};

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _authorized() async {
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
          !_readRoles.contains(session.role.trim().toLowerCase())) {
        throw const MedicationReconciliationReadSessionException();
      }
      return session;
    } on MedicationReconciliationReadSessionException {
      rethrow;
    } on Object {
      throw const MedicationReconciliationReadSessionException();
    }
  }

  bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  static void _id(int value, String name) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, name, 'Must be a positive identifier.');
    }
  }

  Future<T> _get<T>(
    String path,
    T Function(Map<String, dynamic> json, int clinicId) decode,
  ) async {
    final revision = sessionNavigationGuard.revision;
    if (!_current(revision)) {
      throw const MedicationReconciliationReadSessionException();
    }
    final before = await _authorized();
    if (!_current(revision)) {
      throw const MedicationReconciliationReadSessionException();
    }
    final raw = await apiClient.getJson(path, bearerToken: before.accessToken);
    if (!_current(revision)) {
      throw const MedicationReconciliationReadSessionException();
    }
    final after = await _authorized();
    if (!_current(revision) || !_same(before, after)) {
      throw const MedicationReconciliationReadSessionException();
    }
    return decode(raw, before.clinicContextId!);
  }

  Future<MedicationReconciliationPage> listForAdmission({
    required int admissionId,
    int? expectedPatientId,
    int page = 1,
    int perPage = 50,
    MedicationReconciliationEvent? event,
  }) {
    _id(admissionId, 'admissionId');
    if (expectedPatientId != null) _id(expectedPatientId, 'expectedPatientId');
    if (page < 1 || perPage < 1 || perPage > 100) {
      throw ArgumentError('Invalid reconciliation pagination.');
    }
    final path = Uri(
      path: 'api/v1/medication-reconciliations/admissions/$admissionId',
      queryParameters: <String, String>{
        'page': '$page',
        'per_page': '$perPage',
        if (event != null) 'event_type': event.wireValue,
      },
    ).toString();
    return _get(path, (raw, clinicId) {
      return MedicationReconciliationPage.fromJson(
        raw,
        expectedClinicId: clinicId,
        expectedAdmissionId: admissionId,
        expectedPatientId: expectedPatientId,
        requestedPage: page,
        requestedPerPage: perPage,
        requestedEvent: event,
      );
    });
  }

  Future<MedicationReconciliationRecord> getForEvent({
    required int admissionId,
    required MedicationReconciliationEvent event,
    int? wardTransferId,
    int? expectedPatientId,
  }) {
    _id(admissionId, 'admissionId');
    if (expectedPatientId != null) _id(expectedPatientId, 'expectedPatientId');
    if ((event == MedicationReconciliationEvent.transfer) !=
        (wardTransferId != null)) {
      throw ArgumentError('Transfer event requires exactly one transfer ID.');
    }
    if (wardTransferId != null) _id(wardTransferId, 'wardTransferId');
    final path = Uri(
      path:
          'api/v1/medication-reconciliations/admissions/$admissionId/events/${event.wireValue}',
      queryParameters: wardTransferId == null
          ? null
          : <String, String>{'ward_transfer_id': '$wardTransferId'},
    ).toString();
    return _get(path, (raw, clinicId) {
      final record = raw['reconciliation'];
      if (record is! Map<String, dynamic>) {
        throw const FormatException('Missing reconciliation event envelope.');
      }
      return MedicationReconciliationRecord.fromJson(
        record,
        expectedClinicId: clinicId,
        expectedAdmissionId: admissionId,
        expectedPatientId: expectedPatientId,
        expectedEvent: event,
        expectedWardTransferId: wardTransferId,
      );
    });
  }

  Future<MedicationReconciliationRecord> getById({
    required int reconciliationId,
    int? expectedAdmissionId,
    int? expectedPatientId,
  }) {
    _id(reconciliationId, 'reconciliationId');
    if (expectedAdmissionId != null) {
      _id(expectedAdmissionId, 'expectedAdmissionId');
    }
    if (expectedPatientId != null) _id(expectedPatientId, 'expectedPatientId');
    return _get('api/v1/medication-reconciliations/$reconciliationId', (
      raw,
      clinicId,
    ) {
      final record = raw['reconciliation'];
      if (record is! Map<String, dynamic>) {
        throw const FormatException('Missing reconciliation detail envelope.');
      }
      return MedicationReconciliationRecord.fromJson(
        record,
        expectedClinicId: clinicId,
        expectedAdmissionId: expectedAdmissionId,
        expectedPatientId: expectedPatientId,
        expectedId: reconciliationId,
      );
    });
  }
}
