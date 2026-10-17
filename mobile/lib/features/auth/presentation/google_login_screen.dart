import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

/// Mobile Google login and local session-status screen.
///
/// Google account acquisition, backend verification, and app session
/// persistence remain separate steps. A session is shown as ready only after
/// the backend exchange succeeds and the coordinator saves the token pair.
class GoogleLoginScreen extends StatefulWidget {
  const GoogleLoginScreen({
    super.key,
    required this.acquireGoogleIdToken,
    required this.authRemoteDataSource,
    required this.sessionCoordinator,
    this.sessionNavigationGuard,
    this.onSessionEnding,
    this.onSessionChanged,
  });

  final Future<String?> Function() acquireGoogleIdToken;
  final AuthRemoteDataSource authRemoteDataSource;
  final AuthSessionCoordinator sessionCoordinator;
  final SessionNavigationGuard? sessionNavigationGuard;
  final FutureOr<void> Function()? onSessionEnding;
  final VoidCallback? onSessionChanged;

  @override
  State<GoogleLoginScreen> createState() => _GoogleLoginScreenState();
}

class _GoogleLoginScreenState extends State<GoogleLoginScreen> {
  AuthSessionState? _sessionState;
  bool _restoring = true;
  bool _busy = false;
  String? _errorMessage;
  String? _statusMessage;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard?.addListener(_handleNavigationStatusChanged);
    unawaited(_restoreSession());
  }

  @override
  void dispose() {
    widget.sessionNavigationGuard?.removeListener(
      _handleNavigationStatusChanged,
    );
    super.dispose();
  }

  void _handleNavigationStatusChanged() {
    if (!mounted) return;
    final status = widget.sessionNavigationGuard?.status;
    if (status == SessionNavigationStatus.invalidated) {
      setState(() {
        _sessionState = const AuthSessionState.recoveryRequired();
        _restoring = false;
        _errorMessage = 'Your session is no longer valid. Clear local session data before signing in.';
        _statusMessage = null;
      });
    } else if (status == SessionNavigationStatus.signedOut) {
      setState(() {
        _sessionState = const AuthSessionState.signedOut();
        _restoring = false;
        _errorMessage = null;
        _statusMessage = null;
      });
    }
  }

  void _applyNavigationState(AuthSessionState state) {
    final guard = widget.sessionNavigationGuard;
    if (guard == null) return;
    if (state.disposition == AuthSessionDisposition.ready) {
      guard.markSessionAvailable();
    } else if (state.disposition == AuthSessionDisposition.signedOut) {
      guard.markSignedOut();
    } else {
      guard.invalidate();
    }
  }

  Future<void> _restoreSession() async {
    if (widget.sessionNavigationGuard?.requiresLocalRecovery ?? false) {
      if (!mounted) return;
      setState(() {
        _sessionState = const AuthSessionState.recoveryRequired();
        _restoring = false;
        _errorMessage =
            'Your session requires local recovery before signing in.';
      });
      return;
    }
    try {
      final restored = await widget.sessionCoordinator.restoreSession();
      if (!mounted) return;
      if (widget.sessionNavigationGuard?.requiresLocalRecovery ?? false) {
        setState(() {
          _sessionState = const AuthSessionState.recoveryRequired();
          _restoring = false;
          _errorMessage =
              'Your session requires local recovery before signing in.';
        });
        return;
      }
      _applyNavigationState(restored);
      setState(() {
        _sessionState = restored;
        _restoring = false;
      });
    } catch (_) {
      if (!mounted) return;
      widget.sessionNavigationGuard?.invalidate();
      setState(() {
        _sessionState = const AuthSessionState.recoveryRequired();
        _restoring = false;
        _errorMessage =
            'We could not safely read your saved session. '
            'Clear local session data before signing in.';
      });
    }
  }

  Future<AuthSessionState> _restoreOrRequireRecovery() async {
    if (widget.sessionNavigationGuard?.requiresLocalRecovery ?? false) {
      return const AuthSessionState.recoveryRequired();
    }
    try {
      final restored = await widget.sessionCoordinator.restoreSession();
      // Invalidation may happen while secure storage/session restoration is
      // pending. Never let that stale completion reopen protected navigation.
      if (widget.sessionNavigationGuard?.requiresLocalRecovery ?? false) {
        return const AuthSessionState.recoveryRequired();
      }
      return restored;
    } catch (_) {
      return const AuthSessionState.recoveryRequired();
    }
  }

  Future<void> _signIn() async {
    if (_busy || _restoring) return;

    final navigationGuard = widget.sessionNavigationGuard;
    if (navigationGuard?.requiresLocalRecovery ?? false) return;
    final navigationRevision = navigationGuard?.revision;

    setState(() {
      _busy = true;
      _errorMessage = null;
      _statusMessage = null;
    });

    try {
      final idToken = await widget.acquireGoogleIdToken();

      if (idToken == null) {
        if (!mounted) return;
        setState(() {
          _busy = false;
          _statusMessage = 'Sign-in cancelled.';
        });
        return;
      }

      final tokens = await widget.authRemoteDataSource.loginWithGoogleIdToken(
        idToken: idToken,
      );

      final authenticated = await widget.sessionCoordinator
          .persistAuthenticatedSession(tokens);

      if (navigationGuard != null &&
          navigationGuard.revision != navigationRevision) {
        final currentState =
            navigationGuard.status == SessionNavigationStatus.signedOut
            ? const AuthSessionState.signedOut()
            : const AuthSessionState.recoveryRequired();
        if (!mounted) return;
        setState(() {
          _sessionState = currentState;
          _busy = false;
          _errorMessage =
              currentState.disposition ==
                  AuthSessionDisposition.recoveryRequired
              ? 'Session state changed during sign-in. Clear local session data before continuing.'
              : null;
          _statusMessage = null;
        });
        return;
      }

      navigationGuard?.markSessionAvailable();
      widget.onSessionChanged?.call();

      if (!mounted) return;
      setState(() {
        _sessionState = authenticated;
        _busy = false;
        _statusMessage = 'Signed in securely.';
      });
    } catch (_) {
      final restored = await _restoreOrRequireRecovery();
      _applyNavigationState(restored);
      if (!mounted) return;

      setState(() {
        _sessionState = restored;
        _busy = false;
        _errorMessage =
            'Google sign-in could not be completed. '
            'Check your connection or configuration and try again.';
      });
    }
  }

  Future<void> _signOut() async {
    if (_busy || _restoring) return;

    setState(() {
      _busy = true;
      _errorMessage = null;
      _statusMessage = null;
    });

    // Close protected navigation immediately, before waiting for replay to stop.
    widget.sessionNavigationGuard?.invalidate();

    try {
      await widget.onSessionEnding?.call();
      final result = await widget.sessionCoordinator.logoutSession();
      final restored = await widget.sessionCoordinator.restoreSession();
      _applyNavigationState(restored);

      if (!mounted) return;

      final message = switch (result.remoteStatus) {
        AuthLogoutRemoteStatus.confirmed =>
          'Signed out. The server confirmed logout.',
        AuthLogoutRemoteStatus.uncertain => 'Signed out on this device. Server revocation could not be confirmed.',
        AuthLogoutRemoteStatus.notAttempted =>
          'Local session cleared. Server logout was not attempted.',
      };

      setState(() {
        _sessionState = restored;
        _busy = false;
        _statusMessage = message;
      });
    } catch (_) {
      widget.sessionNavigationGuard?.invalidate();
      if (!mounted) return;
      setState(() {
        _sessionState = const AuthSessionState.recoveryRequired();
        _busy = false;
        _errorMessage =
            'We could not safely clear local session data. '
            'Sign-in remains disabled until storage recovery succeeds.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(title: const Text('Sign in')),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 480),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Icon(
                      Icons.local_hospital_outlined,
                      size: 48,
                      color: colors.primary,
                    ),
                    const SizedBox(height: 16),
                    Text(
                      'Clinic System Pro',
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 24),
                    _buildSessionContent(context),
                    if (_errorMessage != null) ...[
                      const SizedBox(height: 16),
                      Text(
                        _errorMessage!,
                        key: const ValueKey('auth-error-message'),
                        textAlign: TextAlign.center,
                        style: TextStyle(color: colors.error),
                      ),
                    ],
                    if (_statusMessage != null) ...[
                      const SizedBox(height: 16),
                      Text(
                        _statusMessage!,
                        key: const ValueKey('auth-status-message'),
                        textAlign: TextAlign.center,
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildSessionContent(BuildContext context) {
    if (_restoring) {
      return const Column(
        children: [
          CircularProgressIndicator(key: ValueKey('auth-session-loading')),
          SizedBox(height: 12),
          Text('Checking saved session...'),
        ],
      );
    }

    final state = _sessionState;
    if (state == null) {
      return const Text('Session status is unavailable.');
    }

    switch (state.disposition) {
      case AuthSessionDisposition.signedOut:
        return _buildSignedOut();
      case AuthSessionDisposition.ready:
        return _buildReady(state.session!);
      case AuthSessionDisposition.recoveryRequired:
        return _buildRecoveryRequired();
    }
  }

  Widget _buildSignedOut() {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        const Text(
          'Sign in securely to continue.',
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 20),
        SizedBox(
          width: double.infinity,
          child: ElevatedButton.icon(
            key: const ValueKey('google-sign-in-button'),
            onPressed: _busy ? null : _signIn,
            icon: _busy
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.login),
            label: Text(_busy ? 'Signing in...' : 'Continue with Google'),
          ),
        ),
      ],
    );
  }

  Widget _buildReady(AuthTokenResponse session) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        const Icon(Icons.check_circle_outline, size: 40, color: Colors.green),
        const SizedBox(height: 12),
        Text(
          'You are signed in',
          key: const ValueKey('authenticated-session'),
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 8),
        Text('Role: ${session.role}'),
        const SizedBox(height: 20),
        SizedBox(
          width: double.infinity,
          child: ElevatedButton.icon(
            key: const ValueKey('open-patient-directory-button'),
            onPressed: _busy
                ? null
                : () => context.push<void>(
                    '/patients',
                    extra: session.accessToken,
                  ),
            icon: const Icon(Icons.people_outline),
            label: const Text('Open patient directory'),
          ),
        ),
        const SizedBox(height: 12),
        SizedBox(
          width: double.infinity,
          child: OutlinedButton.icon(
            key: const ValueKey('sign-out-button'),
            onPressed: _busy ? null : _signOut,
            icon: _busy
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.logout),
            label: Text(_busy ? 'Signing out...' : 'Sign out'),
          ),
        ),
      ],
    );
  }

  Widget _buildRecoveryRequired() {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        const Icon(Icons.warning_amber_rounded, size: 40, color: Colors.orange),
        const SizedBox(height: 12),
        Text(
          'Session recovery required',
          key: const ValueKey('session-recovery-required'),
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 8),
        const Text(
          'For your security, signing in is disabled until the local session '
          'state has been cleared.',
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 20),
        SizedBox(
          width: double.infinity,
          child: OutlinedButton.icon(
            key: const ValueKey('clear-local-session-button'),
            onPressed: _busy ? null : _signOut,
            icon: _busy
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.cleaning_services_outlined),
            label: Text(_busy ? 'Clearing session...' : 'Clear local session'),
          ),
        ),
      ],
    );
  }
}
