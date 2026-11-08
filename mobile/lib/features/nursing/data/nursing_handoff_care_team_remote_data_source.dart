import 'dart:math';

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

import 'models/nursing_handoff_care_team.dart';

class HandoffCareSessionUnavailable implements Exception {
  const HandoffCareSessionUnavailable();
  @override
  String toString() => 'Nursing care-team session unavailable.';
}

class HandoffCarePreflightChanged implements Exception {
  const HandoffCarePreflightChanged();
  @override
  String toString() => 'Handoff or care-team state changed; review again.';
}

class HandoffCareOutcomeUnconfirmed implements Exception {
  const HandoffCareOutcomeUnconfirmed();
  @override
  String toString() => 'Clinical write outcome unconfirmed; do not retry.';
}

class HandoffSummaryCounts {
  const HandoffSummaryCounts({
    required this.handoffId,
    required this.criticalAlerts,
    required this.pendingLabOrders,
    required this.dueAdministrations,
    required this.tasks,
  });
  final int handoffId;
  final int criticalAlerts;
  final int pendingLabOrders;
  final int dueAdministrations;
  final int tasks;
}

/// P23E.F: REST-only, live admission scope, explicit one-write terminal lock.
/// Neither clinical mutations nor their payloads are saved in offline queues.
class NursingHandoffCareTeamRemoteDataSource {
  NursingHandoffCareTeamRemoteDataSource({
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

  static const _readerRoles = <String>{'admin', 'doctor', 'nurse'};

  static int _id(int value, String key) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, key, 'Invalid clinical identifier.');
    }
    return value;
  }

  static String _text(String value, String key, {int max = 10000}) {
    final trimmed = value.trim();
    if (trimmed.isEmpty || trimmed.length > max) {
      throw ArgumentError.value(value, key, 'Invalid clinical text.');
    }
    return trimmed;
  }

  static String? _optionalText(String? value, String key) {
    if (value == null || value.trim().isEmpty) {
      return null;
    }
    return _text(value, key);
  }

  bool _valid(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _session(int revision) async {
    if (!_valid(revision)) {
      throw const HandoffCareSessionUnavailable();
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
          !_readerRoles.contains(s.role.trim().toLowerCase()) ||
          !_valid(revision)) {
        throw const HandoffCareSessionUnavailable();
      }
      return s;
    } on HandoffCareSessionUnavailable {
      rethrow;
    } on Object {
      throw const HandoffCareSessionUnavailable();
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
    final value = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    final after = await _session(revision);
    if (!_same(before, after) || !_valid(revision)) {
      throw const HandoffCareSessionUnavailable();
    }
    return value;
  }

  Future<NursingHandoffCareReview?> inspect({required int patientId}) async {
    _id(patientId, 'patientId');
    final revision = sessionNavigationGuard.revision;
    final before = await _session(revision);
    final admission = await admissions.fetchCurrentAdmission(
      patientId: patientId,
    );
    if (admission == null) {
      final after = await _session(revision);
      if (!_same(before, after)) {
        throw const HandoffCareSessionUnavailable();
      }
      return null;
    }
    if (!admission.isActive || admission.patientId != patientId) {
      throw const FormatException('Current admission scope mismatch.');
    }
    final scope = 'api/v1/nursing/admissions/${admission.id}';
    final clinicId = before.clinicContextId!;
    final team = AdmissionCareTeam.fromJson(
      await _get('$scope/care-team'),
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admission.id,
    );
    final handoffs = handoffList(
      await _get('$scope/handoffs'),
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admission.id,
    );
    final history = careTeamHistory(
      await _get('$scope/care-team/assignment-history'),
    );
    final after = await _session(revision);
    if (!_same(before, after) || !_valid(revision)) {
      throw const HandoffCareSessionUnavailable();
    }
    for (final assignment in <PrimaryClinicianAssignment?>[
      team.primaryDoctor,
      team.primaryNurse,
    ]) {
      if (assignment != null &&
          !history.any(
            (item) =>
                item.id == assignment.id && item.staffId == assignment.staffId,
          )) {
        throw const FormatException('Current assignment missing from history.');
      }
    }
    return NursingHandoffCareReview(
      admission: admission,
      clinicId: clinicId,
      sessionUserId: before.userId,
      careTeam: team,
      handoffs: handoffs,
      assignmentHistory: history,
      mayHandoffWrite: before.role.trim().toLowerCase() == 'nurse',
      mayTeamWrite: before.role.trim().toLowerCase() == 'admin',
    );
  }

  Future<AuthTokenResponse> _preflight(
    NursingHandoffCareReview reviewed, {
    required bool requireAdmin,
  }) async {
    if (_writeAttempted) {
      throw const HandoffCareOutcomeUnconfirmed();
    }
    if (!reviewed.admission.isActive ||
        !(requireAdmin ? reviewed.mayTeamWrite : reviewed.mayHandoffWrite)) {
      throw const HandoffCarePreflightChanged();
    }
    final revision = sessionNavigationGuard.revision;
    final before = await _session(revision);
    final expectedRole = requireAdmin ? 'admin' : 'nurse';
    if (before.role.trim().toLowerCase() != expectedRole ||
        before.userId != reviewed.sessionUserId ||
        before.clinicContextId != reviewed.clinicId) {
      throw const HandoffCareSessionUnavailable();
    }
    final fresh = await inspect(patientId: reviewed.admission.patientId);
    if (fresh == null ||
        fresh.admission.id != reviewed.admission.id ||
        fresh.admission.bedId != reviewed.admission.bedId ||
        fresh.clinicId != reviewed.clinicId ||
        fresh.handoffs.length != reviewed.handoffs.length ||
        fresh.assignmentHistory.length != reviewed.assignmentHistory.length) {
      throw const HandoffCarePreflightChanged();
    }
    for (final kind in PrimaryClinicianKind.values) {
      final old = reviewed.careTeam.assignment(kind);
      final now = fresh.careTeam.assignment(kind);
      if (old?.id != now?.id ||
          old?.staffId != now?.staffId ||
          old?.active != now?.active) {
        throw const HandoffCarePreflightChanged();
      }
    }
    for (final old in reviewed.handoffs) {
      final now = fresh.handoff(old.id);
      if (now == null ||
          now.version != old.version ||
          now.status != old.status ||
          now.incomingStaffId != old.incomingStaffId ||
          now.outgoingStaffId != old.outgoingStaffId) {
        throw const HandoffCarePreflightChanged();
      }
    }
    final after = await _session(revision);
    if (!_same(before, after) || !_valid(revision)) {
      throw const HandoffCareSessionUnavailable();
    }
    return after;
  }

  static String _key() {
    final random = Random.secure();
    final bytes = List<int>.generate(16, (_) => random.nextInt(256));
    final hex = bytes.map((n) => n.toRadixString(16).padLeft(2, '0')).join();
    return 'p23ef-$hex';
  }

  Future<Map<String, dynamic>> _writeOnce(
    String path,
    Map<String, dynamic> body,
    AuthTokenResponse approved, {
    bool patch = false,
    bool acknowledge = false,
  }) async {
    if (_writeAttempted) {
      throw const HandoffCareOutcomeUnconfirmed();
    }
    final revision = sessionNavigationGuard.revision;
    _writeAttempted = true;
    try {
      final result = patch
          ? await apiClient.patchJson(
              path,
              body: body,
              bearerToken: approved.accessToken,
            )
          : await apiClient.postJson(
              path,
              body: body,
              bearerToken: approved.accessToken,
              idempotencyKey: acknowledge ? _key() : null,
            );
      final after = await _session(revision);
      if (!_same(approved, after) || !_valid(revision)) {
        throw const HandoffCareSessionUnavailable();
      }
      return result;
    } on Object {
      // Even a 5xx/timeout can mean the server already committed.
      throw const HandoffCareOutcomeUnconfirmed();
    }
  }

  static Map<String, dynamic> _child(
    Map<String, dynamic> response,
    String key,
  ) {
    final child = response[key];
    if (child is! Map<String, dynamic>) {
      throw FormatException('Missing clinical mutation response: $key');
    }
    return child;
  }

  NursingHandoffEntry _handoff(
    Map<String, dynamic> json,
    NursingHandoffCareReview reviewed,
  ) => NursingHandoffEntry.fromJson(
    json,
    clinicId: reviewed.clinicId,
    patientId: reviewed.admission.patientId,
    admissionId: reviewed.admission.id,
  );

  Future<NursingHandoffEntry> _readHandoff(
    int handoffId,
    NursingHandoffCareReview reviewed,
  ) async {
    final found = _handoff(
      await _get('api/v1/nursing/handoffs/$handoffId'),
      reviewed,
    );
    if (found.id != handoffId) {
      throw const FormatException('Wrong nursing handoff detail returned.');
    }
    return found;
  }

  Future<HandoffSummaryCounts> summary({
    required NursingHandoffCareReview reviewed,
    required NursingHandoffEntry selected,
  }) async {
    if (reviewed.handoff(selected.id) == null) {
      throw const HandoffCarePreflightChanged();
    }
    final response = await _get(
      'api/v1/nursing/handoffs/${selected.id}/summary',
    );
    final record = _handoff(_child(response, 'handoff'), reviewed);
    if (record.id != selected.id ||
        record.version != selected.version ||
        record.status != selected.status) {
      throw const HandoffCarePreflightChanged();
    }
    int count(String key) {
      final item = response[key];
      if (item is! List) {
        throw FormatException('Invalid clinical summary: $key');
      }
      return item.length;
    }

    return HandoffSummaryCounts(
      handoffId: record.id,
      criticalAlerts: count('critical_alerts'),
      pendingLabOrders: count('pending_lab_orders'),
      dueAdministrations: count('due_medication_administrations'),
      tasks: count('tasks'),
    );
  }

  Future<NursingHandoffEntry> createHandoff({
    required NursingHandoffCareReview reviewed,
    required DateTime shiftStart,
    required DateTime shiftEnd,
    required int incomingStaffId,
    required String situation,
    required String assessment,
    required String recommendation,
    String? background,
    String? safetyConcerns,
    String? pendingActions,
  }) async {
    _id(incomingStaffId, 'incomingStaffId');
    if (!shiftEnd.isAfter(shiftStart)) {
      throw const FormatException('Shift end must follow shift start.');
    }
    final body = <String, dynamic>{
      'shift_start_at': shiftStart.toUtc().toIso8601String(),
      'shift_end_at': shiftEnd.toUtc().toIso8601String(),
      'incoming_staff_id': incomingStaffId,
      'situation': _text(situation, 'situation'),
      'assessment': _text(assessment, 'assessment'),
      'recommendation': _text(recommendation, 'recommendation'),
      'background': _optionalText(background, 'background'),
      'safety_concerns': _optionalText(safetyConcerns, 'safetyConcerns'),
      'pending_actions': _optionalText(pendingActions, 'pendingActions'),
    };
    final authorized = await _preflight(reviewed, requireAdmin: false);
    final response = await _writeOnce(
      'api/v1/nursing/admissions/${reviewed.admission.id}/handoffs',
      body,
      authorized,
    );
    try {
      final created = _handoff(_child(response, 'handoff'), reviewed);
      final confirmed = await _readHandoff(created.id, reviewed);
      if (reviewed.handoff(created.id) != null ||
          created.status != HandoffLife.draft ||
          confirmed.status != created.status ||
          confirmed.incomingStaffId != incomingStaffId ||
          confirmed.situation != body['situation'] ||
          confirmed.assessment != body['assessment'] ||
          confirmed.recommendation != body['recommendation'] ||
          confirmed.background != body['background'] ||
          confirmed.safetyConcerns != body['safety_concerns'] ||
          confirmed.pendingActions != body['pending_actions']) {
        throw const HandoffCareOutcomeUnconfirmed();
      }
      return confirmed;
    } on Object {
      throw const HandoffCareOutcomeUnconfirmed();
    }
  }

  Future<NursingHandoffEntry> editHandoff({
    required NursingHandoffCareReview reviewed,
    required NursingHandoffEntry selected,
    required String situation,
    required String assessment,
    required String recommendation,
    required int incomingStaffId,
    String? background,
    String? safetyConcerns,
    String? pendingActions,
  }) async {
    _id(incomingStaffId, 'incomingStaffId');
    final prior = reviewed.handoff(selected.id);
    if (prior == null || !prior.mayEdit || prior.version != selected.version) {
      throw const HandoffCarePreflightChanged();
    }
    final body = <String, dynamic>{
      'situation': _text(situation, 'situation'),
      'assessment': _text(assessment, 'assessment'),
      'recommendation': _text(recommendation, 'recommendation'),
      'incoming_staff_id': incomingStaffId,
      'background': _optionalText(background, 'background'),
      'safety_concerns': _optionalText(safetyConcerns, 'safetyConcerns'),
      'pending_actions': _optionalText(pendingActions, 'pendingActions'),
    };
    final authorized = await _preflight(reviewed, requireAdmin: false);
    final response = await _writeOnce(
      'api/v1/nursing/handoffs/${selected.id}',
      body,
      authorized,
      patch: true,
    );
    try {
      final changed = _handoff(_child(response, 'handoff'), reviewed);
      final confirmed = await _readHandoff(selected.id, reviewed);
      if (changed.id != selected.id ||
          confirmed.version <= selected.version ||
          confirmed.status != HandoffLife.draft ||
          confirmed.version != changed.version ||
          confirmed.incomingStaffId != incomingStaffId ||
          changed.situation != confirmed.situation ||
          confirmed.situation != body['situation'] ||
          confirmed.assessment != body['assessment'] ||
          confirmed.recommendation != body['recommendation'] ||
          confirmed.background != body['background'] ||
          confirmed.safetyConcerns != body['safety_concerns'] ||
          confirmed.pendingActions != body['pending_actions']) {
        throw const HandoffCareOutcomeUnconfirmed();
      }
      return confirmed;
    } on Object {
      throw const HandoffCareOutcomeUnconfirmed();
    }
  }

  Future<NursingHandoffEntry> transitionHandoff({
    required NursingHandoffCareReview reviewed,
    required NursingHandoffEntry selected,
    required HandoffLife action,
  }) async {
    final old = reviewed.handoff(selected.id);
    if (old == null ||
        old.version != selected.version ||
        !((action == HandoffLife.ready && old.mayReady) ||
            (action == HandoffLife.acknowledged && old.mayAcknowledge) ||
            (action == HandoffLife.cancelled && old.mayCancel))) {
      throw const HandoffCarePreflightChanged();
    }
    // A clinical summary preview is required before final acknowledgement.
    if (action == HandoffLife.acknowledged) {
      await summary(reviewed: reviewed, selected: selected);
    }
    final authorized = await _preflight(reviewed, requireAdmin: false);
    final suffix = switch (action) {
      HandoffLife.ready => 'ready',
      HandoffLife.acknowledged => 'acknowledge',
      HandoffLife.cancelled => 'cancel',
      HandoffLife.draft => throw const HandoffCarePreflightChanged(),
    };
    final response = await _writeOnce(
      'api/v1/nursing/handoffs/${selected.id}/$suffix',
      <String, dynamic>{},
      authorized,
      acknowledge: action == HandoffLife.acknowledged,
    );
    try {
      final changed = _handoff(_child(response, 'handoff'), reviewed);
      final confirmed = await _readHandoff(selected.id, reviewed);
      if (confirmed.id != selected.id ||
          confirmed.version <= selected.version ||
          confirmed.status != action ||
          changed.status != action ||
          changed.version != confirmed.version) {
        throw const HandoffCareOutcomeUnconfirmed();
      }
      if (action == HandoffLife.acknowledged) {
        // CT3B: acknowledgement atomically transfers the primary nurse.
        final team = AdmissionCareTeam.fromJson(
          await _get(
            'api/v1/nursing/admissions/${reviewed.admission.id}/care-team',
          ),
          clinicId: reviewed.clinicId,
          patientId: reviewed.admission.patientId,
          admissionId: reviewed.admission.id,
        );
        if (team.primaryNurse?.staffId != selected.incomingStaffId ||
            team.primaryNurse?.active != true) {
          throw const HandoffCareOutcomeUnconfirmed();
        }
      }
      return confirmed;
    } on Object {
      throw const HandoffCareOutcomeUnconfirmed();
    }
  }

  Future<PrimaryClinicianAssignment> assignPrimary({
    required NursingHandoffCareReview reviewed,
    required PrimaryClinicianKind kind,
    required int staffId,
    required String reason,
  }) async {
    _id(staffId, 'staffId');
    final current = reviewed.careTeam.assignment(kind);
    if (current?.staffId == staffId) {
      throw const HandoffCarePreflightChanged();
    }
    final authorized = await _preflight(reviewed, requireAdmin: true);
    final body = <String, dynamic>{
      'assignment_kind': primaryKindWire(kind),
      'staff_id': staffId,
      'assignment_reason': _text(reason, 'assignmentReason', max: 500),
      'expected_current_assignment_id': ?current?.id,
    };
    final response = await _writeOnce(
      'api/v1/nursing/admissions/${reviewed.admission.id}/care-team/assignments',
      body,
      authorized,
    );
    try {
      final created = PrimaryClinicianAssignment.fromJson(
        _child(response, 'assignment'),
      );
      final team = AdmissionCareTeam.fromJson(
        await _get(
          'api/v1/nursing/admissions/${reviewed.admission.id}/care-team',
        ),
        clinicId: reviewed.clinicId,
        patientId: reviewed.admission.patientId,
        admissionId: reviewed.admission.id,
      );
      final confirmed = team.assignment(kind);
      if (created.kind != kind ||
          created.staffId != staffId ||
          !created.current ||
          confirmed?.id != created.id ||
          confirmed?.staffId != staffId) {
        throw const HandoffCareOutcomeUnconfirmed();
      }
      return confirmed!;
    } on Object {
      throw const HandoffCareOutcomeUnconfirmed();
    }
  }

  Future<PrimaryClinicianAssignment> endPrimary({
    required NursingHandoffCareReview reviewed,
    required PrimaryClinicianKind kind,
    required String reason,
  }) async {
    final current = reviewed.careTeam.assignment(kind);
    if (current == null) {
      throw const HandoffCarePreflightChanged();
    }
    final authorized = await _preflight(reviewed, requireAdmin: true);
    final response = await _writeOnce(
      'api/v1/nursing/admissions/${reviewed.admission.id}/care-team/end-assignment',
      {
        'assignment_kind': primaryKindWire(kind),
        'expected_current_assignment_id': current.id,
        'end_reason': _text(reason, 'endReason', max: 500),
      },
      authorized,
    );
    try {
      final ended = PrimaryClinicianAssignment.fromJson(
        _child(response, 'assignment'),
      );
      final team = AdmissionCareTeam.fromJson(
        await _get(
          'api/v1/nursing/admissions/${reviewed.admission.id}/care-team',
        ),
        clinicId: reviewed.clinicId,
        patientId: reviewed.admission.patientId,
        admissionId: reviewed.admission.id,
      );
      if (ended.id != current.id ||
          ended.kind != kind ||
          ended.current ||
          team.assignment(kind) != null) {
        throw const HandoffCareOutcomeUnconfirmed();
      }
      return ended;
    } on Object {
      throw const HandoffCareOutcomeUnconfirmed();
    }
  }
}
