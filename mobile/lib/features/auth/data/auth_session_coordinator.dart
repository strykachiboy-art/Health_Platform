import '../../../core/networking/api_exception.dart';
import 'auth_remote_data_source.dart';
import 'auth_session_store.dart';
import 'models/auth_token_response.dart';

/// Describes what the app may safely do with the locally stored session.
///
/// `ready` means a structurally valid session is available locally; the
/// backend remains authoritative about expiry and revocation.
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

  /// Intentionally null for recovery-required states so callers cannot use
  /// the old token pair as if it were known to be valid.
  final AuthTokenResponse? session;

  /// Optional failure context. Do not log request credentials or token values.
  final Object? cause;
}

/// Coordinates refresh-token rotation with the secure session record.
///
/// The pending marker is written before network I/O. Only a confirmed refresh
/// token rejection clears the old local session. Network failures, invalid
/// responses, and replacement-session persistence failures require recovery;
/// this class never retries an uncertain refresh request.
class AuthSessionCoordinator {
  factory AuthSessionCoordinator({
    required AuthSessionStore sessionStore,
    required AuthRemoteDataSource remoteDataSource,
  }) => AuthSessionCoordinator._(sessionStore, remoteDataSource);

  AuthSessionCoordinator._(this._sessionStore, this._remoteDataSource);

  final AuthSessionStore _sessionStore;
  final AuthRemoteDataSource _remoteDataSource;

  Future<AuthSessionState>? _refreshInFlight;

  /// Restores local state without doing network I/O.
  ///
  /// A pending refresh is not exposed as a usable session. The caller should
  /// route the user through the app's safe reauthentication/recovery path.
  Future<AuthSessionState> restoreSession() async {
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
  ///
  /// Concurrent callers share one in-flight operation. If a pending marker is
  /// already present (for example, after a process restart), no request is
  /// sent with the old refresh token.
  Future<AuthSessionState> refreshSession() {
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

    // This write must complete before the remote refresh starts. If secure
    // storage fails here, the exception propagates and no request is sent.
    await _sessionStore.save(stored.session, refreshPending: true);

    final AuthTokenResponse replacement;
    try {
      replacement = await _remoteDataSource.refresh(
        refreshToken: stored.session.refreshToken,
      );
    } on ApiException catch (error) {
      if (_isDefinitiveTokenRejection(error)) {
        // The backend explicitly rejected the refresh credential. If local
        // clearing fails, let that storage failure propagate rather than
        // claim the client is signed out.
        await _sessionStore.clear();
        return const AuthSessionState.signedOut();
      }

      return AuthSessionState.recoveryRequired(cause: error);
    } catch (error) {
      // Includes malformed success payloads: the server may already have
      // rotated its token even though the client cannot use the response.
      return AuthSessionState.recoveryRequired(cause: error);
    }

    try {
      // One secure record write replaces both tokens and clears the marker.
      await _sessionStore.save(replacement, refreshPending: false);
    } catch (error) {
      // The existing pending record is the safe recovery signal unless the
      // storage platform completed the write before reporting its failure.
      return AuthSessionState.recoveryRequired(cause: error);
    }

    return AuthSessionState.ready(replacement);
  }

  bool _isDefinitiveTokenRejection(ApiException error) {
    if (error.kind != ApiFailureKind.httpStatus) {
      return false;
    }

    // Flask-JWT-Extended rejects expired/revoked credentials with 401 and
    // malformed/invalid JWTs with 422. Other status codes remain uncertain:
    // an intermediary/server error could occur after rotation took place.
    return error.statusCode == 401 || error.statusCode == 422;
  }
}
