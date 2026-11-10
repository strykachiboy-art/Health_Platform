// P23G.S03.C — online-only, one-shot clinical draft mutations.
// This adapter never finalizes, amends, persists a queue, or retries writes.
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/models/medication_reconciliation.dart';

class MedicationReconciliationMutationSessionException implements Exception {
  const MedicationReconciliationMutationSessionException();

  @override
  String toString() =>
      'Medication reconciliation write session unavailable or changed.';
}

class MedicationReconciliationMutationInFlightException implements Exception {
  const MedicationReconciliationMutationInFlightException();

  @override
  String toString() => 'A reconciliation write is already in progress.';
}

/// A write may have reached the server but could not be confirmed with readback.
/// Callers must reload the authoritative record before offering any next action.
class MedicationReconciliationWriteRecheckRequiredException
    implements Exception {
  const MedicationReconciliationWriteRecheckRequiredException();

  @override
  String toString() => 'Reconciliation outcome needs a fresh server read.';
}

extension on MedicationReconciliationDecision {
  String get backendValue => switch (this) {
    MedicationReconciliationDecision.continueMedication => 'continue',
    MedicationReconciliationDecision.hold => 'hold',
    MedicationReconciliationDecision.stop => 'stop',
    MedicationReconciliationDecision.replace => 'replace',
    MedicationReconciliationDecision.newMedication => 'new',
    MedicationReconciliationDecision.verify => 'verify',
    MedicationReconciliationDecision.unknown => 'unknown',
  };
}

/// Complete server draft document; never represents clinical finalization.
class MedicationReconciliationDraftDocument {
  const MedicationReconciliationDraftDocument({
    required this.noMedicationsReported,
    required this.notes,
    required this.items,
  });

  final bool noMedicationsReported;
  final String? notes;
  final List<MedicationReconciliationItem> items;

  static String? _optionalText(String? value, String name, int limit) {
    if (value == null) {
      return null;
    }
    if (value.isEmpty || value.trim() != value || value.length > limit) {
      throw ArgumentError.value(value, name, 'Invalid clinical text.');
    }
    return value;
  }

  Map<String, dynamic> toJson() {
    if (items.length > 500 || (noMedicationsReported && items.isNotEmpty)) {
      throw ArgumentError('Contradictory or oversized reconciliation draft.');
    }
    final seenSourceIds = <int>{};
    final medications = <Map<String, dynamic>>[];
    for (final item in items) {
      final raw = <String, dynamic>{
        'source_prescription_item_id': item.sourcePrescriptionItemId,
        'medication_name': item.medicationName,
        'decision': item.decision.backendValue,
        'replacement_medication_name': _optionalText(
          item.replacementMedicationName,
          'replacementMedicationName',
          255,
        ),
        'dose_text': _optionalText(item.doseText, 'doseText', 255),
        'route_text': _optionalText(item.routeText, 'routeText', 128),
        'frequency_text': _optionalText(
          item.frequencyText,
          'frequencyText',
          255,
        ),
        'instructions': _optionalText(item.instructions, 'instructions', 1000),
        'rationale': _optionalText(item.rationale, 'rationale', 1000),
      };
      // Reuse S01's backend-shape and replacement-decision validation.
      MedicationReconciliationItem.fromJson(raw);
      final sourceId = item.sourcePrescriptionItemId;
      if (sourceId != null && !seenSourceIds.add(sourceId)) {
        throw ArgumentError('Duplicate source prescription item.');
      }
      medications.add(raw);
    }
    return <String, dynamic>{
      'no_medications_reported': noMedicationsReported,
      'notes': _optionalText(notes, 'notes', 10000),
      'items': medications,
    };
  }
}

/// A single adapter instance serializes its own mutations. The backend remains
/// authoritative for role, clinic, staff-author and timestamp CAS enforcement.
class MedicationReconciliationMutationRemoteDataSource {
  MedicationReconciliationMutationRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  bool _writeInFlight = false;

  static const _writeRoles = <String>{'doctor', 'pharmacist'};

  static void _id(int value, String name) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, name, 'Must be a positive identifier.');
    }
  }

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
          !_writeRoles.contains(session.role.trim().toLowerCase())) {
        throw const MedicationReconciliationMutationSessionException();
      }
      return session;
    } on MedicationReconciliationMutationSessionException {
      rethrow;
    } on Object {
      throw const MedicationReconciliationMutationSessionException();
    }
  }

  bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<void> _verify(AuthTokenResponse before, int revision) async {
    if (!_current(revision)) {
      throw const MedicationReconciliationMutationSessionException();
    }
    final after = await _authorized();
    if (!_current(revision) || !_same(before, after)) {
      throw const MedicationReconciliationMutationSessionException();
    }
  }

  Future<MedicationReconciliationRecord> createDraft({
    required int admissionId,
    required int expectedPatientId,
    required MedicationReconciliationEvent event,
    required MedicationReconciliationDraftDocument document,
    int? wardTransferId,
  }) {
    _id(admissionId, 'admissionId');
    _id(expectedPatientId, 'expectedPatientId');
    if ((event == MedicationReconciliationEvent.transfer) !=
        (wardTransferId != null)) {
      throw ArgumentError('Transfer reconciliation requires one transfer ID.');
    }
    if (wardTransferId != null) {
      _id(wardTransferId, 'wardTransferId');
    }
    final body = <String, dynamic>{
      'admission_id': admissionId,
      'event_type': event.wireValue,
      'ward_transfer_id': ?wardTransferId,
      ...document.toJson(),
    };
    return _write(
      method: 'POST',
      path: 'api/v1/medication-reconciliations',
      body: body,
      admissionId: admissionId,
      patientId: expectedPatientId,
      event: event,
      transferId: wardTransferId,
    );
  }

  Future<MedicationReconciliationRecord> updateDraft({
    required MedicationReconciliationRecord current,
    required MedicationReconciliationDraftDocument document,
  }) {
    if (current.status != MedicationReconciliationRecordStatus.draft ||
        current.version != 1 ||
        current.updatedAt == null) {
      throw StateError('A fresh backend draft with updated_at is required.');
    }
    _id(current.id, 'reconciliationId');
    _id(current.clinicId, 'clinicId');
    _id(current.admissionId, 'admissionId');
    _id(current.patientId, 'patientId');
    if (current.event == MedicationReconciliationEvent.transfer) {
      if (current.wardTransferId == null) {
        throw StateError('Transfer reconciliation is missing transfer ID.');
      }
      _id(current.wardTransferId!, 'wardTransferId');
    }
    return _write(
      method: 'PATCH',
      path: 'api/v1/medication-reconciliations/${current.id}',
      body: <String, dynamic>{
        'expected_updated_at': current.updatedAt!.toUtc().toIso8601String(),
        ...document.toJson(),
      },
      admissionId: current.admissionId,
      patientId: current.patientId,
      event: current.event,
      transferId: current.wardTransferId,
      recordId: current.id,
      expectedClinicId: current.clinicId,
      priorTimestamp: current.updatedAt,
    );
  }

  static bool _sameItems(
    List<MedicationReconciliationItem> a,
    List<MedicationReconciliationItem> b,
  ) {
    if (a.length != b.length) {
      return false;
    }
    for (var i = 0; i < a.length; i++) {
      final x = a[i];
      final y = b[i];
      if (x.sourcePrescriptionItemId != y.sourcePrescriptionItemId ||
          x.medicationName != y.medicationName ||
          x.decision != y.decision ||
          x.replacementMedicationName != y.replacementMedicationName ||
          x.doseText != y.doseText ||
          x.routeText != y.routeText ||
          x.frequencyText != y.frequencyText ||
          x.instructions != y.instructions ||
          x.rationale != y.rationale) {
        return false;
      }
    }
    return true;
  }

  Future<MedicationReconciliationRecord> _write({
    required String method,
    required String path,
    required Map<String, dynamic> body,
    required int admissionId,
    required int patientId,
    required MedicationReconciliationEvent event,
    int? transferId,
    int? recordId,
    int? expectedClinicId,
    DateTime? priorTimestamp,
  }) async {
    if (_writeInFlight) {
      throw const MedicationReconciliationMutationInFlightException();
    }
    _writeInFlight = true;
    try {
      final revision = sessionNavigationGuard.revision;
      if (!_current(revision)) {
        throw const MedicationReconciliationMutationSessionException();
      }
      final before = await _authorized();
      if (!_current(revision) ||
          (expectedClinicId != null &&
              expectedClinicId != before.clinicContextId)) {
        throw const MedicationReconciliationMutationSessionException();
      }
      final raw = method == 'POST'
          ? await apiClient.postJson(
              path,
              body: body,
              bearerToken: before.accessToken,
            )
          : await apiClient.patchJson(
              path,
              body: body,
              bearerToken: before.accessToken,
            );
      await _verify(before, revision);
      final output = raw['reconciliation'];
      if (output is! Map<String, dynamic>) {
        throw const MedicationReconciliationWriteRecheckRequiredException();
      }
      final committed = MedicationReconciliationRecord.fromJson(
        output,
        expectedClinicId: before.clinicContextId!,
        expectedAdmissionId: admissionId,
        expectedPatientId: patientId,
        expectedId: recordId,
        expectedEvent: event,
        expectedWardTransferId: transferId,
      );
      if (committed.status != MedicationReconciliationRecordStatus.draft ||
          committed.updatedAt == null ||
          (priorTimestamp != null &&
              !committed.updatedAt!.isAfter(priorTimestamp))) {
        throw const MedicationReconciliationWriteRecheckRequiredException();
      }
      final response = await apiClient.getJson(
        'api/v1/medication-reconciliations/${committed.id}',
        bearerToken: before.accessToken,
      );
      await _verify(before, revision);
      final detail = response['reconciliation'];
      if (detail is! Map<String, dynamic>) {
        throw const MedicationReconciliationWriteRecheckRequiredException();
      }
      final authoritative = MedicationReconciliationRecord.fromJson(
        detail,
        expectedClinicId: before.clinicContextId!,
        expectedAdmissionId: admissionId,
        expectedPatientId: patientId,
        expectedId: committed.id,
        expectedEvent: event,
        expectedWardTransferId: transferId,
      );
      if (authoritative.status != MedicationReconciliationRecordStatus.draft ||
          authoritative.version != committed.version ||
          authoritative.updatedAt != committed.updatedAt ||
          authoritative.createdByStaffId != committed.createdByStaffId ||
          authoritative.noMedicationsReported !=
              committed.noMedicationsReported ||
          authoritative.notes != committed.notes ||
          !_sameItems(authoritative.items, committed.items)) {
        throw const MedicationReconciliationWriteRecheckRequiredException();
      }
      // A matched readback is the only successful completion signal.
      return authoritative;
    } finally {
      _writeInFlight = false;
    }
  }
}
