import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';

/// In-memory proof that a clinical read still belongs to the same session.
/// No tokens or patient data are persisted, logged, or put into route extras.
typedef ClinicalSafetySessionStamp = ({
  int userId,
  int clinicContextId,
  String accessToken,
  int navigationRevision,
});

class ClinicalSafetySessionScope {
  const ClinicalSafetySessionScope({
    required this.restoreSession,
    required this.navigationGuard,
  });

  final Future<AuthSessionState> Function() restoreSession;
  final SessionNavigationGuard navigationGuard;

  Future<ClinicalSafetySessionStamp> snapshot() async {
    final revision = navigationGuard.revision;
    if (!navigationGuard.canAccessProtectedRoutes) {
      throw const AuthenticatedSessionUnavailableException(
        AuthenticatedSessionAccessFailure.signedOut,
      );
    }

    final AuthSessionState state;
    try {
      state = await restoreSession();
    } on Object {
      throw const AuthenticatedSessionUnavailableException(
        AuthenticatedSessionAccessFailure.recoveryRequired,
      );
    }

    if (!navigationGuard.canAccessProtectedRoutes ||
        navigationGuard.revision != revision) {
      throw const AuthenticatedSessionUnavailableException(
        AuthenticatedSessionAccessFailure.recoveryRequired,
      );
    }

    final session = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.userId <= 0 ||
        session.clinicContextId == null ||
        session.clinicContextId! <= 0 ||
        session.accessToken.isEmpty ||
        session.accessToken.trim() != session.accessToken) {
      throw const AuthenticatedSessionUnavailableException(
        AuthenticatedSessionAccessFailure.recoveryRequired,
      );
    }

    return (
      userId: session.userId,
      clinicContextId: session.clinicContextId!,
      accessToken: session.accessToken,
      navigationRevision: revision,
    );
  }
}
