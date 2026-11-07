import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_consultation.dart';

/// This is a clinical online-only writer, never a queue/replay adapter.
/// The backend accepts no idempotency key on these clinical-note routes.
class ConsultationNoteSessionException implements Exception {
  const ConsultationNoteSessionException();
}

class ConsultationNoteStaleException implements Exception {
  const ConsultationNoteStaleException();
}

class ConsultationNoteAmendment {
  const ConsultationNoteAmendment({
    required this.id,
    required this.previousVersion,
    required this.newVersion,
    required this.reason,
    required this.amendedByStaffId,
  });

  final int id;
  final int previousVersion;
  final int newVersion;
  final String reason;
  final int amendedByStaffId;

  static int _positive(Map<String, dynamic> value, String key) {
    final field = value[key];
    if (field is! int || field < 1) {
      throw const FormatException('Invalid clinical amendment metadata.');
    }
    return field;
  }

  factory ConsultationNoteAmendment.parse(
    Map<String, dynamic> value, {
    required PatientConsultation record,
  }) {
    if (_positive(value, 'clinic_id') != record.clinicId ||
        _positive(value, 'patient_id') != record.patientId ||
        _positive(value, 'source_record_id') != record.id ||
        value['new_content'] is! Map<String, dynamic>) {
      throw const FormatException('Clinical amendment scope mismatch.');
    }
    final prior = _positive(value, 'previous_version');
    final next = _positive(value, 'new_version');
    final reason = value['reason'];
    if (next != prior + 1 || reason is! String || reason.trim().isEmpty) {
      throw const FormatException('Invalid clinical amendment version.');
    }
    return ConsultationNoteAmendment(
      id: _positive(value, 'id'),
      previousVersion: prior,
      newVersion: next,
      reason: reason,
      amendedByStaffId: _positive(value, 'amended_by_staff_id'),
    );
  }
}

class ConsultationClinicalNoteDataSource {
  const ConsultationClinicalNoteDataSource({required this.consultations});

  final ConsultationRemoteDataSource consultations;

  static const _roles = <String>{'admin', 'doctor', 'nurse'};
  static const _documentLimits = <String, int>{
    'icd10_code': 10,
    'chief_complaint': 2000,
    'symptoms': 5000,
    'diagnosis': 5000,
    'treatment_plan': 5000,
    'notes': 10000,
    'voice_note_url': 255,
    'transcribed_text': 20000,
  };

  ApiClient get _api => consultations.apiClient;
  SessionNavigationGuard get _guard => consultations.sessionNavigationGuard;

  bool _active(int revision) =>
      _guard.canAccessProtectedRoutes && _guard.revision == revision;

  Future<AuthTokenResponse> _session({bool doctorOnly = false}) async {
    try {
      final state = await consultations.loadSession();
      final s = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          s == null ||
          s.userId < 1 ||
          s.clinicContextId == null ||
          s.clinicContextId! < 1 ||
          s.accessToken.trim().isEmpty ||
          s.accessToken != s.accessToken.trim() ||
          !_roles.contains(s.role.trim().toLowerCase()) ||
          (doctorOnly && s.role.trim().toLowerCase() != 'doctor')) {
        throw const ConsultationNoteSessionException();
      }
      return s;
    } on ConsultationNoteSessionException {
      rethrow;
    } on Object {
      throw const ConsultationNoteSessionException();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<String> authorizedRole() async {
    final revision = _guard.revision;
    if (!_active(revision)) {
      throw const ConsultationNoteSessionException();
    }
    final before = await _session();
    if (!_active(revision)) {
      throw const ConsultationNoteSessionException();
    }
    return before.role.trim().toLowerCase();
  }

  Future<PatientConsultation> load({
    required int patientId,
    required int consultationId,
  }) => consultations.getForPatient(
    patientId: patientId,
    consultationId: consultationId,
  );

  static void _text(String? text, String field, {bool nonNull = false}) {
    if (text == null) {
      if (nonNull) {
        throw ArgumentError.value(text, field, 'Required.');
      }
      return;
    }
    final limit = _documentLimits[field];
    if (limit == null || text.trim().isEmpty || text.length > limit) {
      throw ArgumentError.value(text, field, 'Invalid clinical field.');
    }
  }

  void _baseline(PatientConsultation record) {
    if (record.id < 1 ||
        record.patientId < 1 ||
        record.clinicId < 1 ||
        record.recordVersion < 1) {
      throw ArgumentError('Invalid consultation reference.');
    }
  }

  Future<PatientConsultation> _write({
    required PatientConsultation record,
    required String path,
    required Map<String, dynamic> body,
    required bool doctorOnly,
    required bool patch,
    required bool Function(PatientConsultation) allowed,
    required bool Function(PatientConsultation) expected,
    bool amendmentEnvelope = false,
  }) async {
    _baseline(record);
    if (!allowed(record)) {
      throw const ConsultationNoteStaleException();
    }
    final revision = _guard.revision;
    if (!_active(revision)) {
      throw const ConsultationNoteSessionException();
    }
    final before = await _session(doctorOnly: doctorOnly);
    if (!_active(revision) || before.clinicContextId != record.clinicId) {
      throw const ConsultationNoteSessionException();
    }

    // A fresh server read rejects stale version/status before dispatch.
    // This is not an atomic CAS on PATCH/complete/cancel: backend owns the
    // final concurrency check and all uncertain results remain locked in UI.
    final current = await consultations.getForPatient(
      patientId: record.patientId,
      consultationId: record.id,
    );
    if (!_active(revision)) {
      throw const ConsultationNoteSessionException();
    }
    if (current.recordVersion != record.recordVersion ||
        current.status != record.status ||
        current.recordStatus != record.recordStatus ||
        current.updatedAt != record.updatedAt ||
        !allowed(current)) {
      throw const ConsultationNoteStaleException();
    }
    final checked = await _session(doctorOnly: doctorOnly);
    if (!_active(revision) || !_same(before, checked)) {
      throw const ConsultationNoteSessionException();
    }

    final response = patch
        ? await _api.patchJson(
            path,
            body: body,
            bearerToken: before.accessToken,
          )
        : await _api.postJson(
            path,
            body: body,
            bearerToken: before.accessToken,
          );

    if (!_active(revision)) {
      throw const ConsultationNoteSessionException();
    }
    final after = await _session(doctorOnly: doctorOnly);
    if (!_active(revision) || !_same(before, after)) {
      throw const ConsultationNoteSessionException();
    }
    if (response['success'] != true) {
      throw const FormatException(
        'Clinical mutation response was not successful.',
      );
    }
    final data = response['data'];
    if (data is! Map<String, dynamic>) {
      throw const FormatException('Invalid clinical mutation envelope.');
    }
    final raw = amendmentEnvelope ? data['consultation'] : data;
    if (raw is! Map<String, dynamic>) {
      throw const FormatException('Missing clinical consultation record.');
    }
    final parsed = PatientConsultation.fromJson(
      raw,
      expectedPatientId: record.patientId,
      expectedClinicId: before.clinicContextId!,
    );
    if (parsed.id != record.id ||
        parsed.staffId != record.staffId ||
        !expected(parsed)) {
      throw const FormatException('Clinical mutation response mismatch.');
    }
    if (amendmentEnvelope) {
      final a = data['amendment'];
      if (a is! Map<String, dynamic>) {
        throw const FormatException('Missing versioned amendment.');
      }
      final parsedAmendment = ConsultationNoteAmendment.parse(
        a,
        record: parsed,
      );
      if (parsedAmendment.previousVersion != record.recordVersion ||
          parsedAmendment.newVersion != parsed.recordVersion ||
          parsed.latestAmendmentId != parsedAmendment.id) {
        throw const FormatException('Amendment version chain mismatch.');
      }
    }
    return parsed;
  }

  /// Only changed, nonblank draft fields are sent. The backend forbids
  /// editing a finalized or cancelled consultation.
  Future<PatientConsultation> saveDraft(
    PatientConsultation record,
    Map<String, String> changedFields,
  ) {
    if (changedFields.isEmpty) {
      throw ArgumentError('No draft fields changed.');
    }
    for (final entry in changedFields.entries) {
      if (!_documentLimits.containsKey(entry.key)) {
        throw ArgumentError.value(entry.key, 'changedFields');
      }
      _text(entry.value, entry.key, nonNull: true);
    }
    return _write(
      record: record,
      path: 'api/v1/consultations/${record.id}',
      body: Map<String, dynamic>.from(changedFields),
      doctorOnly: false,
      patch: true,
      allowed: (r) =>
          r.status != PatientConsultationStatus.cancelled &&
          r.recordStatus == PatientClinicalRecordStatus.draft,
      expected: (r) => r.recordStatus == PatientClinicalRecordStatus.draft,
    );
  }

  Future<PatientConsultation> complete(
    PatientConsultation record, {
    required String diagnosis,
    String? treatmentPlan,
    String? notes,
  }) {
    _text(diagnosis, 'diagnosis', nonNull: true);
    _text(treatmentPlan, 'treatment_plan');
    _text(notes, 'notes');
    return _write(
      record: record,
      path: 'api/v1/consultations/${record.id}/complete',
      body: <String, dynamic>{
        'diagnosis': diagnosis.trim(),
        if (treatmentPlan != null) 'treatment_plan': treatmentPlan.trim(),
        if (notes != null) 'notes': notes.trim(),
      },
      doctorOnly: false,
      patch: false,
      allowed: (r) =>
          r.status == PatientConsultationStatus.inProgress &&
          r.recordStatus == PatientClinicalRecordStatus.draft,
      expected: (r) => r.status == PatientConsultationStatus.completed,
    );
  }

  Future<PatientConsultation> cancel(
    PatientConsultation record, {
    String? reason,
  }) {
    if (reason != null && (reason.trim().isEmpty || reason.length > 500)) {
      throw ArgumentError.value(reason, 'reason');
    }
    return _write(
      record: record,
      path: 'api/v1/consultations/${record.id}/cancel',
      body: <String, dynamic>{if (reason != null) 'reason': reason.trim()},
      doctorOnly: false,
      patch: false,
      allowed: (r) =>
          r.status == PatientConsultationStatus.inProgress &&
          r.recordStatus == PatientClinicalRecordStatus.draft,
      expected: (r) => r.status == PatientConsultationStatus.cancelled,
    );
  }

  Future<PatientConsultation> finalize(PatientConsultation record) {
    return _write(
      record: record,
      path: 'api/v1/consultations/${record.id}/finalize',
      body: <String, dynamic>{'expected_version': record.recordVersion},
      doctorOnly: true,
      patch: false,
      allowed: (r) =>
          r.status == PatientConsultationStatus.completed &&
          r.recordStatus == PatientClinicalRecordStatus.draft,
      expected: (r) =>
          r.recordStatus == PatientClinicalRecordStatus.finalized &&
          r.recordVersion == 1 &&
          r.finalizedAt != null,
    );
  }

  Future<PatientConsultation> amend(
    PatientConsultation record, {
    required String reason,
    required Map<String, String?> document,
  }) {
    if (reason.trim().isEmpty || reason.length > 10000) {
      throw ArgumentError.value(reason, 'reason');
    }
    if (document.length != _documentLimits.length ||
        !document.keys.toSet().containsAll(_documentLimits.keys)) {
      throw ArgumentError.value(document.keys, 'document');
    }
    for (final entry in document.entries) {
      _text(entry.value, entry.key);
    }
    return _write(
      record: record,
      path: 'api/v1/consultations/${record.id}/amend',
      body: <String, dynamic>{
        'expected_version': record.recordVersion,
        'reason': reason.trim(),
        'document': document,
      },
      doctorOnly: true,
      patch: false,
      allowed: (r) =>
          r.status == PatientConsultationStatus.completed &&
          r.recordStatus != PatientClinicalRecordStatus.draft,
      expected: (r) =>
          r.recordStatus == PatientClinicalRecordStatus.amended &&
          r.recordVersion == record.recordVersion + 1,
      amendmentEnvelope: true,
    );
  }

  Future<List<ConsultationNoteAmendment>> loadAmendments(
    PatientConsultation record,
  ) async {
    _baseline(record);
    final revision = _guard.revision;
    if (!_active(revision)) {
      throw const ConsultationNoteSessionException();
    }
    final before = await _session();
    if (!_active(revision) || before.clinicContextId != record.clinicId) {
      throw const ConsultationNoteSessionException();
    }
    final result = await _api.getJson(
      'api/v1/consultations/${record.id}/amendments',
      bearerToken: before.accessToken,
    );
    if (!_active(revision)) {
      throw const ConsultationNoteSessionException();
    }
    final after = await _session();
    if (!_active(revision) || !_same(before, after)) {
      throw const ConsultationNoteSessionException();
    }
    if (result['success'] != true || result['data'] is! List) {
      throw const FormatException('Invalid clinical amendment history.');
    }
    final ids = <int>{};
    final amendments = <ConsultationNoteAmendment>[];
    for (final row in result['data'] as List) {
      if (row is! Map<String, dynamic>) {
        throw const FormatException('Invalid amendment history row.');
      }
      final item = ConsultationNoteAmendment.parse(row, record: record);
      if (!ids.add(item.id)) {
        throw const FormatException('Duplicate amendment history row.');
      }
      amendments.add(item);
    }
    return List<ConsultationNoteAmendment>.unmodifiable(amendments);
  }
}
