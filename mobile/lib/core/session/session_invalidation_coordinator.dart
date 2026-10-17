import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_scheduler.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';

/// Coordinates fail-closed handling of a definitive authenticated HTTP 401.
///
/// Navigation is invalidated and scheduling is paused synchronously. Local
/// session deletion waits until an active replay run has returned; the callback
/// itself never waits for that run, so a replay-originated 401 cannot deadlock.
/// Offline operations and their encryption keys are intentionally retained.
class SessionInvalidationCoordinator {
  SessionInvalidationCoordinator({
    required AuthSessionCoordinator sessionCoordinator,
    required SessionNavigationGuard sessionNavigationGuard,
    required OperationReplayScheduler replayScheduler,
  }) : this._(sessionCoordinator, sessionNavigationGuard, replayScheduler);

  SessionInvalidationCoordinator._(
    this._sessionCoordinator,
    this._sessionNavigationGuard,
    this._replayScheduler,
  );

  final AuthSessionCoordinator _sessionCoordinator;
  final SessionNavigationGuard _sessionNavigationGuard;
  final OperationReplayScheduler _replayScheduler;

  Future<void>? _invalidationInFlight;

  /// Starts invalidation immediately and returns a coalesced completion future.
  /// A networking callback should call this without awaiting it.
  Future<void> handleDefinitiveAuthenticationRejection() {
    _sessionNavigationGuard.invalidate();
    _replayScheduler.pause();

    final active = _invalidationInFlight;
    if (active != null) return active;

    late final Future<void> operation;
    operation = _finishInvalidation().whenComplete(() {
      if (identical(_invalidationInFlight, operation)) {
        _invalidationInFlight = null;
      }
    });
    _invalidationInFlight = operation;
    return operation;
  }

  Future<void> _finishInvalidation() async {
    try {
      await _replayScheduler.pauseAndWait();
      await _sessionCoordinator
          .invalidateAfterDefinitiveAuthenticationRejection();
      _sessionNavigationGuard.markSignedOut();
    } on Object {
      // Fail closed: retain invalidated navigation and the paused scheduler.
      // A secure-storage failure must remain visible and recoverable in the UI.
    }
  }
}
