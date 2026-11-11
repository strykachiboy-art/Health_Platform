import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/sync/operation_discard_service.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_worker.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_create_queue_state_read_model.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

/// Only metadata escapes this adapter. It never decrypts a queued payload.
typedef PendingWorkPageLoader = Future<AppointmentCreateQueuePage> Function({
  required int page,
  required int pageSize,
});
typedef PendingWorkReconcileRunner =
    Future<OperationReconciliationResult> Function(String operationId);
typedef PendingWorkDiscardRunner = Future<OperationDiscardResult> Function(
  String operationId, {
  required bool manualNoEffectConfirmed,
});

enum PendingWorkActionOutcome {
  confirmed,
  reviewRequired,
  rejected,
  unconfirmed,
  unavailable,
}

/// A fixed, non-sensitive action result. A status is not an appointment record.
class PendingWorkActionResult {
  const PendingWorkActionResult(this.outcome);
  final PendingWorkActionOutcome outcome;
}

abstract class PendingWorkAccess {
  SessionNavigationGuard get guard;
  Future<AppointmentCreateQueuePage> loadPage({
    int page = 1,
    int pageSize = 20,
  });
  Future<PendingWorkActionResult> reconcile(String operationId);
  Future<PendingWorkActionResult> discard(String operationId);
}

/// Runtime composition of the EXISTING appointment-create queue reader, replay
/// worker reconciliation, and server-confirmed no-effect discard service.
/// The source deliberately has no enqueue, replay, POST retry, or cached PHI.
class PendingWorkSource implements PendingWorkAccess {
  PendingWorkSource({
    required this.guard,
    required this.loadSession,
    required this.readPage,
    required this.reconcileAppointment,
    required this.discardAppointment,
  });

  @override
  final SessionNavigationGuard guard;
  final Future<AuthSessionState> Function() loadSession;
  final PendingWorkPageLoader readPage;
  final PendingWorkReconcileRunner reconcileAppointment;
  final PendingWorkDiscardRunner discardAppointment;

  static const Set<String> _roles = <String>{
    'admin',
    'doctor',
    'nurse',
    'receptionist',
  };

  bool _inFlight = false;
  bool _uncertain = false;
  bool get actionsLocked => _inFlight || _uncertain;

  Future<AuthTokenResponse?> _authorized(int revision) async {
    if (!guard.canAccessProtectedRoutes || guard.revision != revision) {
      return null;
    }
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken != session.accessToken.trim() ||
          session.refreshToken.isEmpty ||
          !_roles.contains(session.role.trim().toLowerCase()) ||
          !guard.canAccessProtectedRoutes ||
          guard.revision != revision) {
        return null;
      }
      return session;
    } on Object {
      return null;
    }
  }

  bool _same(AuthTokenResponse a, AuthTokenResponse? b, int revision) =>
      b != null &&
      guard.canAccessProtectedRoutes &&
      guard.revision == revision &&
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  @override
  Future<AppointmentCreateQueuePage> loadPage({
    int page = 1,
    int pageSize = 20,
  }) async {
    if (page < 1 || page > 10000 || pageSize < 1 || pageSize > 50) {
      throw ArgumentError('Invalid pending-work page.');
    }
    final revision = guard.revision;
    final before = await _authorized(revision);
    if (before == null) {
      return AppointmentCreateQueuePage.unavailable(page: page);
    }
    try {
      final result = await readPage(page: page, pageSize: pageSize);
      final after = await _authorized(revision);
      if (!_same(before, after, revision) ||
          result.page != page ||
          result.items.length > pageSize) {
        return AppointmentCreateQueuePage.unavailable(page: page);
      }
      return result;
    } on Object {
      return AppointmentCreateQueuePage.unavailable(page: page);
    }
  }

  Future<PendingWorkActionResult> _action(
    String operationId,
    Future<PendingWorkActionResult> Function() invoke,
  ) async {
    if (operationId.isEmpty || operationId != operationId.trim()) {
      return const PendingWorkActionResult(PendingWorkActionOutcome.rejected);
    }
    if (actionsLocked) {
      return const PendingWorkActionResult(
        PendingWorkActionOutcome.unavailable,
      );
    }
    final revision = guard.revision;
    final before = await _authorized(revision);
    if (before == null) {
      return const PendingWorkActionResult(
        PendingWorkActionOutcome.unavailable,
      );
    }
    _inFlight = true;
    try {
      // Do not attempt any second network mutation, including on lost response.
      final result = await invoke();
      final after = await _authorized(revision);
      if (!_same(before, after, revision)) {
        _uncertain = true;
        return const PendingWorkActionResult(
          PendingWorkActionOutcome.unconfirmed,
        );
      }
      if (result.outcome == PendingWorkActionOutcome.unconfirmed) {
        _uncertain = true;
      }
      return result;
    } on Object {
      _uncertain = true;
      return const PendingWorkActionResult(
        PendingWorkActionOutcome.unconfirmed,
      );
    } finally {
      _inFlight = false;
    }
  }

  @override
  Future<PendingWorkActionResult> reconcile(
    String operationId,
  ) => _action(operationId, () async {
    final result = await reconcileAppointment(operationId);
    if (result.isAcknowledged) {
      return const PendingWorkActionResult(PendingWorkActionOutcome.confirmed);
    }
    if (result.disposition ==
            OperationReconciliationDisposition
                .acknowledgementPersistenceUncertain ||
        result.disposition == OperationReconciliationDisposition.unresolved) {
      return const PendingWorkActionResult(
        PendingWorkActionOutcome.unconfirmed,
      );
    }
    return const PendingWorkActionResult(
      PendingWorkActionOutcome.reviewRequired,
    );
  });

  @override
  Future<PendingWorkActionResult> discard(
    String operationId,
  ) => _action(operationId, () async {
    // Default false: never allow user-supplied "manual no effect" claims.
    final result = await discardAppointment(
      operationId,
      manualNoEffectConfirmed: false,
    );
    if (result.serverDispositionConfirmed &&
        (result.localRecordRemoved ||
            result.status == OperationDiscardStatus.alreadyAbsentKeyDeleted ||
            result.status ==
                OperationDiscardStatus
                    .alreadyAbsentKeyRetainedForDependencies ||
            result.status ==
                OperationDiscardStatus.alreadyAbsentKeyCleanupPending)) {
      return const PendingWorkActionResult(PendingWorkActionOutcome.confirmed);
    }
    if (result.status == OperationDiscardStatus.outcomeRequiresReconciliation ||
        result.status == OperationDiscardStatus.invalidServerReceipt ||
        result.status == OperationDiscardStatus.localCleanupFailed) {
      return const PendingWorkActionResult(
        PendingWorkActionOutcome.unconfirmed,
      );
    }
    return const PendingWorkActionResult(PendingWorkActionOutcome.rejected);
  });
}
