import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_scheduler.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';

/// App-wide sign-out transaction. It never deletes the durable offline queue
/// or its encryption keys. Remote revocation can remain uncertain.
class AuthenticatedSignOutCoordinator {
  const AuthenticatedSignOutCoordinator({
    required this.sessionCoordinator,
    required this.sessionNavigationGuard,
    required this.replayScheduler,
  });

  final AuthSessionCoordinator sessionCoordinator;
  final SessionNavigationGuard sessionNavigationGuard;
  final OperationReplayScheduler replayScheduler;

  /// Invalidates all protected routes synchronously, before any asynchronous
  /// replay drain, local token deletion or best-effort remote revocation.
  Future<AuthLogoutResult> signOut() async {
    sessionNavigationGuard.invalidate();
    replayScheduler.pause();
    try {
      await replayScheduler.pauseAndWait();
      final result = await sessionCoordinator.logoutSession();
      final after = await sessionCoordinator.restoreSession();
      if (after.disposition != AuthSessionDisposition.signedOut) {
        throw StateError('Local sign-out was not confirmed.');
      }
      sessionNavigationGuard.markSignedOut();
      return result;
    } on Object {
      // No recovery path is allowed to expose old-clinic views or restart
      // replay until secure session cleanup has been completed.
      sessionNavigationGuard.invalidate();
      rethrow;
    }
  }
}
