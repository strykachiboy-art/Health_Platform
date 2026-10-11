import '../../../core/networking/api_exception.dart';
import 'auth_remote_data_source.dart';
import 'auth_session_store.dart';
import 'models/auth_token_response.dart';

/// Describes what the app may safely do with the locally stored session.
enum AuthSessionDisposition { signedOut, ready, recoveryRequired }

class AuthSessionState {
  const AuthSessionState.signedOut()
    : disposition = AuthSessionDisposition.signedOut,
      session = null,
      cause = null;

  const AuthSessionState.ready(AuthTokenResponse value)
    : disposition = AuthSessionDisposition.ready,
      session = value,
      cause = null;

  const AuthSessionState.recoveryRequired({this.cause})
    : disposition = AuthSessionDisposition.recoveryRequired,
      session = null;

  final AuthSessionDisposition disposition;
  final AuthTokenResponse? session;
  final Object? cause;
}

enum AuthLogoutRemoteStatus { notAttempted, confirmed, uncertain }

/// Returned only after the local session record is absent.
class AuthLogoutResult {
  const AuthLogoutResult({required this.remoteStatus, this.cause});

  final AuthLogoutRemoteStatus remoteStatus;
  final Object? cause;
}

/// Coordinates refresh-token rotation with the secure session record.
class AuthSessionCoordinator {
  factory AuthSessionCoordinator({
    required AuthSessionStore sessionStore,
    required AuthRemoteDataSource remoteDataSource,
  }) => AuthSessionCoordinator._(sessionStore, remoteDataSource);

  AuthSessionCoordinator._(this._sessionStore, this._remoteDataSource);

  final AuthSessionStore _sessionStore;
  final AuthRemoteDataSource _remoteDataSource;

  Future<AuthSessionState>? _refreshInFlight;
  Future<AuthLogoutResult>? _logoutInFlight;

  bool _logoutRequested = false;
  bool _sessionEstablishmentInFlight = false;

  /// Persists a token pair returned by a successful authentication call.
  ///
  /// An existing session is never silently replaced by another login.
  Future<AuthSessionState> persistAuthenticatedSession(
    AuthTokenResponse session,
  ) async {
    if (_logoutRequested ||
        _logoutInFlight != null ||
        _refreshInFlight != null ||
        _sessionEstablishmentInFlight) {
      throw StateError(
        'Cannot establish a session during another session operation.',
      );
    }

    _sessionEstablishmentInFlight = true;
    try {
      final stored = await _sessionStore.readRecord();
      if (stored != null) {
        throw StateError(
          'An authentication session already exists. '
          'Sign out before switching accounts.',
        );
      }

      if (_logoutRequested ||
          _logoutInFlight != null ||
          _refreshInFlight != null) {
        throw StateError('Session state changed during authentication.');
      }

      await _sessionStore.save(session);
      return AuthSessionState.ready(session);
    } finally {
      _sessionEstablishmentInFlight = false;
    }
  }

  /// Restores local state without doing network I/O.
  Future<AuthSessionState> restoreSession() async {
    if (_logoutRequested) {
      return AuthSessionState.recoveryRequired(
        cause: StateError('Logout is in progress or local cleanup failed.'),
      );
    }

    final stored = await _sessionStore.readRecord();

    if (stored == null) {
      return const AuthSessionState.signedOut();
    }

    if (stored.refreshPending) {
      return const AuthSessionState.recoveryRequired();
    }

    return AuthSessionState.ready(stored.session);
  }

  /// Rotates the current refresh token once and persists the replacement pair.
  Future<AuthSessionState> refreshSession() {
    if (_logoutRequested || _sessionEstablishmentInFlight) {
      return Future.value(
        AuthSessionState.recoveryRequired(
          cause: StateError('Refresh blocked during a session transition.'),
        ),
      );
    }

    final active = _refreshInFlight;
    if (active != null) {
      return active;
    }

    late final Future<AuthSessionState> operation;
    operation = _refreshStoredSession().whenComplete(() {
      if (identical(_refreshInFlight, operation)) {
        _refreshInFlight = null;
      }
    });
    _refreshInFlight = operation;
    return operation;
  }

  Future<AuthSessionState> _refreshStoredSession() async {
    final stored = await _sessionStore.readRecord();

    if (stored == null) {
      return const AuthSessionState.signedOut();
    }

    if (stored.refreshPending) {
      return const AuthSessionState.recoveryRequired();
    }

    await _sessionStore.save(stored.session, refreshPending: true);

    final AuthTokenResponse replacement;
    try {
      replacement = await _remoteDataSource.refresh(
        refreshToken: stored.session.refreshToken,
      );
    } on ApiException catch (error) {
      if (_isDefinitiveTokenRejection(error)) {
        await _sessionStore.clear();
        return const AuthSessionState.signedOut();
      }

      return AuthSessionState.recoveryRequired(cause: error);
    } catch (error) {
      return AuthSessionState.recoveryRequired(cause: error);
    }

    try {
      await _sessionStore.save(replacement, refreshPending: false);
    } catch (error) {
      return AuthSessionState.recoveryRequired(cause: error);
    }

    return AuthSessionState.ready(replacement);
  }

  /// Signs out locally first, then makes one best-effort remote logout call.
  Future<AuthLogoutResult> logoutSession() {
    if (_sessionEstablishmentInFlight) {
      throw StateError(
        'Cannot log out while a new session is being persisted.',
      );
    }

    final active = _logoutInFlight;
    if (active != null) {
      return active;
    }

    _logoutRequested = true;

    late final Future<AuthLogoutResult> operation;
    operation = _logoutStoredSession()
        .then((result) {
          _logoutRequested = false;
          return result;
        })
        .whenComplete(() {
          if (identical(_logoutInFlight, operation)) {
            _logoutInFlight = null;
          }
        });

    _logoutInFlight = operation;
    return operation;
  }

  Future<AuthLogoutResult> _logoutStoredSession() async {
    final activeRefresh = _refreshInFlight;
    StoredAuthSession? beforeRefresh;
    AuthSessionState? refreshState;
    Object? refreshError;

    if (activeRefresh != null) {
      try {
        beforeRefresh = await _sessionStore.readRecord();
      } catch (_) {}

      try {
        refreshState = await activeRefresh;
      } catch (error) {
        refreshError = error;
      }
    }

    StoredAuthSession? stored;
    Object? readError;
    try {
      stored = await _sessionStore.readRecord();
    } catch (error) {
      readError = error;
    }

    if (readError != null) {
      await _sessionStore.clear();
      return AuthLogoutResult(
        remoteStatus: AuthLogoutRemoteStatus.notAttempted,
        cause: readError,
      );
    }

    if (stored == null) {
      if (activeRefresh == null) {
        return const AuthLogoutResult(
          remoteStatus: AuthLogoutRemoteStatus.notAttempted,
        );
      }

      if (refreshState?.disposition == AuthSessionDisposition.signedOut) {
        if (beforeRefresh == null) {
          return const AuthLogoutResult(
            remoteStatus: AuthLogoutRemoteStatus.notAttempted,
          );
        }

        return _attemptRemoteLogout(beforeRefresh.session);
      }

      return AuthLogoutResult(
        remoteStatus: AuthLogoutRemoteStatus.uncertain,
        cause:
            refreshError ??
            StateError(
              'Session state changed during refresh; remote logout was not attempted.',
            ),
      );
    }

    final refreshPending = stored.refreshPending;

    await _sessionStore.clear();

    if (refreshPending) {
      return AuthLogoutResult(
        remoteStatus: AuthLogoutRemoteStatus.notAttempted,
        cause: StateError(
          'Refresh outcome is uncertain; local session was cleared without '
          'sending a potentially stale token pair to logout.',
        ),
      );
    }

    return _attemptRemoteLogout(stored.session);
  }

  Future<AuthLogoutResult> _attemptRemoteLogout(
    AuthTokenResponse session,
  ) async {
    try {
      await _remoteDataSource.logout(
        accessToken: session.accessToken,
        refreshToken: session.refreshToken,
      );

      return const AuthLogoutResult(
        remoteStatus: AuthLogoutRemoteStatus.confirmed,
      );
    } catch (error) {
      return AuthLogoutResult(
        remoteStatus: AuthLogoutRemoteStatus.uncertain,
        cause: error,
      );
    }
  }

  bool _isDefinitiveTokenRejection(ApiException error) {
    if (error.kind != ApiFailureKind.httpStatus) {
      return false;
    }

    return error.statusCode == 401 || error.statusCode == 422;
  }
}
