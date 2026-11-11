import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_remote_data_source.dart';

/// P24F: one current-session sign-out surface. It never claims that changing
/// a push device registration revokes a JWT or that signing out revokes every
/// other session. The existing auth coordinator owns token and replay cleanup.
class SessionSecurityScreen extends StatefulWidget {
  const SessionSecurityScreen({
    super.key,
    required this.loadSession,
    required this.sessionNavigationGuard,
    required this.performSignOut,
    this.deviceManagementConfigured = false,
  });

  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  final Future<AuthLogoutResult> Function() performSignOut;
  final bool deviceManagementConfigured;

  @override
  State<SessionSecurityScreen> createState() => _SessionSecurityScreenState();
}

class _SessionSecurityScreenState extends State<SessionSecurityScreen> {
  int _generation = 0;
  int? _userId;
  int? _clinicContextId;
  int? _sessionRevision;
  String? _role;
  String? _message;
  bool _loading = true;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void dispose() {
    widget.sessionNavigationGuard.removeListener(_sessionChanged);
    _generation++;
    super.dispose();
  }

  void _clear() {
    _userId = null;
    _clinicContextId = null;
    _sessionRevision = null;
    _role = null;
  }

  void _sessionChanged() {
    _generation++;
    if (!mounted) return;
    setState(() {
      _clear();
      _busy = false;
      _loading = false;
      _message = 'Session changed. Sign in again to manage security.';
    });
    if (widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  bool _current(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.sessionNavigationGuard.revision == revision;

  Future<void> _load() async {
    final generation = ++_generation;
    final revision = widget.sessionNavigationGuard.revision;
    if (!mounted) return;
    setState(() {
      _clear();
      _loading = true;
      _message = null;
    });
    try {
      if (!widget.sessionNavigationGuard.canAccessProtectedRoutes) {
        throw StateError('No active navigation session.');
      }
      final restored = await widget.loadSession();
      if (!_current(generation, revision)) return;
      final session = restored.session;
      if (restored.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.role.trim().isEmpty ||
          session.accessToken.isEmpty ||
          session.refreshToken.isEmpty) {
        throw StateError('No authenticated session.');
      }
      setState(() {
        _userId = session.userId;
        _role = session.role.toLowerCase();
        _clinicContextId = session.clinicContextId;
        _sessionRevision = revision;
        _loading = false;
      });
    } on Object {
      if (!_current(generation, revision)) return;
      setState(() {
        _clear();
        _loading = false;
        _message = 'Session could not be verified. Sign in again.';
      });
    }
  }

  Future<bool> _stillSameSession() async {
    final revision = _sessionRevision;
    final userId = _userId;
    final role = _role;
    if (revision == null ||
        userId == null ||
        role == null ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes ||
        widget.sessionNavigationGuard.revision != revision) {
      return false;
    }
    try {
      final current = await widget.loadSession();
      final session = current.session;
      return current.disposition == AuthSessionDisposition.ready &&
          session != null &&
          session.userId == userId &&
          session.role.toLowerCase() == role &&
          session.clinicContextId == _clinicContextId &&
          session.accessToken.isNotEmpty &&
          session.refreshToken.isNotEmpty &&
          widget.sessionNavigationGuard.canAccessProtectedRoutes &&
          widget.sessionNavigationGuard.revision == revision;
    } on Object {
      return false;
    }
  }

  void _unavailable() {
    if (!mounted) return;
    setState(() {
      _clear();
      _busy = false;
      _message = 'Session changed. Sign in again before continuing.';
    });
  }

  Future<void> _signOutWithConfirmation() async {
    if (_busy || _loading || _userId == null) return;
    final generation = ++_generation;
    setState(() => _busy = true);

    if (!await _stillSameSession()) {
      if (generation == _generation) _unavailable();
      return;
    }
    if (!mounted || generation != _generation) return;
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Sign out of this session?'),
        content: const Text(
          'The app will block protected screens, pause queued replay, and '
          'clear this local login. Encrypted queued work and its keys are '
          'retained for its original account and clinic; it will not move '
          'to another account. Server logout may be unconfirmed if the '
          'network fails. This does not revoke sessions on other devices.',
          key: ValueKey('p24f-signout-warning'),
        ),
        actions: [
          TextButton(
            key: const ValueKey('p24f-cancel-signout'),
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('p24f-confirm-signout'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Sign out'),
          ),
        ],
      ),
    );
    if (!mounted || generation != _generation) return;
    if (accepted != true) {
      setState(() => _busy = false);
      return;
    }
    if (!await _stillSameSession()) {
      if (generation == _generation) _unavailable();
      return;
    }
    if (!mounted || generation != _generation) return;

    try {
      // The shared coordinator invalidates navigation before asynchronous
      // cleanup, pauses and drains replay, clears local session, then attempts
      // exactly one best-effort backend logout. It handles remote uncertainty.
      await widget.performSignOut();
      if (!mounted || generation != _generation) return;
      // A real coordinator will have changed the guard and redirected to
      // login. Never show an old account or claim remote revocation here.
      _unavailable();
    } on Object {
      if (!mounted || generation != _generation) return;
      setState(() {
        _clear();
        _busy = false;
        _message = 'Secure sign-out was not confirmed. Recover your session.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final active =
        _userId != null &&
        !_loading &&
        widget.sessionNavigationGuard.canAccessProtectedRoutes &&
        _sessionRevision == widget.sessionNavigationGuard.revision;
    final canManageDevices =
        active &&
        widget.deviceManagementConfigured &&
        DeviceRegistrationRemoteDataSource.allowedRoles.contains(_role);

    return Scaffold(
      key: const ValueKey('p24f-security-screen'),
      appBar: AppBar(title: const Text('Security and sessions')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            const Text(
              'Account security',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 12),
            if (_loading)
              const LinearProgressIndicator(key: ValueKey('p24f-loading')),
            if (_message != null)
              Text(_message!, key: const ValueKey('p24f-session-status')),
            if (active) ...[
              const Text(
                'To reauthenticate on this device, sign out below and '
                'sign in again. A local saved session is not proof that '
                'its server credentials are still valid.',
                key: ValueKey('p24f-reauth-explanation'),
              ),
              const SizedBox(height: 12),
              const Text(
                'Signing out affects this login only. It does not sign '
                'out other devices or guarantee remote revocation when '
                'connectivity is uncertain.',
                key: ValueKey('p24f-revocation-boundary'),
              ),
              const SizedBox(height: 12),
              const Text(
                'Pending offline work is retained encrypted, scoped to '
                'its original user and clinic. Signing out does not delete '
                'it, move it to another account, or authorize its replay.',
                key: ValueKey('p24f-queue-ownership'),
              ),
              const SizedBox(height: 20),
              FilledButton.icon(
                key: const ValueKey('p24f-sign-out'),
                onPressed: _busy ? null : _signOutWithConfirmation,
                icon: const Icon(Icons.logout),
                label: Text(
                  _busy ? 'Checking session…' : 'Sign out and reauthenticate',
                ),
              ),
              if (canManageDevices) ...[
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  key: const ValueKey('p24f-manage-devices'),
                  onPressed: _busy
                      ? null
                      : () => context.push<void>('/devices'),
                  icon: const Icon(Icons.devices_outlined),
                  label: const Text('Manage push devices'),
                ),
                const Text(
                  'Deactivating or deleting a device registration affects '
                  'push targeting, not its active login session.',
                  key: ValueKey('p24f-device-boundary'),
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }
}
