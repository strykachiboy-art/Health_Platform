import 'dart:math';

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

import 'models/nursing_note_workflow.dart';

class NursingNoteSessionUnavailable implements Exception {
  const NursingNoteSessionUnavailable();
  @override
  String toString() => 'Nursing note session is unavailable.';
}

class NursingNotePreflightChanged implements Exception {
  const NursingNotePreflightChanged();
  @override
  String toString() => 'Nursing note state changed; review again.';
}

class NursingNoteOutcomeUnconfirmed implements Exception {
  const NursingNoteOutcomeUnconfirmed();
  @override
  String toString() =>
      'Nursing note write outcome unconfirmed; reconcile without replay.';
}

/// Online-only nursing notes. One POST or PATCH per adapter lifetime.
/// GETs can be repeated deliberately; uncertain writes must never be replayed.
class NursingNoteWorkflowRemoteDataSource {
  NursingNoteWorkflowRemoteDataSource({
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

  static const _readRoles = <String>{'admin', 'doctor', 'nurse'};

  static void _positive(int value, String key) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, key, 'Invalid identifier.');
    }
  }

  static String _text(String raw, String key, int max) {
    final value = raw.trim();
    if (value.isEmpty || value.length > max) {
      throw ArgumentError.value(raw, key, 'Invalid nursing note text length.');
    }
    return value;
  }

  bool _valid(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _session(int revision) async {
    if (!_valid(revision)) {
      throw const NursingNoteSessionUnavailable();
    }
    try {
      final state = await loadSession();
      final s = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          s == null ||
          s.userId <= 0 ||
          s.clinicContextId == null ||
          s.clinicContextId! <= 0 ||
          s.accessToken.isEmpty ||
          s.accessToken != s.accessToken.trim() ||
          !_readRoles.contains(s.role.trim().toLowerCase()) ||
          !_valid(revision)) {
        throw const NursingNoteSessionUnavailable();
      }
      return s;
    } on NursingNoteSessionUnavailable {
      rethrow;
    } on Object {
      throw const NursingNoteSessionUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<Map<String, dynamic>> _get(String path) async {
    final revision = sessionNavigationGuard.revision;
    final before = await _session(revision);
    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    final after = await _session(revision);
    if (!_same(before, after) || !_valid(revision)) {
      throw const NursingNoteSessionUnavailable();
    }
    return response;
  }

  Future<NursingNoteReview?> inspect({required int patientId}) async {
    _positive(patientId, 'patientId');
    final revision = sessionNavigationGuard.revision;
    final before = await _session(revision);
    final admission = await admissions.fetchCurrentAdmission(
      patientId: patientId,
    );
    if (admission == null) {
      final after = await _session(revision);
      if (!_same(before, after) || !_valid(revision)) {
        throw const NursingNoteSessionUnavailable();
      }
      return null;
    }
    if (admission.patientId != patientId || !admission.isActive) {
      throw const FormatException('Current admission mismatch.');
    }
    final response = await _get(
      'api/v1/nursing/admissions/${admission.id}/notes',
    );
    final notes = nursingNoteList(
      response,
      clinicId: before.clinicContextId!,
      patientId: patientId,
      admissionId: admission.id,
    );
    final after = await _session(revision);
    if (!_same(before, after) || !_valid(revision)) {
      throw const NursingNoteSessionUnavailable();
    }
    return NursingNoteReview(
      admission: admission,
      clinicId: before.clinicContextId!,
      notes: notes,
      mayWrite: before.role.trim().toLowerCase() == 'nurse',
    );
  }

  Future<AuthTokenResponse> _preflight(NursingNoteReview reviewed) async {
    if (_writeAttempted) {
      throw const NursingNoteOutcomeUnconfirmed();
    }
    if (!reviewed.mayWrite || !reviewed.admission.isActive) {
      throw const NursingNotePreflightChanged();
    }
    final revision = sessionNavigationGuard.revision;
    final before = await _session(revision);
    if (before.role.trim().toLowerCase() != 'nurse' ||
        before.clinicContextId != reviewed.clinicId) {
      throw const NursingNoteSessionUnavailable();
    }
    final fresh = await inspect(patientId: reviewed.admission.patientId);
    if (fresh == null ||
        fresh.admission.id != reviewed.admission.id ||
        fresh.admission.bedId != reviewed.admission.bedId ||
        fresh.clinicId != reviewed.clinicId ||
        fresh.notes.length != reviewed.notes.length) {
      throw const NursingNotePreflightChanged();
    }
    for (final prior in reviewed.notes) {
      final now = fresh.note(prior.id);
      if (now == null ||
          now.version != prior.version ||
          now.recordVersion != prior.recordVersion ||
          now.status != prior.status ||
          now.recordStatus != prior.recordStatus ||
          now.authorStaffId != prior.authorStaffId ||
          now.text != prior.text ||
          now.kind != prior.kind ||
          now.observedAt != prior.observedAt) {
        throw const NursingNotePreflightChanged();
      }
    }
    final after = await _session(revision);
    if (!_same(before, after) || !_valid(revision)) {
      throw const NursingNoteSessionUnavailable();
    }
    return after;
  }

  static Map<String, dynamic> _noteChild(Map<String, dynamic> response) {
    final child = response['note'];
    if (child is! Map<String, dynamic>) {
      throw const FormatException('Missing nursing note mutation record.');
    }
    return child;
  }

  NursingNoteEntry _parse(
    Map<String, dynamic> response,
    NursingNoteReview reviewed,
  ) => NursingNoteEntry.fromJson(
    response,
    clinicId: reviewed.clinicId,
    patientId: reviewed.admission.patientId,
    admissionId: reviewed.admission.id,
  );

  static String _newKey() {
    final random = Random.secure();
    final bytes = List<int>.generate(16, (_) => random.nextInt(256));
    final hex = bytes.map((n) => n.toRadixString(16).padLeft(2, '0')).join();
    return 'p23ee-$hex';
  }

  Future<Map<String, dynamic>> _writeOnce(
    String path,
    Map<String, dynamic> body,
    AuthTokenResponse approved, {
    required bool patch,
    bool sign = false,
  }) async {
    if (_writeAttempted) {
      throw const NursingNoteOutcomeUnconfirmed();
    }
    final revision = sessionNavigationGuard.revision;
    _writeAttempted = true; // Terminal lock: before network mutation.
    try {
      final response = patch
          ? await apiClient.patchJson(
              path,
              body: body,
              bearerToken: approved.accessToken,
            )
          : await apiClient.postJson(
              path,
              body: body,
              bearerToken: approved.accessToken,
              idempotencyKey: sign ? _newKey() : null,
            );
      final after = await _session(revision);
      if (!_same(approved, after) || !_valid(revision)) {
        throw const NursingNoteSessionUnavailable();
      }
      return response;
    } on Object {
      throw const NursingNoteOutcomeUnconfirmed();
    }
  }

  Future<NursingNoteEntry> _readDetail(
    int noteId,
    NursingNoteReview reviewed,
  ) async {
    final note = _parse(await _get('api/v1/nursing/notes/$noteId'), reviewed);
    if (note.id != noteId) {
      throw const FormatException('Nursing note detail ID mismatch.');
    }
    return note;
  }

  Future<NursingNoteEntry> detail({
    required NursingNoteReview reviewed,
    required int noteId,
  }) async {
    _positive(noteId, 'noteId');
    if (reviewed.note(noteId) == null) {
      throw const NursingNotePreflightChanged();
    }
    return _readDetail(noteId, reviewed);
  }

  Future<List<NursingNoteAmendment>> amendments({
    required NursingNoteReview reviewed,
    required NursingNoteEntry note,
  }) async {
    if (reviewed.note(note.id) == null || !note.mayAmend) {
      throw const NursingNotePreflightChanged();
    }
    final response = await _get('api/v1/nursing/notes/${note.id}/amendments');
    final rows = response['items'];
    final total = response['total'];
    if (rows is! List || total is! int || total < 0 || rows.length != total) {
      throw const FormatException('Invalid amendment history response.');
    }
    final amendments = <NursingNoteAmendment>[];
    final ids = <int>{};
    for (final row in rows) {
      if (row is! Map<String, dynamic>) {
        throw const FormatException('Invalid amendment history item.');
      }
      final amendment = NursingNoteAmendment.fromJson(
        row,
        clinicId: reviewed.clinicId,
        patientId: reviewed.admission.patientId,
        noteId: note.id,
      );
      if (!ids.add(amendment.id)) {
        throw const FormatException('Duplicate nursing note amendment.');
      }
      amendments.add(amendment);
    }
    return List<NursingNoteAmendment>.unmodifiable(amendments);
  }

  Future<NursingNoteEntry> createDraft({
    required NursingNoteReview reviewed,
    required NursingNoteKind kind,
    required String text,
  }) async {
    final clean = _text(text, 'noteText', 20000);
    final authorized = await _preflight(reviewed);
    final response = await _writeOnce(
      'api/v1/nursing/admissions/${reviewed.admission.id}/notes',
      {'note_text': clean, 'note_type': kind.name},
      authorized,
      patch: false,
    );
    final created = _parse(_noteChild(response), reviewed);
    if (reviewed.note(created.id) != null ||
        !created.mayEditDraft ||
        created.text != clean ||
        created.kind != kind) {
      throw const NursingNoteOutcomeUnconfirmed();
    }
    final confirmed = await _readDetail(created.id, reviewed);
    if (confirmed.id != created.id ||
        confirmed.version != created.version ||
        confirmed.text != clean ||
        !confirmed.mayEditDraft) {
      throw const NursingNoteOutcomeUnconfirmed();
    }
    return confirmed;
  }

  Future<NursingNoteEntry> editDraft({
    required NursingNoteReview reviewed,
    required NursingNoteEntry selected,
    required NursingNoteKind kind,
    required String text,
  }) async {
    final clean = _text(text, 'noteText', 20000);
    final before = reviewed.note(selected.id);
    if (before == null ||
        before.version != selected.version ||
        !before.mayEditDraft) {
      throw const NursingNotePreflightChanged();
    }
    final authorized = await _preflight(reviewed);
    final response = await _writeOnce(
      'api/v1/nursing/notes/${selected.id}',
      {'note_text': clean, 'note_type': kind.name},
      authorized,
      patch: true,
    );
    final updated = _parse(_noteChild(response), reviewed);
    final confirmed = await _readDetail(selected.id, reviewed);
    if (updated.id != selected.id ||
        updated.version <= selected.version ||
        confirmed.version != updated.version ||
        !confirmed.mayEditDraft ||
        confirmed.text != clean ||
        confirmed.kind != kind) {
      throw const NursingNoteOutcomeUnconfirmed();
    }
    return confirmed;
  }

  Future<NursingNoteEntry> signDraft({
    required NursingNoteReview reviewed,
    required NursingNoteEntry selected,
  }) async {
    final before = reviewed.note(selected.id);
    if (before == null ||
        before.version != selected.version ||
        !before.maySign) {
      throw const NursingNotePreflightChanged();
    }
    final authorized = await _preflight(reviewed);
    final response = await _writeOnce(
      'api/v1/nursing/notes/${selected.id}/sign',
      <String, dynamic>{},
      authorized,
      patch: false,
      sign: true,
    );
    final signed = _parse(_noteChild(response), reviewed);
    final confirmed = await _readDetail(selected.id, reviewed);
    if (signed.id != selected.id ||
        signed.status != NursingNoteLife.signed ||
        confirmed.id != signed.id ||
        confirmed.status != NursingNoteLife.signed ||
        confirmed.recordStatus != NursingRecordLife.finalRecord ||
        confirmed.signedAt == null ||
        confirmed.text != selected.text) {
      throw const NursingNoteOutcomeUnconfirmed();
    }
    return confirmed;
  }

  Future<NursingNoteEntry> amendSigned({
    required NursingNoteReview reviewed,
    required NursingNoteEntry selected,
    required String reason,
    required String replacementText,
  }) async {
    final cleanReason = _text(reason, 'reason', 10000);
    final cleanText = _text(replacementText, 'noteText', 20000);
    final before = reviewed.note(selected.id);
    if (before == null ||
        before.recordVersion != selected.recordVersion ||
        before.version != selected.version ||
        !before.mayAmend ||
        before.text == cleanText) {
      throw const NursingNotePreflightChanged();
    }
    final authorized = await _preflight(reviewed);
    final response = await _writeOnce(
      'api/v1/nursing/notes/${selected.id}/amend',
      {
        'expected_version': selected.recordVersion,
        'reason': cleanReason,
        'document': {
          'note_type': selected.kind.name,
          'note_text': cleanText,
          'observed_at': selected.observedAt.toUtc().toIso8601String(),
        },
      },
      authorized,
      patch: false,
    );
    final amended = _parse(_noteChild(response), reviewed);
    final confirmed = await _readDetail(selected.id, reviewed);
    if (amended.id != selected.id ||
        amended.recordVersion <= selected.recordVersion ||
        confirmed.recordVersion != amended.recordVersion ||
        !confirmed.mayAmend ||
        confirmed.recordStatus != NursingRecordLife.amended ||
        confirmed.text != cleanText ||
        confirmed.latestAmendmentId == null) {
      throw const NursingNoteOutcomeUnconfirmed();
    }
    return confirmed;
  }
}
