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

  /// Hospital members use the backend's GAP-B membership APIs, not the
  /// legacy super-admin-only clinic-context endpoint.
  Future<AuthSessionState> selectTenant(String organizationId) {
    if (!RegExp(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')
        .hasMatch(organizationId)) {
      throw ArgumentError('Organization identifier must be a valid UUID.');
    }
    return _changeMember(tenantId: organizationId);
  }

  Future<AuthSessionState> selectMemberClinic(int clinicId) {
    if (clinicId <= 0) {
      throw ArgumentError('Clinic identifier must be positive.');
    }
    return _changeMember(clinicId: clinicId);
  }

  Future<AuthSessionState> clearTenant() => _changeMember(clear: true);

  Future<AuthSessionState> _changeMember({
    String? tenantId,
    int? clinicId,
    bool clear = false,
  }) async {
    if (!sessionNavigationGuard.canAccessProtectedRoutes) {
      throw StateError(
        'A ready session is required for organization switching.',
      );
    }
    sessionNavigationGuard.invalidate();
    final revision = sessionNavigationGuard.revision;
    replayScheduler.pause();
    try {
      await replayScheduler.pauseAndWait();
      final next = await sessionCoordinator.changeTenantContext(
        tenantId: tenantId,
        clinicId: clinicId,
        clear: clear,
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
      // An unknown outcome must not restore or retry stale tokens.
      return const AuthSessionState.recoveryRequired();
    }
  }

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
