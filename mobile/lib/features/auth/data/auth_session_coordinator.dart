import 'dart:async';

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
  Future<AuthSessionState>? _clinicContextInFlight;
  Future<AuthLogoutResult>? _logoutInFlight;

  bool _logoutRequested = false;
  bool _sessionEstablishmentInFlight = false;
  Completer<void>? _sessionEstablishmentFinished;
  Future<void>? _authenticationRejectionInvalidationInFlight;
  int _sessionTransitionGeneration = 0;

  /// Persists a token pair returned by a successful authentication call.
  ///
  /// An existing session is never silently replaced by another login.
  Future<AuthSessionState> persistAuthenticatedSession(
    AuthTokenResponse session,
  ) async {
    if (_logoutRequested ||
        _logoutInFlight != null ||
        _refreshInFlight != null ||
        _clinicContextInFlight != null ||
        _sessionEstablishmentInFlight) {
      throw StateError(
        'Cannot establish a session during another session operation.',
      );
    }

    _sessionEstablishmentInFlight = true;
    final establishmentFinished = Completer<void>();
    _sessionEstablishmentFinished = establishmentFinished;
    _sessionTransitionGeneration++;
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
          _refreshInFlight != null ||
          _clinicContextInFlight != null) {
        throw StateError('Session state changed during authentication.');
      }

      await _sessionStore.save(session);
      return AuthSessionState.ready(session);
    } finally {
      _sessionEstablishmentInFlight = false;
      if (identical(_sessionEstablishmentFinished, establishmentFinished)) {
        _sessionEstablishmentFinished = null;
      }
      if (!establishmentFinished.isCompleted) {
        establishmentFinished.complete();
      }
    }
  }

  /// Restores local state without doing network I/O.
  Future<AuthSessionState> restoreSession() async {
    if (_logoutRequested ||
        _logoutInFlight != null ||
        _refreshInFlight != null ||
        _clinicContextInFlight != null ||
        _sessionEstablishmentInFlight) {
      return AuthSessionState.recoveryRequired(
        cause: StateError('A session transition is in progress.'),
      );
    }

    final observedGeneration = _sessionTransitionGeneration;
    final stored = await _sessionStore.readRecord();

    if (_logoutRequested ||
        _refreshInFlight != null ||
        _clinicContextInFlight != null ||
        _sessionEstablishmentInFlight ||
        observedGeneration != _sessionTransitionGeneration) {
      return AuthSessionState.recoveryRequired(
        cause: StateError('Session state changed during restoration.'),
      );
    }

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
    if (_logoutRequested ||
        _logoutInFlight != null ||
        _clinicContextInFlight != null ||
        _sessionEstablishmentInFlight) {
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

    _sessionTransitionGeneration++;
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

    if (replacement.userId != stored.session.userId ||
        replacement.clinicContextId != stored.session.clinicContextId) {
      final contextChange = StateError(
        'Refreshed session identity or clinic context changed.',
      );
      try {
        await _sessionStore.clear();
      } catch (error) {
        return AuthSessionState.recoveryRequired(cause: error);
      }
      return AuthSessionState.recoveryRequired(cause: contextChange);
    }

    try {
      await _sessionStore.save(replacement, refreshPending: false);
    } catch (error) {
      return AuthSessionState.recoveryRequired(cause: error);
    }

    return AuthSessionState.ready(replacement);
  }

  /// Rotates both credentials for a backend-authorized super-admin clinic
  /// transition. The old session is marked pending BEFORE the request.
  /// A connection failure or uncertain response never restores it.
  Future<AuthSessionState> changeClinicContext({int? clinicId}) {
    if (clinicId != null && clinicId <= 0) {
      throw ArgumentError.value(clinicId, 'clinicId', 'Must be positive.');
    }
    if (_logoutRequested ||
        _logoutInFlight != null ||
        _refreshInFlight != null ||
        _clinicContextInFlight != null ||
        _sessionEstablishmentInFlight) {
      return Future.value(
        AuthSessionState.recoveryRequired(
          cause: StateError('Another session transition is active.'),
        ),
      );
    }

    final generation = ++_sessionTransitionGeneration;
    late final Future<AuthSessionState> operation;
    operation = _changeStoredClinicContext(clinicId, generation).whenComplete(
      () {
        if (identical(_clinicContextInFlight, operation)) {
          _clinicContextInFlight = null;
        }
      },
    );
    _clinicContextInFlight = operation;
    return operation;
  }

  Future<AuthSessionState> _changeStoredClinicContext(
    int? clinicId,
    int generation,
  ) async {
    final StoredAuthSession? stored;
    try {
      stored = await _sessionStore.readRecord();
    } on Object catch (error) {
      return AuthSessionState.recoveryRequired(cause: error);
    }
    final previous = stored?.session;
    if (stored == null ||
        stored.refreshPending ||
        previous == null ||
        previous.role.trim().toUpperCase() != 'SUPER_ADMIN') {
      return const AuthSessionState.recoveryRequired();
    }
    if (_logoutRequested || generation != _sessionTransitionGeneration) {
      return const AuthSessionState.recoveryRequired();
    }

    try {
      await _sessionStore.save(previous, refreshPending: true);
    } on Object catch (error) {
      return AuthSessionState.recoveryRequired(cause: error);
    }

    final AuthTokenResponse replacement;
    try {
      replacement = clinicId == null
          ? await _remoteDataSource.clearClinicContext(
              accessToken: previous.accessToken,
            )
          : await _remoteDataSource.selectClinicContext(
              accessToken: previous.accessToken,
              clinicId: clinicId,
            );
    } on Object catch (error) {
      return AuthSessionState.recoveryRequired(cause: error);
    }

    if (_logoutRequested ||
        generation != _sessionTransitionGeneration ||
        replacement.userId != previous.userId ||
        replacement.role.trim().toUpperCase() != 'SUPER_ADMIN' ||
        replacement.clinicContextId != clinicId ||
        replacement.accessToken == previous.accessToken ||
        replacement.refreshToken == previous.refreshToken) {
      return const AuthSessionState.recoveryRequired();
    }

    try {
      await _sessionStore.save(replacement, refreshPending: false);
    } on Object catch (error) {
      return AuthSessionState.recoveryRequired(cause: error);
    }
    if (_logoutRequested || generation != _sessionTransitionGeneration) {
      return const AuthSessionState.recoveryRequired();
    }
    return AuthSessionState.ready(replacement);
  }

  /// Clears only the saved session after a definitive authenticated HTTP 401.
  /// This deliberately makes no remote logout request and never deletes
  /// queued operations or their context encryption keys.
  Future<void> invalidateAfterDefinitiveAuthenticationRejection() {
    final active = _authenticationRejectionInvalidationInFlight;
    if (active != null) return active;

    late final Future<void> operation;
    operation = _clearRejectedSessionLocally().whenComplete(() {
      if (identical(_authenticationRejectionInvalidationInFlight, operation)) {
        _authenticationRejectionInvalidationInFlight = null;
      }
    });
    _authenticationRejectionInvalidationInFlight = operation;
    return operation;
  }

  Future<void> _clearRejectedSessionLocally() async {
    _logoutRequested = true;
    _sessionTransitionGeneration++;
    try {
      final establishment = _sessionEstablishmentFinished;
      if (establishment != null) await establishment.future;

      final activeLogout = _logoutInFlight;
      if (activeLogout != null) {
        try {
          await activeLogout;
        } on Object {
          // Local clearing remains required if remote logout was uncertain.
        }
        _logoutRequested = true;
      }

      final activeRefresh = _refreshInFlight;
      if (activeRefresh != null) {
        try {
          await activeRefresh;
        } on Object {
          // Clear the latest stored record even if refresh did not complete.
        }
        _logoutRequested = true;
      }

      final clinicChange = _clinicContextInFlight;
      if (clinicChange != null) {
        try {
          await clinicChange;
        } on Object {
          // Wait for a pending secure-store transition before clearing it.
        }
        _logoutRequested = true;
      }

      await _sessionStore.clear();
    } finally {
      _logoutRequested = false;
    }
  }

  /// Signs out locally first, then makes one best-effort remote logout call.
  Future<AuthLogoutResult> logoutSession() {
    if (_clinicContextInFlight != null) {
      throw StateError('Cannot log out during a clinic-context transition.');
    }
    if (_sessionEstablishmentInFlight) {
      throw StateError(
        'Cannot log out while a new session is being persisted.',
      );
    }

    final active = _logoutInFlight;
    if (active != null) {
      return active;
    }
    if (_logoutRequested &&
        _authenticationRejectionInvalidationInFlight != null) {
      throw StateError('Cannot log out during another session transition.');
    }

    _logoutRequested = true;
    _sessionTransitionGeneration++;

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
