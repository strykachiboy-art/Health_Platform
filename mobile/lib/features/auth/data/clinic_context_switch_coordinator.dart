import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_scheduler.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';

/// Single global transition boundary shared with the app's real session store.
/// It never deletes queued operations or queue encryption keys.
class ClinicContextSwitchCoordinator {
  const ClinicContextSwitchCoordinator({
    required this.sessionCoordinator,
    required this.sessionNavigationGuard,
    required this.replayScheduler,
  });

  final AuthSessionCoordinator sessionCoordinator;
  final SessionNavigationGuard sessionNavigationGuard;
  final OperationReplayScheduler replayScheduler;

  Future<AuthSessionState> selectClinic(int clinicId) {
    if (clinicId <= 0) {
      throw ArgumentError.value(clinicId, 'clinicId', 'Must be positive.');
    }
    return _change(clinicId);
  }

  Future<AuthSessionState> clearClinic() => _change(null);

  Future<AuthSessionState> _change(int? clinicId) async {
    if (!sessionNavigationGuard.canAccessProtectedRoutes) {
      throw StateError('Clinic switching requires an active session.');
    }

    // Unmount protected clinical widgets BEFORE an HTTP request is sent.
    // This also bumps the navigation revision used by in-flight reads.
    sessionNavigationGuard.invalidate();
    final revision = sessionNavigationGuard.revision;
    replayScheduler.pause();
    try {
      await replayScheduler.pauseAndWait();
      final next = await sessionCoordinator.changeClinicContext(
        clinicId: clinicId,
      );
      if (next.disposition != AuthSessionDisposition.ready ||
          next.session == null ||
          sessionNavigationGuard.revision != revision ||
          sessionNavigationGuard.status !=
              SessionNavigationStatus.invalidated) {
        return const AuthSessionState.recoveryRequired();
      }
      sessionNavigationGuard.markSessionAvailable();
      replayScheduler.notifySessionChanged();
      return next;
    } on Object {
      // A timed-out context operation can have succeeded on the server.
      // Never retry automatically, and never re-expose the old clinic.
      return const AuthSessionState.recoveryRequired();
    }
  }
}
