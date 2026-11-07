import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';

/// Context selector for the backend's patient- and staff-scoped appointment
/// lists. It stores no PHI, sends no HTTP, and never infers staff ID from user ID.
class AppointmentEntryScreen extends StatefulWidget {
  const AppointmentEntryScreen({
    super.key,
    required this.loadSession,
    required this.sessionNavigationGuard,
    required this.onPatientSelected,
    required this.onStaffSelected,
    this.onCreateRequested,
    this.onSyncStatusRequested,
  });

  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  final ValueChanged<int> onPatientSelected;
  final ValueChanged<int> onStaffSelected;
  final VoidCallback? onCreateRequested;
  final VoidCallback? onSyncStatusRequested;

  @override
  State<AppointmentEntryScreen> createState() => _AppointmentEntryScreenState();
}

class _AppointmentEntryScreenState extends State<AppointmentEntryScreen> {
  static const _roles = <String>{'admin', 'doctor', 'nurse', 'receptionist'};

  final _patient = TextEditingController();
  final _staff = TextEditingController();
  bool _loading = true;
  bool _authorized = false;
  String? _patientError;
  String? _staffError;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant AppointmentEntryScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
          oldWidget.sessionNavigationGuard,
          widget.sessionNavigationGuard,
        ) ||
        !identical(oldWidget.loadSession, widget.loadSession)) {
      oldWidget.sessionNavigationGuard.removeListener(_sessionChanged);
      widget.sessionNavigationGuard.addListener(_sessionChanged);
      _clearInputs();
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.sessionNavigationGuard.removeListener(_sessionChanged);
    _patient.dispose();
    _staff.dispose();
    super.dispose();
  }

  void _clearInputs() {
    _patient.clear();
    _staff.clear();
    _patientError = null;
    _staffError = null;
  }

  void _sessionChanged() {
    _generation++;
    _clearInputs();
    if (!mounted) return;
    setState(() {
      _authorized = false;
      _loading = false;
    });
    if (widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  Future<void> _load() async {
    final guard = widget.sessionNavigationGuard;
    final generation = ++_generation;
    final revision = guard.revision;
    if (!mounted) return;
    setState(() {
      _loading = true;
      _authorized = false;
    });
    if (!guard.canAccessProtectedRoutes) {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
      return;
    }
    try {
      final state = await widget.loadSession();
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      final session = state.session;
      final allowed =
          state.disposition == AuthSessionDisposition.ready &&
          session != null &&
          session.userId > 0 &&
          session.clinicContextId != null &&
          session.clinicContextId! > 0 &&
          session.accessToken.trim().isNotEmpty &&
          _roles.contains(session.role.trim().toLowerCase());
      setState(() {
        _loading = false;
        _authorized = allowed;
      });
    } on Object {
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _loading = false;
        _authorized = false;
      });
    }
  }

  void _open({required bool patient}) {
    if (!_authorized ||
        _loading ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      return;
    }
    final controller = patient ? _patient : _staff;
    final raw = controller.text;
    final trimmed = raw.trim();
    final id = int.tryParse(trimmed);
    if (trimmed != raw ||
        trimmed.length > 10 ||
        !RegExp(r'^[1-9][0-9]*$').hasMatch(trimmed) ||
        id == null ||
        id > 2147483647) {
      setState(() {
        if (patient) {
          _patientError = 'Enter a valid patient ID.';
        } else {
          _staffError = 'Enter a valid staff ID.';
        }
      });
      return;
    }
    setState(() {
      if (patient) {
        _patientError = null;
      } else {
        _staffError = null;
      }
    });
    if (patient) {
      widget.onPatientSelected(id);
    } else {
      widget.onStaffSelected(id);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Appointments')),
      body: _loading
          ? const Center(
              child: CircularProgressIndicator(
                key: ValueKey('appointments-entry-loading'),
              ),
            )
          : !_authorized
          ? const Center(
              child: Text(
                'Appointment access unavailable for this session.',
                key: ValueKey('appointments-entry-denied'),
              ),
            )
          : ListView(
              key: const ValueKey('appointments-entry-ready'),
              padding: const EdgeInsets.all(20),
              children: [
                Text(
                  'Choose appointment context',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 10),
                const Text(
                  'Appointment history and schedules are scoped to one '
                  'patient or staff member. Select an existing record ID; '
                  'this screen does not load a clinic-wide appointment list.',
                ),
                const SizedBox(height: 24),
                // A visible action is not a server booking. It only opens
                // the existing encrypted offline-queue request form.
                if (widget.onCreateRequested != null) ...[
                  FilledButton(
                    key: const ValueKey('appointments-open-create'),
                    onPressed: () {
                      if (!_authorized ||
                          _loading ||
                          !widget
                              .sessionNavigationGuard
                              .canAccessProtectedRoutes) {
                        return;
                      }
                      widget.onCreateRequested?.call();
                    },
                    child: const Text('Create appointment request'),
                  ),
                  const SizedBox(height: 20),
                ],
                if (widget.onSyncStatusRequested != null) ...[
                  OutlinedButton(
                    key: const ValueKey('appointments-open-sync-status'),
                    onPressed: () {
                      if (!_authorized ||
                          _loading ||
                          !widget
                              .sessionNavigationGuard
                              .canAccessProtectedRoutes) {
                        return;
                      }
                      widget.onSyncStatusRequested?.call();
                    },
                    child: const Text('View local sync status'),
                  ),
                  const SizedBox(height: 12),
                ],
                Text(
                  'Patient appointment history',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                TextField(
                  key: const ValueKey('appointments-patient-id'),
                  controller: _patient,
                  keyboardType: TextInputType.number,
                  decoration: InputDecoration(
                    labelText: 'Patient ID',
                    errorText: _patientError,
                  ),
                ),
                const SizedBox(height: 8),
                FilledButton(
                  key: const ValueKey('appointments-open-patient'),
                  onPressed: () => _open(patient: true),
                  child: const Text('View patient appointments'),
                ),
                const SizedBox(height: 28),
                Text(
                  'Staff appointment schedule',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                TextField(
                  key: const ValueKey('appointments-staff-id'),
                  controller: _staff,
                  keyboardType: TextInputType.number,
                  decoration: InputDecoration(
                    labelText: 'Staff ID',
                    errorText: _staffError,
                  ),
                ),
                const SizedBox(height: 8),
                FilledButton(
                  key: const ValueKey('appointments-open-staff'),
                  onPressed: () => _open(patient: false),
                  child: const Text('View staff schedule'),
                ),
              ],
            ),
    );
  }
}
