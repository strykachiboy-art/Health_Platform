// ignore_for_file: prefer_initializing_formals

import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';

import 'auth_session_coordinator.dart';

class CoordinatorAuthenticatedSessionAccess {
  const CoordinatorAuthenticatedSessionAccess({
    required AuthSessionCoordinator sessionCoordinator,
  }) : _sessionCoordinator = sessionCoordinator;

  final AuthSessionCoordinator _sessionCoordinator;

  Future<String> requireAccessToken() async {
    final AuthSessionState state;

    try {
      state = await _sessionCoordinator.restoreSession();
    } catch (_) {
      throw const AuthenticatedSessionUnavailableException(
        AuthenticatedSessionAccessFailure.recoveryRequired,
      );
    }

    if (state.disposition == AuthSessionDisposition.signedOut) {
      throw const AuthenticatedSessionUnavailableException(
        AuthenticatedSessionAccessFailure.signedOut,
      );
    }

    final session = state.session;

    if (state.disposition != AuthSessionDisposition.ready || session == null) {
      throw const AuthenticatedSessionUnavailableException(
        AuthenticatedSessionAccessFailure.recoveryRequired,
      );
    }

    final accessToken = session.accessToken;

    if (accessToken.isEmpty || accessToken.trim() != accessToken) {
      throw const AuthenticatedSessionUnavailableException(
        AuthenticatedSessionAccessFailure.recoveryRequired,
      );
    }

    return accessToken;
  }
}
