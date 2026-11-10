// P23H.S02 — online-only, one-shot prescription mutations.
// The authenticated backend owns authorization, CDS, state and dispensing.
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_prescription.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_prescription_detail.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';

class PrescriptionMutationScopeUnavailable implements Exception {
  const PrescriptionMutationScopeUnavailable();
}

class PrescriptionMutationEvidenceChanged implements Exception {
  const PrescriptionMutationEvidenceChanged();
}

class PrescriptionMutationOutcomeUnconfirmed implements Exception {
  const PrescriptionMutationOutcomeUnconfirmed();
}

class PrescriptionMutationAlreadyAttempted implements Exception {
  const PrescriptionMutationAlreadyAttempted();
}

class PrescriptionDraftMedication {
  const PrescriptionDraftMedication({
    required this.drugId,
    required this.dosage,
    required this.frequency,
    required this.duration,
    required this.quantity,
    required this.routeCode,
    required this.routeDisplay,
    required this.orderPriority,
    required this.orderType,
    this.instructions,
  });

  final int drugId;
  final String dosage;
  final String frequency;
  final String duration;
  final int quantity;
  final String routeCode;
  final String routeDisplay;
  final String orderPriority;
  final String orderType;
  final String? instructions;

  static bool _validText(String value, int limit) =>
      value.isNotEmpty && value == value.trim() && value.length <= limit;

  Map<String, dynamic> toRequestBody() {
    if (drugId <= 0 ||
        drugId > 2147483647 ||
        quantity <= 0 ||
        !_validText(dosage, 255) ||
        !_validText(frequency, 255) ||
        !_validText(duration, 255) ||
        !_validText(routeCode, 64) ||
        !_validText(routeDisplay, 128) ||
        !RegExp(r'^[A-Za-z][A-Za-z0-9_.-]*$').hasMatch(routeCode) ||
        !const <String>{'routine', 'urgent', 'stat'}.contains(orderPriority) ||
        !const <String>{
          'inpatient',
          'outpatient',
          'discharge',
        }.contains(orderType) ||
        (instructions != null && !_validText(instructions!, 1000))) {
      throw const FormatException('Invalid medication order fields.');
    }
    return <String, dynamic>{
      'drug_id': drugId,
      'dosage': dosage,
      'frequency': frequency,
      'duration': duration,
      'quantity': quantity,
      'route_code': routeCode,
      'route_display': routeDisplay,
      'order_priority': orderPriority,
      'order_type': orderType,
      if (instructions != null) 'instructions': instructions,
    };
  }
}

class PrescriptionMutationConfirmation {
  const PrescriptionMutationConfirmation({
    required this.prescription,
    required this.interactionWarnings,
  });

  final PatientPrescriptionDetail prescription;
  final List<Map<String, dynamic>> interactionWarnings;
}

/// Own one clinical write attempt across this instance's entire lifetime.
/// Never create an automatic replay or infer success from an HTTP 2xx alone.
class PatientPrescriptionMutationRemoteDataSource {
  PatientPrescriptionMutationRemoteDataSource({required this.reader});

  final PatientPrescriptionRemoteDataSource reader;
  bool _writeAttempted = false;
  bool get writeAttempted => _writeAttempted;

  bool _current(int revision) =>
      reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      reader.sessionNavigationGuard.revision == revision;

  static bool _same(AuthTokenResponse x, AuthTokenResponse y) =>
      x.userId == y.userId &&
      x.clinicContextId == y.clinicContextId &&
      x.role.trim().toLowerCase() == y.role.trim().toLowerCase() &&
      x.accessToken == y.accessToken &&
      x.refreshToken == y.refreshToken;

  Future<AuthTokenResponse> _session(
    int revision, {
    required bool doctorOnly,
  }) async {
    if (!_current(revision)) throw const PrescriptionMutationScopeUnavailable();
    try {
      final state = await reader.loadSession();
      final s = state.session;
      final roles = doctorOnly
          ? const <String>{'doctor'}
          : const <String>{'doctor', 'pharmacist'};
      if (state.disposition != AuthSessionDisposition.ready ||
          s == null ||
          s.userId <= 0 ||
          s.clinicContextId == null ||
          s.clinicContextId! <= 0 ||
          s.accessToken.isEmpty ||
          s.accessToken.trim() != s.accessToken ||
          !roles.contains(s.role.trim().toLowerCase()) ||
          !_current(revision)) {
        throw const PrescriptionMutationScopeUnavailable();
      }
      return s;
    } on PrescriptionMutationScopeUnavailable {
      rethrow;
    } on Object {
      throw const PrescriptionMutationScopeUnavailable();
    }
  }

  Future<void> _verify(
    AuthTokenResponse before,
    int revision, {
    required bool doctorOnly,
  }) async {
    if (!_same(before, await _session(revision, doctorOnly: doctorOnly)) ||
        !_current(revision)) {
      throw const PrescriptionMutationScopeUnavailable();
    }
  }

  static void _id(int id, String field) {
    if (id <= 0 || id > 2147483647) {
      throw ArgumentError.value(id, field, 'Invalid identity.');
    }
  }

  static Map<String, dynamic> _responseRow(Map<String, dynamic> response) {
    if (response['success'] != true ||
        response['data'] is! Map<String, dynamic>) {
      throw const FormatException('Unconfirmed prescription response.');
    }
    return response['data'] as Map<String, dynamic>;
  }

  Future<PatientPrescriptionDetail> inspect({
    required int prescriptionId,
    required int patientId,
  }) => reader.fetchPrescriptionDetail(
    prescriptionId: prescriptionId,
    expectedPatientId: patientId,
  );

  Future<PrescriptionMutationConfirmation> createOnce({
    required int patientId,
    required List<PrescriptionDraftMedication> medications,
    required String idempotencyKey,
    int? consultationId,
    String? notes,
    bool clinicalSafetyAcknowledged = false,
    String? clinicalSafetyJustification,
  }) async {
    if (_writeAttempted) throw const PrescriptionMutationAlreadyAttempted();
    _id(patientId, 'patientId');
    if (consultationId != null) _id(consultationId, 'consultationId');
    if (idempotencyKey.isEmpty ||
        idempotencyKey.trim() != idempotencyKey ||
        idempotencyKey.length > 255 ||
        idempotencyKey.contains('\n')) {
      throw const FormatException('Invalid idempotency key.');
    }
    if (medications.isEmpty ||
        medications.length > 100 ||
        (notes != null && (notes != notes.trim() || notes.length > 2000)) ||
        (clinicalSafetyJustification != null &&
            (clinicalSafetyJustification !=
                    clinicalSafetyJustification.trim() ||
                clinicalSafetyJustification.length > 2000)) ||
        (clinicalSafetyAcknowledged &&
            (clinicalSafetyJustification == null ||
                clinicalSafetyJustification.isEmpty)) ||
        (!clinicalSafetyAcknowledged && clinicalSafetyJustification != null)) {
      throw const FormatException('Invalid prescription creation evidence.');
    }
    final items = <Map<String, dynamic>>[];
    final drugIds = <int>{};
    for (final medication in medications) {
      final item = medication.toRequestBody();
      if (!drugIds.add(medication.drugId)) {
        throw const FormatException('Duplicate medication ID.');
      }
      items.add(item);
    }

    final revision = reader.sessionNavigationGuard.revision;
    final before = await _session(revision, doctorOnly: true);
    final body = <String, dynamic>{
      'patient_id': patientId,
      'consultation_id': ?consultationId,
      'items': items,
      'notes': ?notes,
      'clinical_safety_acknowledged': clinicalSafetyAcknowledged,
      'clinical_safety_justification': ?clinicalSafetyJustification,
    };

    _writeAttempted = true;
    // From here on, any failure (including 409, 403 or timeout) is
    // non-replayable on this adapter. Server readback is required.
    try {
      final response = await reader.apiClient.postJson(
        'api/v1/prescriptions',
        body: body,
        bearerToken: before.accessToken,
        idempotencyKey: idempotencyKey,
      );
      await _verify(before, revision, doctorOnly: true);
      final accepted = PatientPrescription.fromJson(
        _responseRow(response),
        expectedPatientId: patientId,
        expectedClinicId: before.clinicContextId!,
      );
      if (accepted.status != PatientPrescriptionStatus.active ||
          accepted.items.length != medications.length ||
          !accepted.items.map((i) => i.drugId).toSet().containsAll(drugIds)) {
        throw const FormatException('Unexpected created prescription state.');
      }
      final rawWarnings = response['interaction_warnings'];
      if (rawWarnings is! List ||
          rawWarnings.any((e) => e is! Map<String, dynamic>)) {
        throw const FormatException('Missing prescription warnings evidence.');
      }
      final warnings = <Map<String, dynamic>>[
        for (final warning in rawWarnings)
          Map<String, dynamic>.unmodifiable(warning as Map<String, dynamic>),
      ];
      final detail = await inspect(
        prescriptionId: accepted.id,
        patientId: patientId,
      );
      await _verify(before, revision, doctorOnly: true);
      if (detail.prescription.status != PatientPrescriptionStatus.active ||
          detail.prescription.items.length != medications.length ||
          detail.structuredItems.length != medications.length) {
        throw const FormatException('Server readback disagrees with creation.');
      }
      for (final medication in medications) {
        final matches = detail.prescription.items
            .where((item) => item.drugId == medication.drugId)
            .toList(growable: false);
        if (matches.length != 1 ||
            matches.single.dosage != medication.dosage ||
            matches.single.frequency != medication.frequency ||
            matches.single.duration != medication.duration ||
            matches.single.quantity != medication.quantity ||
            detail.structuredItems
                    .where(
                      (item) =>
                          item.id == matches.single.id &&
                          item.routeCode == medication.routeCode &&
                          item.routeDisplay == medication.routeDisplay &&
                          item.priority == medication.orderPriority &&
                          item.orderType == medication.orderType,
                    )
                    .length !=
                1) {
          throw const FormatException('Server medication evidence changed.');
        }
      }
      return PrescriptionMutationConfirmation(
        prescription: detail,
        interactionWarnings: List<Map<String, dynamic>>.unmodifiable(warnings),
      );
    } on Object {
      throw const PrescriptionMutationOutcomeUnconfirmed();
    }
  }

  static bool _sameEvidence(PatientPrescription a, PatientPrescription b) =>
      a.id == b.id &&
      a.clinicId == b.clinicId &&
      a.patientId == b.patientId &&
      a.status == b.status &&
      a.notes == b.notes &&
      a.items.length == b.items.length &&
      a.items.every(
        (i) => b.items.any(
          (j) =>
              i.id == j.id && i.drugId == j.drugId && i.quantity == j.quantity,
        ),
      );

  Future<PrescriptionMutationConfirmation> transitionOnce({
    required PatientPrescriptionDetail reviewed,
    required bool cancel,
    String? reason,
  }) async {
    if (_writeAttempted) throw const PrescriptionMutationAlreadyAttempted();
    final record = reviewed.prescription;
    _id(record.id, 'prescriptionId');
    _id(record.patientId, 'patientId');
    if (record.status != PatientPrescriptionStatus.active) {
      throw const PrescriptionMutationEvidenceChanged();
    }
    if (cancel &&
        (reason == null ||
            reason.trim().isEmpty ||
            reason != reason.trim() ||
            reason.length > 1000)) {
      throw const FormatException('Cancellation requires a reason.');
    }
    if (!cancel && reason != null) {
      throw const FormatException(
        'Completion cannot accept a cancellation reason.',
      );
    }
    final revision = reader.sessionNavigationGuard.revision;
    final before = await _session(revision, doctorOnly: cancel);
    if (before.clinicContextId != record.clinicId) {
      throw const PrescriptionMutationScopeUnavailable();
    }
    final current = await inspect(
      prescriptionId: record.id,
      patientId: record.patientId,
    );
    await _verify(before, revision, doctorOnly: cancel);
    if (!_sameEvidence(record, current.prescription)) {
      throw const PrescriptionMutationEvidenceChanged();
    }
    _writeAttempted = true;
    final desired = cancel
        ? PatientPrescriptionStatus.cancelled
        : PatientPrescriptionStatus.completed;
    try {
      final response = await reader.apiClient.postJson(
        'api/v1/prescriptions/${record.id}/${cancel ? 'cancel' : 'complete'}',
        body: cancel
            ? <String, dynamic>{'reason': reason!}
            : <String, dynamic>{},
        bearerToken: before.accessToken,
      );
      await _verify(before, revision, doctorOnly: cancel);
      final accepted = PatientPrescription.fromJson(
        _responseRow(response),
        expectedClinicId: before.clinicContextId!,
        expectedPatientId: record.patientId,
      );
      if (accepted.id != record.id || accepted.status != desired) {
        throw const FormatException(
          'Unexpected prescription transition response.',
        );
      }
      final detail = await inspect(
        prescriptionId: record.id,
        patientId: record.patientId,
      );
      await _verify(before, revision, doctorOnly: cancel);
      if (detail.prescription.status != desired) {
        throw const FormatException(
          'Prescription transition readback differs.',
        );
      }
      return PrescriptionMutationConfirmation(
        prescription: detail,
        interactionWarnings: const <Map<String, dynamic>>[],
      );
    } on Object {
      throw const PrescriptionMutationOutcomeUnconfirmed();
    }
  }
}
