import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';

/// Password-reauthenticated enrollment for the existing Super Admin account.
/// This widget does not own session persistence or implement offline replay.
class SuperAdminMfaEnrollmentScreen extends StatefulWidget {
  const SuperAdminMfaEnrollmentScreen({
    super.key,
    required this.remote,
    required this.loadSession,
    required this.guard,
    required this.onEnrollmentConfirmed,
  });

  final AuthRemoteDataSource remote;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard guard;
  final Future<void> Function() onEnrollmentConfirmed;

  @override
  State<SuperAdminMfaEnrollmentScreen> createState() =>
      _SuperAdminMfaEnrollmentScreenState();
}

class _SuperAdminMfaEnrollmentScreenState
    extends State<SuperAdminMfaEnrollmentScreen> {
  final _password = TextEditingController();
  final _code = TextEditingController();
  int _revision = 0;
  String? _bearer;
  String? _secret;
  String? _message;
  bool _loading = true;
  bool _busy = false;
  bool _enabled = false;
  bool _enrolled = false;

  @override
  void initState() {
    super.initState();
    _revision = widget.guard.revision;
    widget.guard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void dispose() {
    widget.guard.removeListener(_sessionChanged);
    _bearer = null;
    _secret = null;
    _password.dispose();
    _code.dispose();
    super.dispose();
  }

  void _sessionChanged() {
    if (!mounted) return;
    if (widget.guard.revision != _revision ||
        !widget.guard.canAccessProtectedRoutes) {
      setState(() {
        _bearer = null;
        _secret = null;
        _message = 'Session changed. Sign in again.';
        _enabled = false;
      });
    }
  }

  bool get _valid =>
      mounted &&
      widget.guard.canAccessProtectedRoutes &&
      widget.guard.revision == _revision &&
      _bearer != null;

  Future<void> _load() async {
    try {
      if (!widget.guard.canAccessProtectedRoutes) {
        throw StateError('No authenticated session');
      }
      final state = await widget.loadSession();
      final session = state.session;
      if (!mounted || widget.guard.revision != _revision) return;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.role.toUpperCase() != 'SUPER_ADMIN') {
        throw StateError('Super Admin session required');
      }
      _bearer = session.accessToken;
      final status = await widget.remote.getMfaEnrollmentStatus(
        accessToken: session.accessToken,
      );
      if (!_valid) return;
      setState(() {
        _enabled = status.enrollmentEnabled;
        _enrolled = status.enrolled;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _message = 'MFA enrollment is unavailable for this session.';
      });
    }
  }

  Future<void> _begin() async {
    if (_busy || !_valid || !_enabled || _enrolled || _secret != null) return;
    final password = _password.text;
    if (password.isEmpty) {
      setState(() => _message = 'Enter your current account password.');
      return;
    }
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      final setup = await widget.remote.beginMfaEnrollment(
        accessToken: _bearer!,
        currentPassword: password,
      );
      if (!_valid) return;
      setState(() => _secret = setup);
    } catch (_) {
      if (mounted) {
        setState(
          () => _message = 'Enrollment could not start. Verify your password and connection.',
        );
      }
    } finally {
      _password.clear();
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _confirm() async {
    if (_busy || !_valid || _secret == null) return;
    final code = _code.text.trim();
    if (!RegExp(r'^[0-9]{6}$').hasMatch(code)) {
      setState(() => _message = 'Enter a six-digit authenticator code.');
      return;
    }
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      final recoveryCodes = await widget.remote.confirmMfaEnrollment(
        accessToken: _bearer!,
        code: code,
      );
      _secret = null;
      if (!mounted) return;
      // The backend has already revoked old JWTs. Show recovery codes once,
      // then use the existing sign-out coordinator to clear local credentials.
      await showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (dialogContext) => PopScope(
          canPop: false,
          child: AlertDialog(
            title: const Text('Save your recovery codes now'),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'These codes are shown only once. Store them in a '
                    'secure location, separate from this device.',
                  ),
                  const SizedBox(height: 12),
                  for (final value in recoveryCodes)
                    SelectableText(value, textAlign: TextAlign.center),
                ],
              ),
            ),
            actions: [
              FilledButton(
                key: const ValueKey('g05-mfa-saved-codes'),
                onPressed: () => Navigator.of(dialogContext).pop(),
                child: const Text('I have saved these codes'),
              ),
            ],
          ),
        ),
      );
      if (!mounted) return;
      try {
        await widget.onEnrollmentConfirmed();
      } finally {
        if (mounted) context.go('/login');
      }
    } catch (_) {
      if (mounted) {
        setState(
          () => _message =
              'Verification could not be confirmed. Check your account security '
              'before requesting a new enrollment.',
        );
      }
    } finally {
      _code.clear();
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Super Admin authenticator')),
    body: SafeArea(
      child: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          if (_loading) const LinearProgressIndicator(),
          if (_message != null)
            Text(_message!, key: const ValueKey('g05-mfa-status')),
          if (!_loading && !_enabled)
            const Text('Authenticator enrollment is not available yet.'),
          if (_enabled && _enrolled)
            const Text(
              'An authenticator is already enrolled. Changes require '
              'a separately verified recovery/rotation process.',
            ),
          if (_enabled && !_enrolled && _secret == null) ...[
            const Text(
              'Confirm your current password to enroll an '
              'authenticator. Google-only Super Admin accounts require an '
              'approved reauthentication method before enrollment.',
            ),
            const SizedBox(height: 16),
            TextField(
              key: const ValueKey('g05-mfa-current-password'),
              controller: _password,
              enabled: !_busy,
              obscureText: true,
              enableSuggestions: false,
              autocorrect: false,
              decoration: const InputDecoration(labelText: 'Current password'),
            ),
            const SizedBox(height: 12),
            FilledButton(
              key: const ValueKey('g05-mfa-begin'),
              onPressed: _busy ? null : _begin,
              child: const Text('Begin authenticator setup'),
            ),
          ],
          if (_secret != null) ...[
            const Text(
              'Enter this setup key into your authenticator app. '
              'Do not store it in messages or screenshots.',
            ),
            const SizedBox(height: 12),
            SelectableText(_secret!, key: const ValueKey('g05-mfa-setup-key')),
            const SizedBox(height: 12),
            TextField(
              key: const ValueKey('g05-mfa-confirm-code'),
              controller: _code,
              maxLength: 6,
              enabled: !_busy,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: 'Six-digit code'),
            ),
            FilledButton(
              key: const ValueKey('g05-mfa-confirm'),
              onPressed: _busy ? null : _confirm,
              child: const Text('Confirm authenticator'),
            ),
          ],
        ],
      ),
    ),
  );
}
