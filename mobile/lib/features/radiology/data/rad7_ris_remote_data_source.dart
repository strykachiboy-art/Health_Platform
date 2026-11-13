import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';

import 'rad7_ris_contract.dart';

/// RAD7: one-shot, online-only documentation writes against RAD1. Never replay.
/// Reuses the existing API client, token and session guard; no new queue.
class Rad7RisRemoteDataSource {
  Rad7RisRemoteDataSource({
    required this.dashboard,
    required this.guard,
    required this.connectivity,
  });

  final DashboardRemoteDataSource dashboard;
  final SessionNavigationGuard guard;
  final DeviceConnectivitySource connectivity;
  final Set<String> _attempts = <String>{};

  bool _current(int revision) =>
      guard.canAccessProtectedRoutes &&
      guard.revision == revision &&
      identical(dashboard.sessionNavigationGuard, guard);

  Future<AuthTokenResponse> _ready(int revision) async {
    if (!_current(revision)) throw const Rad7RisUnavailable();
    try {
      final state = await dashboard.loadSession();
      final actor = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          actor == null ||
          actor.userId <= 0 ||
          actor.clinicContextId == null ||
          actor.clinicContextId! <= 0 ||
          actor.role.trim().toLowerCase() != 'doctor' ||
          actor.accessToken.isEmpty ||
          actor.accessToken.trim() != actor.accessToken ||
          !_current(revision)) {
        throw const Rad7RisUnavailable();
      }
      return actor;
    } on Rad7RisUnavailable {
      rethrow;
    } on Object {
      throw const Rad7RisUnavailable();
    }
  }

  bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.toLowerCase() == b.role.toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<void> _check(AuthTokenResponse before, int revision) async {
    if (!_current(revision) || !_same(before, await _ready(revision))) {
      throw const Rad7RisUnavailable();
    }
  }

  Future<Map<String, dynamic>> _get(String path) async {
    final revision = guard.revision;
    final before = await _ready(revision);
    final envelope = await dashboard.apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    await _check(before, revision);
    return rad7Envelope(envelope);
  }

  Future<Map<String, dynamic>> _write(
    String path,
    Map<String, dynamic> body,
    String uniqueAttempt, {
    bool patch = false,
  }) async {
    final revision = guard.revision;
    final before = await _ready(revision);
    if (!_current(revision) ||
        await connectivity.checkStatus() != DeviceNetworkStatus.connected) {
      throw const Rad7RisUnavailable();
    }
    await _check(before, revision);
    if (!_attempts.add(
      '${before.userId}/${before.clinicContextId}/$revision/$uniqueAttempt',
    )) {
      throw const Rad7RisUnconfirmed();
    }
    // The attempt is reserved BEFORE transport. Any timeout, 409, lost reply,
    // app pause or session change requires an explicit GET, never an auto POST.
    try {
      final envelope = patch
          ? await dashboard.apiClient.patchJson(
              path,
              body: body,
              bearerToken: before.accessToken,
            )
          : await dashboard.apiClient.postJson(
              path,
              body: body,
              bearerToken: before.accessToken,
            );
      await _check(before, revision);
      return rad7Envelope(envelope);
    } on Object {
      throw const Rad7RisUnconfirmed();
    }
  }

  Future<Rad7Exam> byOrder(int orderId) async {
    rad7Positive(orderId, 'order');
    final data = await _get('api/v1/radiology/exams/by-order/$orderId');
    final actor = await _ready(guard.revision);
    final exam = Rad7Exam.fromData(data, clinicId: actor.clinicContextId!);
    if (exam.orderId != orderId) {
      throw const FormatException('RIS imaging order mismatch.');
    }
    return exam;
  }

  Future<Rad7Exam> exam(int examId) async {
    rad7Positive(examId, 'exam');
    final data = await _get('api/v1/radiology/exams/$examId');
    final actor = await _ready(guard.revision);
    final exam = Rad7Exam.fromData(data, clinicId: actor.clinicContextId!);
    if (exam.examId != examId) {
      throw const FormatException('RIS exam mismatch.');
    }
    return exam;
  }

  Future<Rad7Exam> createExam(int orderId) async {
    rad7Positive(orderId, 'order');
    final data = await _write('api/v1/radiology/exams', <String, dynamic>{
      'imaging_order_id': orderId,
    }, 'exam/$orderId');
    final actor = await _ready(guard.revision);
    final result = Rad7Exam.fromData(data, clinicId: actor.clinicContextId!);
    if (result.orderId != orderId) {
      throw const FormatException('RIS order mismatch.');
    }
    return result;
  }

  Future<Rad7Worklist> worklist(int examId) async {
    rad7Positive(examId, 'exam');
    final data = await _get('api/v1/radiology/exams/$examId/worklist');
    return Rad7Worklist.fromData(data, examId: examId);
  }

  Future<Rad7Exam> schedule(
    Rad7Exam exam, {
    required String scheduledAt,
    required String stationCode,
  }) async {
    if (exam.status == 'cancelled' ||
        !RegExp(r'^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:\d\d)$')
            .hasMatch(scheduledAt) ||
        DateTime.tryParse(scheduledAt) == null ||
        !RegExp(r'^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$').hasMatch(stationCode)) {
      throw const Rad7RisUnavailable();
    }
    final data = await _write(
      'api/v1/radiology/exams/${exam.examId}/schedule',
      <String, dynamic>{
        'expected_version': exam.version,
        'scheduled_at': scheduledAt,
        'station_code': stationCode,
      },
      'schedule/${exam.examId}/${exam.version}',
    );
    final updated = Rad7Exam.fromData(data, clinicId: exam.clinicId);
    if (updated.examId != exam.examId || updated.version != exam.version + 1) {
      throw const FormatException('RIS schedule version mismatch.');
    }
    return updated;
  }

  Future<Rad7Exam> cancel(Rad7Exam exam) async {
    final data = await _write(
      'api/v1/radiology/exams/${exam.examId}/schedule/cancel',
      <String, dynamic>{'expected_version': exam.version},
      'cancel/${exam.examId}/${exam.version}',
    );
    final updated = Rad7Exam.fromData(data, clinicId: exam.clinicId);
    if (updated.examId != exam.examId || updated.version != exam.version + 1) {
      throw const FormatException('RIS cancellation version mismatch.');
    }
    return updated;
  }

  Future<Rad7Draft> draft(int examId) async {
    rad7Positive(examId, 'exam');
    final data = await _get('api/v1/radiology/exams/$examId/reports');
    return Rad7Draft.fromData(data, examId: examId);
  }

  Future<void> createDraft(int examId, String content) async {
    rad7Positive(examId, 'exam');
    final text = _cleanNote(content);
    final data = await _write(
      'api/v1/radiology/exams/$examId/reports',
      <String, dynamic>{'content': text},
      'draft/$examId',
    );
    if (data['status'] != 'private_draft' ||
        data['diagnostic_report_signed'] != false ||
        rad7Positive(data['exam_id'], 'exam') != examId ||
        rad7Positive(data['version'], 'revision') != 1) {
      throw const FormatException('RIS draft creation unconfirmed.');
    }
  }

  Future<void> reviseDraft(Rad7Draft previous, String content) async {
    final text = _cleanNote(content);
    final data = await _write(
      'api/v1/radiology/reports/${previous.reportId}',
      <String, dynamic>{'expected_version': previous.version, 'content': text},
      'revise/${previous.reportId}/${previous.version}',
      patch: true,
    );
    if (data['status'] != 'private_draft' ||
        data['diagnostic_report_signed'] != false ||
        rad7Positive(data['report_id'], 'report') != previous.reportId ||
        rad7Positive(data['version'], 'revision') != previous.version + 1) {
      throw const FormatException('RIS revision unconfirmed.');
    }
  }

  String _cleanNote(String value) {
    if (value.isEmpty || value.trim() != value || value.length > 20000) {
      throw const Rad7RisUnavailable();
    }
    return value;
  }
}
