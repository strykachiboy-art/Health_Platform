import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/clinic_context_switch_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/authenticated_sign_out_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/profile/data/profile_remote_data_source.dart';

/// Authenticated account surface. Profile reads are never cached persistently.
/// Clinic context mutation remains backend-authoritative and super-admin-only.
class AccountClinicScreen extends StatefulWidget {
  const AccountClinicScreen({
    super.key,
    required this.loadSession,
    required this.profileDataSource,
    required this.authRemoteDataSource,
    required this.clinicContextSwitcher,
    this.signOutCoordinator,
    this.sessionNavigationGuard,
  });

  final Future<AuthSessionState> Function() loadSession;
  final ProfileRemoteDataSource profileDataSource;
  final AuthRemoteDataSource authRemoteDataSource;
  final ClinicContextSwitchCoordinator clinicContextSwitcher;
  final AuthenticatedSignOutCoordinator? signOutCoordinator;
  final SessionNavigationGuard? sessionNavigationGuard;

  @override
  State<AccountClinicScreen> createState() => _AccountClinicScreenState();
}

class _AccountClinicScreenState extends State<AccountClinicScreen> {
  final TextEditingController _clinicId = TextEditingController();
  AuthTokenResponse? _session;
  AuthenticatedProfile? _profile;
  ClinicContextDetails? _contextDetails;
  String? _error;
  bool _loading = true;
  bool _changing = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard?.addListener(_onSessionChanged);
    unawaited(_load());
  }

  @override
  void dispose() {
    widget.sessionNavigationGuard?.removeListener(_onSessionChanged);
    _generation++;
    _clinicId.dispose();
    super.dispose();
  }

  void _onSessionChanged() {
    _generation++;
    if (!mounted) return;
    setState(() {
      _session = null;
      _profile = null;
      _contextDetails = null;
      _loading = false;
      _error = 'Account session changed. Return to sign in.';
    });
    if (widget.sessionNavigationGuard?.canAccessProtectedRoutes == true) {
      unawaited(_load());
    }
  }

  bool _current(int generation, int? guardRevision) {
    final guard = widget.sessionNavigationGuard;
    return mounted &&
        generation == _generation &&
        (guard == null ||
            (guard.canAccessProtectedRoutes &&
                guard.revision == guardRevision));
  }

  Future<void> _load() async {
    final generation = ++_generation;
    final guardRevision = widget.sessionNavigationGuard?.revision;
    if (!mounted) return;
    setState(() {
      _loading = true;
      _session = null;
      _profile = null;
      _contextDetails = null;
      _error = null;
    });
    try {
      if (widget.sessionNavigationGuard?.canAccessProtectedRoutes == false) {
        throw StateError('Session unavailable.');
      }
      final first = await widget.loadSession();
      final session = first.session;
      if (!_current(generation, guardRevision)) return;
      if (first.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0) {
        throw StateError('Session unavailable.');
      }

      AuthenticatedProfile? profile;
      ClinicContextDetails? context;
      if (session.role.trim().toUpperCase() == 'SUPER_ADMIN') {
        // /profile/me excludes super-admin. Use the supported context GET.
        context = await widget.authRemoteDataSource.getClinicContext(
          accessToken: session.accessToken,
        );
        if (context.clinicId != session.clinicContextId) {
          throw const FormatException('Clinic context response mismatch.');
        }
      } else {
        profile = await widget.profileDataSource.fetchMyProfile();
      }

      if (!_current(generation, guardRevision)) return;
      final after = await widget.loadSession();
      if (!_current(generation, guardRevision)) return;
      final current = after.session;
      if (after.disposition != AuthSessionDisposition.ready ||
          current == null ||
          current.userId != session.userId ||
          current.role != session.role ||
          current.clinicContextId != session.clinicContextId ||
          current.accessToken != session.accessToken ||
          current.refreshToken != session.refreshToken) {
        throw StateError('Account session changed during load.');
      }
      setState(() {
        _session = current;
        _profile = profile;
        _contextDetails = context;
        _loading = false;
      });
    } on Object {
      if (!_current(generation, guardRevision)) return;
      setState(() {
        _session = null;
        _profile = null;
        _contextDetails = null;
        _loading = false;
        _error = 'Account details could not be confirmed. Retry online.';
      });
    }
  }

  Future<void> _signOut() async {
    final signOut = widget.signOutCoordinator;
    if (signOut == null || _changing || _loading || _session == null) {
      return;
    }
    setState(() {
      _changing = true;
      _error = null;
      _profile = null;
      _contextDetails = null;
    });
    try {
      await signOut.signOut();
      // Guard status redirects /account to /login; do not reopen this route.
    } on Object {
      if (!mounted) return;
      setState(() {
        _changing = false;
        _session = null;
        _error = 'Secure logout is incomplete. Recover your local session.';
      });
    }
  }

  Future<void> _changeClinic({required bool clear}) async {
    if (_changing ||
        _loading ||
        _session?.role.trim().toUpperCase() != 'SUPER_ADMIN') {
      return;
    }
    int? clinicId;
    if (!clear) {
      final raw = _clinicId.text.trim();
      if (!RegExp(r'^[0-9]{1,18}$').hasMatch(raw)) {
        setState(() => _error = 'Enter a positive clinic ID.');
        return;
      }
      clinicId = int.tryParse(raw);
      if (clinicId == null || clinicId <= 0) {
        setState(() => _error = 'Enter a positive clinic ID.');
        return;
      }
    }
    setState(() {
      _changing = true;
      _error = null;
      _profile = null;
      _contextDetails = null;
    });
    final result = clear
        ? await widget.clinicContextSwitcher.clearClinic()
        : await widget.clinicContextSwitcher.selectClinic(clinicId!);

    // The guard closes this route while the transition is in progress.
    // No stale widget may reopen navigation or render old-clinic state.
    if (!mounted) return;
    if (result.disposition != AuthSessionDisposition.ready) {
      setState(() {
        _changing = false;
        _session = null;
        _error = 'Clinic change could not be confirmed. Secure session recovery is required.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = _session;
    final superAdmin = session?.role.trim().toUpperCase() == 'SUPER_ADMIN';
    return Scaffold(
      appBar: AppBar(title: const Text('Profile and clinic')),
      body: _loading
          ? const Center(
              child: CircularProgressIndicator(
                key: ValueKey('p22-s05-account-loading'),
              ),
            )
          : session == null
          ? Center(
              child: Text(
                _error ?? 'Authenticated account unavailable.',
                key: const ValueKey('p22-s05-account-unavailable'),
              ),
            )
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                Text(
                  'Role: ${session.role}',
                  key: const ValueKey('p22-s05-account-role'),
                ),
                const SizedBox(height: 8),
                Text(
                  'User ID: ${session.userId}',
                  key: const ValueKey('p22-s05-account-user'),
                ),
                const SizedBox(height: 16),
                OutlinedButton.icon(
                  key: const ValueKey('p22-s05-account-sign-out'),
                  onPressed: _changing || widget.signOutCoordinator == null
                      ? null
                      : () => unawaited(_signOut()),
                  icon: const Icon(Icons.logout_outlined),
                  label: const Text('Sign out'),
                ),
                if (!superAdmin) ...[
                  Text(
                    'Email: ${_profile?.email ?? 'Unavailable'}',
                    key: const ValueKey('p22-s05-profile-email'),
                  ),
                  if (_profile?.staffName != null)
                    Text(
                      'Name: ${_profile!.staffName}',
                      key: const ValueKey('p22-s05-profile-name'),
                    ),
                  Text(
                    'Assigned clinic: ${_profile?.assignedClinicName ?? 'Unavailable'}',
                    key: const ValueKey('p22-s05-profile-clinic'),
                  ),
                ] else ...[
                  const Text(
                    'The profile/me endpoint does not authorize super administrators. '
                    'Only verified clinic-context information is shown.',
                    key: ValueKey('p22-s05-profile-superadmin-note'),
                  ),
                  Text(
                    'Selected clinic: ${_contextDetails?.clinicName ?? 'None'}',
                    key: const ValueKey('p22-s05-clinic-context'),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _clinicId,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      labelText: 'Clinic ID',
                      helperText: 'Select an active clinic by its numeric ID.',
                    ),
                    key: const ValueKey('p22-s05-select-clinic-id'),
                  ),
                  const SizedBox(height: 8),
                  FilledButton(
                    key: const ValueKey('p22-s05-select-clinic'),
                    onPressed: _changing
                        ? null
                        : () => unawaited(_changeClinic(clear: false)),
                    child: const Text('Select clinic'),
                  ),
                  OutlinedButton(
                    key: const ValueKey('p22-s05-clear-clinic'),
                    onPressed: _changing
                        ? null
                        : () => unawaited(_changeClinic(clear: true)),
                    child: const Text('Clear clinic context'),
                  ),
                  const Text(
                    'Changing context closes the current clinical workspace '
                    'before new credentials are accepted.',
                    key: ValueKey('p22-s05-switch-warning'),
                  ),
                ],
                if (_error != null)
                  Text(_error!, key: const ValueKey('p22-s05-account-error')),
              ],
            ),
    );
  }
}
