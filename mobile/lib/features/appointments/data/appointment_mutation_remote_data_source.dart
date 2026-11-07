import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/appointment_record.dart';

/// A non-idempotent mutation is confirmed only by a validated server record.
/// A timeout, connection failure, 5xx, malformed response, or session change
/// after dispatch requires a fresh, authorized server read before any retry.
enum AppointmentMutationOutcome {
  confirmed,
  rejected,
  reconciliationRequired,
  sessionUnavailable,
  alreadyInFlight,
}

class AppointmentMutationResult {
  const AppointmentMutationResult(this.outcome, {this.appointment});

  final AppointmentMutationOutcome outcome;
  final AppointmentRecord? appointment;

  bool get isConfirmed =>
      outcome == AppointmentMutationOutcome.confirmed && appointment != null;
}

/// Uses the existing authenticated HTTP transport, NEVER the offline queue.
/// No automatic retry and no Idempotency-Key header: the backend has not
/// established that contract for reschedule or cancel.
class AppointmentMutationRemoteDataSource {
  AppointmentMutationRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _roles = <String>{'admin', 'doctor', 'nurse', 'receptionist'};
  static const _definitiveRejectionCodes = <int>{400, 401, 403, 404, 409, 422};

  final Set<int> _inFlight = <int>{};
  final Set<int> _needsReconciliation = <int>{};

  bool _isCurrent(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse?> _session() async {
    try {
      final state = await loadSession();
      final value = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          value == null ||
          value.userId <= 0 ||
          value.clinicContextId == null ||
          value.clinicContextId! <= 0 ||
          value.accessToken.isEmpty ||
          value.accessToken.trim() != value.accessToken ||
          !_roles.contains(value.role.trim().toLowerCase())) {
        return null;
      }
      return value;
    } on Object {
      return null;
    }
  }

  bool _sameSession(AuthTokenResponse first, AuthTokenResponse next) =>
      first.userId == next.userId &&
      first.clinicContextId == next.clinicContextId &&
      first.role.trim().toLowerCase() == next.role.trim().toLowerCase() &&
      first.accessToken == next.accessToken &&
      first.refreshToken == next.refreshToken;

  bool _eligible(AppointmentRecord record) =>
      record.status == AppointmentRecordStatus.scheduled ||
      record.status == AppointmentRecordStatus.confirmed;

  Future<AppointmentMutationResult> reschedule({
    required AppointmentRecord appointment,
    required DateTime newStart,
    required DateTime newEnd,
  }) {
    if (!newStart.isBefore(newEnd)) {
      throw ArgumentError('New end time must follow new start time.');
    }
    final expectedStart = newStart.toUtc();
    final expectedEnd = newEnd.toUtc();
    return _send(
      appointment: appointment,
      action: 'reschedule',
      body: <String, dynamic>{
        'scheduled_start': expectedStart.toIso8601String(),
        'scheduled_end': expectedEnd.toIso8601String(),
      },
      accepts: (record) =>
          record.status == appointment.status &&
          _instantMatches(record.scheduledStart, expectedStart) &&
          _instantMatches(record.scheduledEnd, expectedEnd),
    );
  }

  Future<AppointmentMutationResult> cancel({
    required AppointmentRecord appointment,
    String? reason,
  }) {
    final String? trimmed;
    if (reason == null) {
      trimmed = null;
    } else {
      trimmed = reason.trim();
      if (trimmed.isEmpty || trimmed.length > 255) {
        throw ArgumentError.value(reason, 'reason', 'Invalid reason.');
      }
    }
    return _send(
      appointment: appointment,
      action: 'cancel',
      body: <String, dynamic>{'cancellation_reason': ?trimmed},
      accepts: (record) =>
          record.status == AppointmentRecordStatus.cancelled &&
          record.cancelledAt != null &&
          (trimmed == null || record.cancellationReason == trimmed),
    );
  }

  /// Status changes use the SAME single-send, revision-bound and uncertain-
  /// outcome lock as reschedule/cancel. No offline queue or idempotency header.
  Future<AppointmentMutationResult> confirm({
    required AppointmentRecord appointment,
  }) => _send(
    appointment: appointment,
    action: 'confirm',
    body: const <String, dynamic>{},
    allowedOriginalStatuses: const <AppointmentRecordStatus>{
      AppointmentRecordStatus.scheduled,
    },
    accepts: (record) => record.status == AppointmentRecordStatus.confirmed,
  );

  Future<AppointmentMutationResult> complete({
    required AppointmentRecord appointment,
    String? notes,
  }) {
    final String? trimmed;
    if (notes == null) {
      trimmed = null;
    } else {
      trimmed = notes.trim();
      if (trimmed.isEmpty || trimmed.length > 2000) {
        throw ArgumentError.value(notes, 'notes', 'Invalid completion notes.');
      }
    }
    return _send(
      appointment: appointment,
      action: 'complete',
      body: <String, dynamic>{'notes': ?trimmed},
      allowedOriginalStatuses: const <AppointmentRecordStatus>{
        AppointmentRecordStatus.confirmed,
      },
      accepts: (record) =>
          record.status == AppointmentRecordStatus.completed &&
          (trimmed == null || record.notes == trimmed),
    );
  }

  Future<AppointmentMutationResult> markNoShow({
    required AppointmentRecord appointment,
  }) => _send(
    appointment: appointment,
    action: 'no-show',
    body: const <String, dynamic>{},
    allowedOriginalStatuses: const <AppointmentRecordStatus>{
      AppointmentRecordStatus.confirmed,
    },
    accepts: (record) => record.status == AppointmentRecordStatus.noShow,
  );

  static bool _instantMatches(String raw, DateTime expected) {
    final parsed = DateTime.tryParse(raw);
    return parsed != null && parsed.isAtSameMomentAs(expected);
  }

  AppointmentMutationResult _uncertain(int id) {
    _needsReconciliation.add(id);
    return const AppointmentMutationResult(
      AppointmentMutationOutcome.reconciliationRequired,
    );
  }

  Future<AppointmentMutationResult> _send({
    required AppointmentRecord appointment,
    required String action,
    required Map<String, dynamic> body,
    required bool Function(AppointmentRecord) accepts,
    Set<AppointmentRecordStatus>? allowedOriginalStatuses,
  }) async {
    final id = appointment.id;
    if (id <= 0 ||
        appointment.clinicId <= 0 ||
        appointment.patientId <= 0 ||
        appointment.staffId <= 0) {
      throw ArgumentError('Appointment identity must be positive.');
    }
    // An uncertain operation always wins over a new action on the same ID.
    // Reopening this route never grants permission for a blind retry.
    if (_needsReconciliation.contains(id)) {
      return const AppointmentMutationResult(
        AppointmentMutationOutcome.reconciliationRequired,
      );
    }
    if (!_eligible(appointment) ||
        (allowedOriginalStatuses != null &&
            !allowedOriginalStatuses.contains(appointment.status))) {
      return const AppointmentMutationResult(
        AppointmentMutationOutcome.rejected,
      );
    }
    if (!_inFlight.add(id)) {
      return const AppointmentMutationResult(
        AppointmentMutationOutcome.alreadyInFlight,
      );
    }

    try {
      final revision = sessionNavigationGuard.revision;
      if (!_isCurrent(revision)) {
        return const AppointmentMutationResult(
          AppointmentMutationOutcome.sessionUnavailable,
        );
      }
      final first = await _session();
      if (!_isCurrent(revision) ||
          first == null ||
          first.clinicContextId != appointment.clinicId) {
        return const AppointmentMutationResult(
          AppointmentMutationOutcome.sessionUnavailable,
        );
      }
      final beforeSend = await _session();
      if (!_isCurrent(revision) ||
          beforeSend == null ||
          !_sameSession(first, beforeSend)) {
        return const AppointmentMutationResult(
          AppointmentMutationOutcome.sessionUnavailable,
        );
      }

      final Map<String, dynamic> envelope;
      try {
        envelope = await apiClient.postJson(
          'api/v1/appointments/$id/$action',
          body: body,
          bearerToken: first.accessToken,
        );
      } on ApiException catch (error) {
        if (error.kind == ApiFailureKind.httpStatus &&
            _definitiveRejectionCodes.contains(error.statusCode)) {
          return const AppointmentMutationResult(
            AppointmentMutationOutcome.rejected,
          );
        }
        return _uncertain(id);
      } on Object {
        return _uncertain(id);
      }

      // Even a 200 must not be trusted after logout, token/clinic rotation,
      // or another session transition. The server action may have happened.
      if (!_isCurrent(revision)) {
        return _uncertain(id);
      }
      final after = await _session();
      if (!_isCurrent(revision) ||
          after == null ||
          !_sameSession(first, after)) {
        return _uncertain(id);
      }

      try {
        if (envelope['success'] != true ||
            envelope['data'] is! Map<String, dynamic>) {
          return _uncertain(id);
        }
        final record = AppointmentRecord.fromJson(
          envelope['data'] as Map<String, dynamic>,
          expectedClinicId: first.clinicContextId!,
          expectedPatientId: appointment.patientId,
        );
        if (record.id != id ||
            record.staffId != appointment.staffId ||
            !accepts(record)) {
          return _uncertain(id);
        }
        return AppointmentMutationResult(
          AppointmentMutationOutcome.confirmed,
          appointment: record,
        );
      } on Object {
        return _uncertain(id);
      }
    } finally {
      _inFlight.remove(id);
    }
  }
}
