import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/sync/appointment_create_queue_producer.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';

/// Testable seam for the existing encrypted appointment-create producer.
/// Production wiring MUST use AppointmentCreateQueueScope.producer.enqueue.
typedef AppointmentCreateEnqueue =
    Future<AppointmentCreateEnqueueResult> Function(
      AppointmentCreateCommand command,
    );

/// Collects one logical appointment-create intent. It does not book directly,
/// infer a staff ID from a user ID, or persist free-text clinical PHI.
/// Queued and acknowledged are distinct states; this screen never claims that
/// a queued request is an appointment confirmed by the backend.
class AppointmentCreateScreen extends StatefulWidget {
  const AppointmentCreateScreen({
    super.key,
    required this.loadSession,
    required this.sessionNavigationGuard,
    required this.enqueue,
    this.initialPatientId,
    this.initialStaffId,
    this.initialStart,
    this.initialEnd,
  });

  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  final AppointmentCreateEnqueue enqueue;
  final int? initialPatientId;
  final int? initialStaffId;
  final DateTime? initialStart;
  final DateTime? initialEnd;

  @override
  State<AppointmentCreateScreen> createState() =>
      _AppointmentCreateScreenState();
}

class _AppointmentCreateScreenState extends State<AppointmentCreateScreen> {
  static const _roles = <String>{'admin', 'doctor', 'nurse', 'receptionist'};
  static const _types = <String>{
    'in_person',
    'telemedicine',
    'follow_up',
    'emergency',
  };

  late final TextEditingController _patient;
  late final TextEditingController _staff;
  DateTime? _start;
  DateTime? _end;
  String _type = 'in_person';
  String? _scope;
  bool _loading = true;
  bool _sending = false;
  bool _authorized = false;
  bool _queued = false;
  bool _conflict = false;
  String? _message;
  AppointmentCreateCommand? _command;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    _patient = TextEditingController(
      text: widget.initialPatientId?.toString() ?? '',
    );
    _staff = TextEditingController(
      text: widget.initialStaffId?.toString() ?? '',
    );
    _start = widget.initialStart;
    _end = widget.initialEnd;
    widget.sessionNavigationGuard.addListener(_onGuardChanged);
    unawaited(_authorize());
  }

  @override
  void didUpdateWidget(covariant AppointmentCreateScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
          oldWidget.sessionNavigationGuard,
          widget.sessionNavigationGuard,
        ) ||
        !identical(oldWidget.loadSession, widget.loadSession) ||
        oldWidget.initialPatientId != widget.initialPatientId ||
        oldWidget.initialStaffId != widget.initialStaffId) {
      oldWidget.sessionNavigationGuard.removeListener(_onGuardChanged);
      widget.sessionNavigationGuard.addListener(_onGuardChanged);
      _invalidateLocalState();
      unawaited(_authorize());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.sessionNavigationGuard.removeListener(_onGuardChanged);
    _patient.dispose();
    _staff.dispose();
    super.dispose();
  }

  void _invalidateLocalState() {
    _generation++;
    _patient.clear();
    _staff.clear();
    _start = null;
    _end = null;
    _type = 'in_person';
    _command = null;
    _scope = null;
    _queued = false;
    _conflict = false;
    _message = null;
    _sending = false;
    _authorized = false;
    _loading = false;
  }

  void _onGuardChanged() {
    _invalidateLocalState();
    if (!mounted) {
      return;
    }
    setState(() {});
    if (widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_authorize());
    }
  }

  static String? _validScope(AuthSessionState state) {
    final session = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.userId <= 0 ||
        session.clinicContextId == null ||
        session.clinicContextId! <= 0 ||
        session.accessToken.trim().isEmpty ||
        !_roles.contains(session.role.trim().toLowerCase())) {
      return null;
    }
    return '${session.userId}:${session.clinicContextId}:${session.role.trim().toLowerCase()}';
  }

  Future<void> _authorize() async {
    final guard = widget.sessionNavigationGuard;
    final generation = ++_generation;
    final revision = guard.revision;
    if (!mounted) {
      return;
    }
    setState(() {
      _loading = true;
      _authorized = false;
      _scope = null;
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
      final scope = _validScope(state);
      setState(() {
        _scope = scope;
        _authorized = scope != null;
        _loading = false;
      });
    } on Object {
      if (!mounted || generation != _generation || guard.revision != revision) {
        return;
      }
      setState(() {
        _scope = null;
        _loading = false;
        _authorized = false;
      });
    }
  }

  static int? _positiveId(String raw) {
    final id = int.tryParse(raw);
    if (raw.isEmpty ||
        raw.length > 10 ||
        !RegExp(r'^[1-9][0-9]*$').hasMatch(raw) ||
        id == null ||
        id > 2147483647) {
      return null;
    }
    return id;
  }

  Future<void> _pickTime({required bool start}) async {
    final guard = widget.sessionNavigationGuard;
    if (!_authorized ||
        _sending ||
        _command != null ||
        !guard.canAccessProtectedRoutes) {
      return;
    }
    final generation = _generation;
    final revision = guard.revision;
    final now = DateTime.now();
    final current = (start ? _start : _end) ?? now;
    final date = await showDatePicker(
      context: context,
      initialDate: current,
      firstDate: DateTime(now.year - 1),
      lastDate: DateTime(now.year + 5),
    );
    if (!mounted ||
        generation != _generation ||
        guard.revision != revision ||
        !guard.canAccessProtectedRoutes ||
        date == null) {
      return;
    }
    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(current),
    );
    if (!mounted ||
        generation != _generation ||
        guard.revision != revision ||
        !guard.canAccessProtectedRoutes ||
        time == null) {
      return;
    }
    final selected = DateTime(
      date.year,
      date.month,
      date.day,
      time.hour,
      time.minute,
    );
    setState(() {
      if (start) {
        _start = selected;
      } else {
        _end = selected;
      }
      _message = null;
    });
  }

  Future<void> _submit() async {
    final guard = widget.sessionNavigationGuard;
    if (!_authorized ||
        _loading ||
        _sending ||
        _queued ||
        _conflict ||
        !guard.canAccessProtectedRoutes) {
      return;
    }
    final patientId = _positiveId(_patient.text);
    final staffId = _positiveId(_staff.text);
    final start = _start;
    final end = _end;
    if (_command == null &&
        (patientId == null ||
            staffId == null ||
            start == null ||
            end == null ||
            !start.isBefore(end))) {
      setState(() {
        _message = 'Enter valid patient/staff IDs and a start before the end.';
      });
      return;
    }
    final generation = _generation;
    final revision = guard.revision;
    final scope = _scope;
    setState(() {
      _sending = true;
      _message = null;
    });
    bool current() =>
        mounted &&
        generation == _generation &&
        guard.revision == revision &&
        guard.canAccessProtectedRoutes;
    try {
      final session = await widget.loadSession();
      if (!current()) {
        return;
      }
      if (_validScope(session) != scope || scope == null) {
        _invalidateLocalState();
        if (mounted) {
          setState(() {});
        }
        return;
      }
      final command =
          _command ??
          AppointmentCreateCommand(
            patientId: patientId!,
            staffId: staffId!,
            scheduledStart: start!,
            scheduledEnd: end!,
            appointmentType: _type,
          );
      _command ??= command;
      final result = await widget.enqueue(command);
      if (!current()) {
        return;
      }
      if (result.operationId != command.operationId ||
          result.idempotencyKey != command.idempotencyKey) {
        setState(
          () => _message =
              'Queue acknowledgement was inconsistent. Review pending work.',
        );
        return;
      }
      setState(() {
        switch (result.status) {
          case AppointmentCreateEnqueueStatus.queued:
          case AppointmentCreateEnqueueStatus.alreadyQueued:
            _queued = true;
            _message = 'Request queued securely. Appointment NOT yet confirmed by server.';
          case AppointmentCreateEnqueueStatus.sessionUnavailable:
            _message = 'Not queued: authenticated clinic session unavailable.';
          case AppointmentCreateEnqueueStatus.conflict:
            _conflict = true;
            _message =
                'Queue conflict. Review pending work before another action.';
        }
      });
    } on Object {
      if (current()) {
        setState(() {
          _message = 'Queue persistence is uncertain. Retry only this same request; do not create another.';
        });
      }
    } finally {
      if (current()) {
        setState(() => _sending = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final ready =
        _authorized &&
        !_loading &&
        widget.sessionNavigationGuard.canAccessProtectedRoutes;
    final editing = ready && !_sending && _command == null;
    final canSubmit = ready && !_sending && !_queued && !_conflict;
    return Scaffold(
      appBar: AppBar(title: const Text('Create appointment request')),
      body: _loading
          ? const Center(
              child: CircularProgressIndicator(
                key: ValueKey('appointment-create-loading'),
              ),
            )
          : !ready
          ? const Center(
              child: Text(
                'Appointment creation is unavailable for this session.',
                key: ValueKey('appointment-create-denied'),
              ),
            )
          : ListView(
              key: const ValueKey('appointment-create-ready'),
              padding: const EdgeInsets.all(20),
              children: [
                const Text(
                  'An appointment is not booked until the backend confirms it.',
                ),
                const SizedBox(height: 14),
                TextField(
                  key: const ValueKey('appointment-create-patient-id'),
                  controller: _patient,
                  enabled: editing,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Patient ID'),
                ),
                TextField(
                  key: const ValueKey('appointment-create-staff-id'),
                  controller: _staff,
                  enabled: editing,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Staff ID'),
                ),
                const SizedBox(height: 14),
                DropdownButton<String>(
                  key: const ValueKey('appointment-create-type'),
                  value: _type,
                  items: _types
                      .map(
                        (type) => DropdownMenuItem(
                          value: type,
                          child: Text(type.replaceAll('_', ' ')),
                        ),
                      )
                      .toList(),
                  onChanged: editing
                      ? (value) {
                          if (value != null) {
                            setState(() => _type = value);
                          }
                        }
                      : null,
                ),
                const SizedBox(height: 8),
                OutlinedButton(
                  key: const ValueKey('appointment-create-start'),
                  onPressed: editing ? () => _pickTime(start: true) : null,
                  child: Text(
                    _start == null
                        ? 'Select start date/time'
                        : 'Start (local): ${_start!.toString()}',
                  ),
                ),
                OutlinedButton(
                  key: const ValueKey('appointment-create-end'),
                  onPressed: editing ? () => _pickTime(start: false) : null,
                  child: Text(
                    _end == null
                        ? 'Select end date/time'
                        : 'End (local): ${_end!.toString()}',
                  ),
                ),
                const SizedBox(height: 10),
                if (_message != null)
                  Text(
                    _message!,
                    key: const ValueKey('appointment-create-status'),
                  ),
                if (_queued)
                  const Text(
                    'Pending server confirmation',
                    key: ValueKey('appointment-create-queued'),
                  ),
                if (_conflict)
                  const Text(
                    'Manual review required',
                    key: ValueKey('appointment-create-conflict'),
                  ),
                const SizedBox(height: 12),
                FilledButton(
                  key: const ValueKey('appointment-create-submit'),
                  onPressed: canSubmit ? _submit : null,
                  child: Text(
                    _sending
                        ? 'Saving request...'
                        : _command == null
                        ? 'Queue appointment request'
                        : 'Retry the same request',
                  ),
                ),
              ],
            ),
    );
  }
}
