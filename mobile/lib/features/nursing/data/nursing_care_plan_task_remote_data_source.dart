import 'dart:math';

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

import 'models/nursing_care_plan_task.dart';

class NursingCareSessionUnavailable implements Exception {
  const NursingCareSessionUnavailable();
  @override
  String toString() => 'Nursing session unavailable.';
}

class NursingCarePreflightChanged implements Exception {
  const NursingCarePreflightChanged();
  @override
  String toString() => 'Nursing clinical state changed. Review again.';
}

class NursingCareOutcomeUnconfirmed implements Exception {
  const NursingCareOutcomeUnconfirmed();
  @override
  String toString() =>
      'Clinical action outcome not confirmed. Reconcile on server; do not retry.';
}

/// P23E.D: admission-bound, no PHI cache, no offline mutations or retries.
/// A started POST permanently locks this adapter for its screen lifetime.
class NursingCarePlanTaskRemoteDataSource {
  NursingCarePlanTaskRemoteDataSource({
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

  static const _roles = <String>{'admin', 'doctor', 'nurse'};

  static void _positive(int value, String key) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, key, 'Must be a valid identifier.');
    }
  }

  static String _requiredText(String value, String key, int max) {
    final clean = value.trim();
    if (clean.isEmpty || clean.length > max) {
      throw ArgumentError.value(value, key, 'Invalid clinical text length.');
    }
    return clean;
  }

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _session(int revision) async {
    if (!_current(revision)) {
      throw const NursingCareSessionUnavailable();
    }
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.trim().isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          !_roles.contains(session.role.trim().toLowerCase()) ||
          !_current(revision)) {
        throw const NursingCareSessionUnavailable();
      }
      return session;
    } on NursingCareSessionUnavailable {
      rethrow;
    } on Object {
      throw const NursingCareSessionUnavailable();
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
    if (!_same(before, after) || !_current(revision)) {
      throw const NursingCareSessionUnavailable();
    }
    return response;
  }

  /// GET-only. Null indicates the exact no-current-admission 404 from the
  /// existing patient-admission adapter; all other errors propagate.
  Future<NursingCareSnapshot?> inspect({required int patientId}) async {
    _positive(patientId, 'patientId');
    final revision = sessionNavigationGuard.revision;
    final before = await _session(revision);
    final admission = await admissions.fetchCurrentAdmission(
      patientId: patientId,
    );
    if (admission == null) {
      final after = await _session(revision);
      if (!_same(before, after)) {
        throw const NursingCareSessionUnavailable();
      }
      return null;
    }
    if (!admission.isActive || admission.patientId != patientId) {
      throw const FormatException('Current admission context invalid.');
    }
    final plans = NursingCareList<NursingCarePlan>.parse(
      await _get('api/v1/nursing/admissions/${admission.id}/care-plans'),
      (row) => NursingCarePlan.fromJson(
        row,
        expectedClinicId: before.clinicContextId!,
        expectedPatientId: patientId,
        expectedAdmissionId: admission.id,
      ),
    );
    final tasks = NursingCareList<NursingCareTask>.parse(
      await _get('api/v1/nursing/admissions/${admission.id}/tasks'),
      (row) => NursingCareTask.fromJson(
        row,
        expectedClinicId: before.clinicContextId!,
        expectedPatientId: patientId,
        expectedAdmissionId: admission.id,
      ),
    );
    for (final task in tasks.items) {
      if (!plans.items.any((plan) => plan.id == task.carePlanId)) {
        throw const FormatException(
          'Nursing task belongs to an unknown care plan.',
        );
      }
    }
    final after = await _session(revision);
    if (!_same(before, after) || !_current(revision)) {
      throw const NursingCareSessionUnavailable();
    }
    return NursingCareSnapshot(
      admission: admission,
      clinicId: before.clinicContextId!,
      plans: plans.items,
      tasks: tasks.items,
      mayWrite: before.role.trim().toLowerCase() == 'nurse',
    );
  }

  Future<AuthTokenResponse> _freshGate(NursingCareSnapshot reviewed) async {
    if (_writeAttempted) {
      throw const NursingCareOutcomeUnconfirmed();
    }
    if (!reviewed.mayWrite || !reviewed.admission.isActive) {
      throw const NursingCarePreflightChanged();
    }
    final revision = sessionNavigationGuard.revision;
    final before = await _session(revision);
    if (before.role.trim().toLowerCase() != 'nurse' ||
        before.clinicContextId != reviewed.clinicId) {
      throw const NursingCareSessionUnavailable();
    }
    final fresh = await inspect(patientId: reviewed.admission.patientId);
    if (fresh == null ||
        fresh.admission.id != reviewed.admission.id ||
        fresh.admission.bedId != reviewed.admission.bedId ||
        fresh.clinicId != reviewed.clinicId) {
      throw const NursingCarePreflightChanged();
    }
    for (final plan in reviewed.plans) {
      final current = fresh.plan(plan.id);
      if (current == null ||
          current.version != plan.version ||
          current.status != plan.status) {
        throw const NursingCarePreflightChanged();
      }
    }
    for (final task in reviewed.tasks) {
      final current = fresh.task(task.id);
      if (current == null ||
          current.version != task.version ||
          current.status != task.status) {
        throw const NursingCarePreflightChanged();
      }
    }
    if (fresh.plans.length != reviewed.plans.length ||
        fresh.tasks.length != reviewed.tasks.length) {
      throw const NursingCarePreflightChanged();
    }
    final after = await _session(revision);
    if (!_same(before, after) || !_current(revision)) {
      throw const NursingCareSessionUnavailable();
    }
    return after;
  }

  static Map<String, dynamic> _child(Map<String, dynamic> json, String key) {
    final value = json[key];
    if (value is! Map<String, dynamic>) {
      throw FormatException('Invalid nursing mutation result: $key');
    }
    return value;
  }

  static String _idempotencyKey() {
    final rng = Random.secure();
    final bytes = List<int>.generate(16, (_) => rng.nextInt(256));
    return 'p23ed-${bytes.map((n) => n.toRadixString(16).padLeft(2, '0')).join()}';
  }

  Future<Map<String, dynamic>> _sendOnce(
    String path,
    Map<String, dynamic> body,
    AuthTokenResponse approved, {
    bool completion = false,
  }) async {
    if (_writeAttempted) {
      throw const NursingCareOutcomeUnconfirmed();
    }
    // This lock is terminal for the lifetime of the entire screen adapter.
    final revision = sessionNavigationGuard.revision;
    _writeAttempted = true;
    try {
      final response = await apiClient.postJson(
        path,
        body: body,
        bearerToken: approved.accessToken,
        idempotencyKey: completion ? _idempotencyKey() : null,
      );
      final after = await _session(revision);
      if (!_same(approved, after) || !_current(revision)) {
        throw const NursingCareSessionUnavailable();
      }
      return response;
    } on Object {
      // Never retry an unknown outcome (including a lost POST response).
      throw const NursingCareOutcomeUnconfirmed();
    }
  }

  NursingCarePlan _plan(
    Map<String, dynamic> json,
    NursingCareSnapshot snapshot,
  ) => NursingCarePlan.fromJson(
    json,
    expectedClinicId: snapshot.clinicId,
    expectedPatientId: snapshot.admission.patientId,
    expectedAdmissionId: snapshot.admission.id,
  );

  NursingCareTask _task(
    Map<String, dynamic> json,
    NursingCareSnapshot snapshot,
  ) => NursingCareTask.fromJson(
    json,
    expectedClinicId: snapshot.clinicId,
    expectedPatientId: snapshot.admission.patientId,
    expectedAdmissionId: snapshot.admission.id,
  );

  Future<NursingCarePlan> createPlan({
    required NursingCareSnapshot reviewed,
    required String title,
    required String problem,
    required String goal,
  }) async {
    final body = <String, dynamic>{
      'title': _requiredText(title, 'title', 200),
      'problem': _requiredText(problem, 'problem', 10000),
      'goal': _requiredText(goal, 'goal', 10000),
    };
    final approved = await _freshGate(reviewed);
    final response = await _sendOnce(
      'api/v1/nursing/admissions/${reviewed.admission.id}/care-plans',
      body,
      approved,
    );
    final created = _plan(_child(response, 'care_plan'), reviewed);
    if (created.status != NursingPlanStatus.active ||
        reviewed.plan(created.id) != null) {
      throw const NursingCareOutcomeUnconfirmed();
    }
    final confirmed = _plan(
      await _get('api/v1/nursing/care-plans/${created.id}'),
      reviewed,
    );
    if (confirmed.id != created.id || confirmed.status != created.status) {
      throw const NursingCareOutcomeUnconfirmed();
    }
    return confirmed;
  }

  Future<NursingCarePlan> changePlan({
    required NursingCareSnapshot reviewed,
    required NursingCarePlan selected,
    required NursingPlanAction action,
  }) async {
    final prior = reviewed.plan(selected.id);
    if (prior == null ||
        prior.version != selected.version ||
        prior.status != selected.status ||
        !prior.allows(action)) {
      throw const NursingCarePreflightChanged();
    }
    final approved = await _freshGate(reviewed);
    final step = action.name;
    final response = await _sendOnce(
      'api/v1/nursing/care-plans/${selected.id}/$step',
      <String, dynamic>{},
      approved,
      completion: action == NursingPlanAction.complete,
    );
    final changed = _plan(_child(response, 'care_plan'), reviewed);
    final expected = switch (action) {
      NursingPlanAction.hold => NursingPlanStatus.onHold,
      NursingPlanAction.resume => NursingPlanStatus.active,
      NursingPlanAction.complete => NursingPlanStatus.completed,
      NursingPlanAction.cancel => NursingPlanStatus.cancelled,
    };
    final confirmed = _plan(
      await _get('api/v1/nursing/care-plans/${selected.id}'),
      reviewed,
    );
    if (changed.id != selected.id ||
        changed.status != expected ||
        confirmed.id != selected.id ||
        confirmed.status != expected ||
        confirmed.version < changed.version ||
        changed.version <= selected.version) {
      throw const NursingCareOutcomeUnconfirmed();
    }
    return confirmed;
  }

  Future<NursingCareTask> createTask({
    required NursingCareSnapshot reviewed,
    required NursingCarePlan selectedPlan,
    required String description,
    String? instructions,
    NursingTaskPriority priority = NursingTaskPriority.routine,
  }) async {
    final plan = reviewed.plan(selectedPlan.id);
    if (plan == null ||
        plan.version != selectedPlan.version ||
        plan.status != NursingPlanStatus.active) {
      throw const NursingCarePreflightChanged();
    }
    final instruction = instructions?.trim();
    if (instruction != null && instruction.length > 10000) {
      throw ArgumentError.value(instructions, 'instructions');
    }
    final approved = await _freshGate(reviewed);
    final response = await _sendOnce(
      'api/v1/nursing/care-plans/${plan.id}/tasks',
      <String, dynamic>{
        'description': _requiredText(description, 'description', 10000),
        'priority': priority.name,
        if (instruction != null && instruction.isNotEmpty)
          'instructions': instruction,
      },
      approved,
    );
    final created = _task(_child(response, 'task'), reviewed);
    final confirmed = _task(
      await _get('api/v1/nursing/tasks/${created.id}'),
      reviewed,
    );
    if (created.carePlanId != plan.id ||
        created.priority != priority ||
        reviewed.task(created.id) != null ||
        created.status != NursingTaskStatus.pending ||
        confirmed.id != created.id ||
        confirmed.carePlanId != plan.id ||
        confirmed.status != NursingTaskStatus.pending) {
      throw const NursingCareOutcomeUnconfirmed();
    }
    return confirmed;
  }

  Future<NursingCareTask> changeTask({
    required NursingCareSnapshot reviewed,
    required NursingCareTask selected,
    required NursingTaskAction action,
    String? cancellationReason,
  }) async {
    final task = reviewed.task(selected.id);
    if (task == null ||
        task.version != selected.version ||
        task.status != selected.status ||
        !task.allows(action)) {
      throw const NursingCarePreflightChanged();
    }
    final body = <String, dynamic>{};
    if (action == NursingTaskAction.cancel) {
      body['reason'] = _requiredText(
        cancellationReason ?? '',
        'cancellationReason',
        10000,
      );
    }
    final approved = await _freshGate(reviewed);
    final response = await _sendOnce(
      'api/v1/nursing/tasks/${task.id}/${action.name}',
      body,
      approved,
      completion: action == NursingTaskAction.complete,
    );
    final changed = _task(_child(response, 'task'), reviewed);
    final expected = switch (action) {
      NursingTaskAction.start => NursingTaskStatus.inProgress,
      NursingTaskAction.complete => NursingTaskStatus.completed,
      NursingTaskAction.cancel => NursingTaskStatus.cancelled,
    };
    final confirmed = _task(
      await _get('api/v1/nursing/tasks/${task.id}'),
      reviewed,
    );
    if (changed.id != task.id ||
        changed.carePlanId != task.carePlanId ||
        changed.status != expected ||
        confirmed.id != task.id ||
        confirmed.carePlanId != task.carePlanId ||
        confirmed.status != expected ||
        confirmed.version < changed.version ||
        changed.version <= selected.version) {
      throw const NursingCareOutcomeUnconfirmed();
    }
    return confirmed;
  }
}
