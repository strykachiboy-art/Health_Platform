import 'dart:async';

import 'package:flutter/material.dart';

import '../../../core/session/session_navigation_guard.dart';
import '../../auth/data/auth_session_coordinator.dart';
import 'appointment_create_sync_status_panel.dart';

/// Authenticates a direct sync-status URL before exposing local queue metadata.
/// The status panel itself can only read metadata and never authorizes replay.
class AppointmentCreateSyncStatusRoute extends StatefulWidget {
  const AppointmentCreateSyncStatusRoute({
    super.key,
    required this.sessionNavigationGuard,
    required this.loadSession,
    required this.loadPage,
  });

  final SessionNavigationGuard sessionNavigationGuard;
  final Future<AuthSessionState> Function() loadSession;
  final AppointmentCreateSyncPageLoader loadPage;

  @override
  State<AppointmentCreateSyncStatusRoute> createState() =>
      _AppointmentCreateSyncStatusRouteState();
}

class _AppointmentCreateSyncStatusRouteState
    extends State<AppointmentCreateSyncStatusRoute> {
  static const Set<String> _allowedRoles = <String>{
    'admin',
    'doctor',
    'nurse',
    'receptionist',
  };

  int _generation = 0;
  bool _loading = false;
  bool _authorized = false;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard.addListener(_onGuardChanged);
    _validate();
  }

  @override
  void didUpdateWidget(covariant AppointmentCreateSyncStatusRoute oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
          oldWidget.sessionNavigationGuard,
          widget.sessionNavigationGuard,
        ) ||
        oldWidget.loadSession != widget.loadSession ||
        oldWidget.loadPage != widget.loadPage) {
      oldWidget.sessionNavigationGuard.removeListener(_onGuardChanged);
      widget.sessionNavigationGuard.addListener(_onGuardChanged);
      _validate();
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.sessionNavigationGuard.removeListener(_onGuardChanged);
    super.dispose();
  }

  void _onGuardChanged() {
    setState(_validate);
  }

  void _validate() {
    final generation = ++_generation;
    final guard = widget.sessionNavigationGuard;
    final revision = guard.revision;
    _authorized = false;
    _loading = guard.canAccessProtectedRoutes;
    if (!_loading) return;
    unawaited(_load(generation, revision));
  }

  Future<void> _load(int generation, int revision) async {
    AuthSessionState? state;
    try {
      state = await widget.loadSession();
    } on Object {
      // Fail closed; never expose raw auth or storage failures.
    }
    final session = state?.session;
    final valid =
        state?.disposition == AuthSessionDisposition.ready &&
        session != null &&
        session.userId > 0 &&
        session.clinicContextId != null &&
        session.clinicContextId! > 0 &&
        session.accessToken.trim().isNotEmpty &&
        _allowedRoles.contains(session.role.trim().toLowerCase());
    if (!mounted ||
        generation != _generation ||
        widget.sessionNavigationGuard.revision != revision ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      return;
    }
    setState(() {
      _loading = false;
      _authorized = valid;
    });
  }

  @override
  Widget build(BuildContext context) {
    final guard = widget.sessionNavigationGuard;
    return Scaffold(
      appBar: AppBar(title: const Text('Appointment sync status')),
      body: !guard.canAccessProtectedRoutes
          ? const Center(
              child: Text(
                'Appointment sync status is unavailable for this session.',
                key: ValueKey('s05c2-session-denied'),
              ),
            )
          : _loading
          ? const Center(
              child: CircularProgressIndicator(key: ValueKey('s05c2-loading')),
            )
          : !_authorized
          ? const Center(
              child: Text(
                'Appointment sync status is unavailable for this session.',
                key: ValueKey('s05c2-session-denied'),
              ),
            )
          : SingleChildScrollView(
              child: AppointmentCreateSyncStatusPanel(
                sessionNavigationGuard: guard,
                loadPage: widget.loadPage,
              ),
            ),
    );
  }
}
