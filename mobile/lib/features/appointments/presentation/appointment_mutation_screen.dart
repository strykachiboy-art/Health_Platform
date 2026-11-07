import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/models/appointment_record.dart';

enum _MutationAction { reschedule, cancel, confirm, complete, noShow }

/// One user-authorized send per action. No local queue, automatic retries,
/// or PHI persistence. Reconciliation uses existing authorized GET pagination.
class AppointmentMutationScreen extends StatefulWidget {
  const AppointmentMutationScreen({
    super.key,
    required this.appointment,
    required this.mutationDataSource,
    required this.readDataSource,
  });

  final AppointmentRecord appointment;
  final AppointmentMutationRemoteDataSource mutationDataSource;
  final AppointmentRemoteDataSource readDataSource;

  @override
  State<AppointmentMutationScreen> createState() =>
      _AppointmentMutationScreenState();
}

class _AppointmentMutationScreenState extends State<AppointmentMutationScreen> {
  final _start = TextEditingController();
  final _end = TextEditingController();
  final _reason = TextEditingController();
  final _completionNotes = TextEditingController();

  late int _revision;
  int _generation = 0;
  bool _acknowledged = false;
  bool _busy = false;
  bool _blocked = false;
  bool _uncertain = false;
  bool _readMatches = false;
  String? _message;
  AppointmentRecord? _confirmedRecord;
  _MutationAction? _attemptedAction;
  DateTime? _attemptedStart;
  DateTime? _attemptedEnd;
  String? _attemptedReason;
  String? _attemptedCompletionNotes;

  SessionNavigationGuard get _guard =>
      widget.mutationDataSource.sessionNavigationGuard;

  bool get _sameGuard =>
      identical(widget.readDataSource.sessionNavigationGuard, _guard);

  bool get _authorized =>
      _sameGuard &&
      _guard.canAccessProtectedRoutes &&
      _guard.revision == _revision;

  bool get _eligible =>
      widget.appointment.id > 0 &&
      widget.appointment.clinicId > 0 &&
      widget.appointment.patientId > 0 &&
      widget.appointment.staffId > 0 &&
      (widget.appointment.status == AppointmentRecordStatus.scheduled ||
          widget.appointment.status == AppointmentRecordStatus.confirmed);

  @override
  void initState() {
    super.initState();
    _revision = _guard.revision;
    _guard.addListener(_sessionChanged);
  }

  @override
  void didUpdateWidget(covariant AppointmentMutationScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.mutationDataSource, widget.mutationDataSource) ||
        !identical(oldWidget.readDataSource, widget.readDataSource) ||
        !identical(oldWidget.appointment, widget.appointment)) {
      oldWidget.mutationDataSource.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      _revision = _guard.revision;
      _guard.addListener(_sessionChanged);
      _generation++;
      _clearForm();
      _busy = false;
      _blocked = false;
      _uncertain = false;
      _readMatches = false;
      _message = null;
      _attemptedAction = null;
      _confirmedRecord = null;
    }
  }

  void _clearForm() {
    _start.clear();
    _end.clear();
    _reason.clear();
    _completionNotes.clear();
    _acknowledged = false;
    _attemptedStart = null;
    _attemptedEnd = null;
    _attemptedReason = null;
    _attemptedCompletionNotes = null;
  }

  void _sessionChanged() {
    _generation++;
    _clearForm();
    if (!mounted) {
      return;
    }
    setState(() {
      _busy = false;
      _message = null;
      _confirmedRecord = null;
      _readMatches = false;
      // Do not reset _revision: this screen is permanently stale.
    });
  }

  @override
  void dispose() {
    _generation++;
    _guard.removeListener(_sessionChanged);
    _start.dispose();
    _end.dispose();
    _reason.dispose();
    _completionNotes.dispose();
    super.dispose();
  }

  bool _current(int generation) =>
      mounted && generation == _generation && _authorized;

  static DateTime? _withExplicitOffset(String input) {
    final value = input.trim();
    if (!RegExp(
      r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$',
    ).hasMatch(value)) {
      return null;
    }
    return DateTime.tryParse(value);
  }

  void _showValidation(String message) {
    setState(() {
      _message = message;
      _acknowledged = false;
    });
  }

  Future<void> _submit(_MutationAction action) async {
    if (!_authorized || !_eligible || _busy || _blocked || !_acknowledged) {
      return;
    }
    DateTime? newStart;
    DateTime? newEnd;
    String? reason;
    String? completionNotes;
    if (action == _MutationAction.reschedule) {
      newStart = _withExplicitOffset(_start.text);
      newEnd = _withExplicitOffset(_end.text);
      if (newStart == null || newEnd == null || !newStart.isBefore(newEnd)) {
        _showValidation(
          'Enter valid start/end ISO timestamps with explicit timezone; end must follow start.',
        );
        return;
      }
    } else if (action == _MutationAction.cancel) {
      reason = _reason.text.trim();
      if (reason.length > 255) {
        _showValidation('Cancellation reason must be at most 255 characters.');
        return;
      }
      if (reason.isEmpty) {
        reason = null;
      }
    } else if (action == _MutationAction.complete) {
      final raw = _completionNotes.text;
      final trimmed = raw.trim();
      if (raw.isNotEmpty && (trimmed.isEmpty || trimmed.length > 2000)) {
        _showValidation(
          'Completion notes must contain non-whitespace text and be at most 2000 characters.',
        );
        return;
      }
      completionNotes = trimmed.isEmpty ? null : trimmed;
    }

    final generation = ++_generation;
    setState(() {
      _busy = true;
      _acknowledged = false;
      _message = null;
      _attemptedAction = action;
      _attemptedStart = newStart;
      _attemptedEnd = newEnd;
      _attemptedReason = reason;
      _attemptedCompletionNotes = completionNotes;
    });

    try {
      final AppointmentMutationResult result;
      if (action == _MutationAction.reschedule) {
        result = await widget.mutationDataSource.reschedule(
          appointment: widget.appointment,
          newStart: newStart!,
          newEnd: newEnd!,
        );
      } else if (action == _MutationAction.cancel) {
        result = await widget.mutationDataSource.cancel(
          appointment: widget.appointment,
          reason: reason,
        );
      } else if (action == _MutationAction.confirm) {
        result = await widget.mutationDataSource.confirm(
          appointment: widget.appointment,
        );
      } else if (action == _MutationAction.complete) {
        result = await widget.mutationDataSource.complete(
          appointment: widget.appointment,
          notes: completionNotes,
        );
      } else {
        result = await widget.mutationDataSource.markNoShow(
          appointment: widget.appointment,
        );
      }
      if (!_current(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        if (result.outcome == AppointmentMutationOutcome.confirmed) {
          _blocked = true;
          _confirmedRecord = result.appointment;
          _message = 'Backend confirmed the appointment change. Reopen the schedule for further actions.';
        } else if (result.outcome == AppointmentMutationOutcome.rejected) {
          _blocked = true;
          _message = 'Server rejected the change. Reopen a fresh schedule before another action.';
        } else if (result.outcome ==
            AppointmentMutationOutcome.reconciliationRequired) {
          _blocked = true;
          _uncertain = true;
          _message = 'Outcome uncertain. Do not retry. Use Recheck server to inspect the current backend record.';
        } else if (result.outcome ==
            AppointmentMutationOutcome.sessionUnavailable) {
          _blocked = true;
          _message = 'Session unavailable. No appointment change is confirmed.';
        } else {
          _blocked = true;
          _message =
              'Another action may be in flight. No second request was sent.';
        }
      });
    } on Object {
      if (!_current(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        _blocked = true;
        _uncertain = true;
        _message = 'Unable to verify whether the request reached the server. Do not retry.';
      });
    }
  }

  bool _observedRequestedState(AppointmentRecord record) {
    if (record.id != widget.appointment.id ||
        record.patientId != widget.appointment.patientId ||
        record.clinicId != widget.appointment.clinicId ||
        record.staffId != widget.appointment.staffId) {
      return false;
    }
    if (_attemptedAction == _MutationAction.cancel) {
      return record.status == AppointmentRecordStatus.cancelled &&
          record.cancelledAt != null &&
          (_attemptedReason == null ||
              record.cancellationReason == _attemptedReason);
    }
    if (_attemptedAction == _MutationAction.confirm) {
      return record.status == AppointmentRecordStatus.confirmed;
    }
    if (_attemptedAction == _MutationAction.complete) {
      return record.status == AppointmentRecordStatus.completed &&
          (_attemptedCompletionNotes == null ||
              record.notes == _attemptedCompletionNotes);
    }
    if (_attemptedAction == _MutationAction.noShow) {
      return record.status == AppointmentRecordStatus.noShow;
    }
    final start = DateTime.tryParse(record.scheduledStart);
    final end = DateTime.tryParse(record.scheduledEnd);
    return _attemptedAction == _MutationAction.reschedule &&
        _attemptedStart != null &&
        _attemptedEnd != null &&
        start != null &&
        end != null &&
        start.isAtSameMomentAs(_attemptedStart!) &&
        end.isAtSameMomentAs(_attemptedEnd!);
  }

  Future<void> _recheck() async {
    if (!_authorized || !_uncertain || _busy) {
      return;
    }
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _message = null;
      _readMatches = false;
    });
    try {
      AppointmentRecord? observed;
      // The backend has no appointment GET-by-ID. Check bounded pages;
      // absence from the scanned pages is NEVER proof of non-execution.
      for (var page = 1; page <= 5; page++) {
        final result = await widget.readDataSource.fetchForPatient(
          patientId: widget.appointment.patientId,
          page: page,
          perPage: 100,
        );
        if (!_current(generation)) {
          return;
        }
        for (final row in result.items) {
          if (row.id == widget.appointment.id) {
            observed = row;
            break;
          }
        }
        if (observed != null || !result.hasNext) {
          break;
        }
      }
      if (!_current(generation)) {
        return;
      }
      final matches = observed != null && _observedRequestedState(observed);
      setState(() {
        _busy = false;
        _readMatches = matches;
        _message = matches
            ? 'Fresh backend read reflects the requested state. Attribution is not proven. Automatic retry remains blocked.'
            : 'Fresh backend read did not establish the requested state. Outcome remains uncertain; do not retry automatically.';
      });
    } on Object {
      if (!_current(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        _message = 'Unable to obtain an authoritative backend read. Outcome remains uncertain; do not retry.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!_authorized) {
      return const Scaffold(
        body: Center(
          child: Text(
            'Session or appointment source changed. Close this screen.',
            key: ValueKey('s04c-session-unavailable'),
          ),
        ),
      );
    }
    final enabled = _eligible && !_busy && !_blocked && _acknowledged;
    return Scaffold(
      appBar: AppBar(title: const Text('Change appointment')),
      body: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Appointment #${widget.appointment.id}',
                key: const ValueKey('s04c-appointment-id'),
              ),
              const Text(
                'Reschedule, cancellation, confirmation, completion and no-show are online-only, single-send actions. A timeout may mean the change happened. No automatic retries.',
              ),
              const SizedBox(height: 12),
              if (!_eligible)
                const Text(
                  'This appointment status cannot be changed from this screen.',
                  key: ValueKey('s04c-ineligible'),
                )
              else ...[
                TextField(
                  key: const ValueKey('s04c-start'),
                  controller: _start,
                  enabled: !_busy && !_blocked,
                  decoration: const InputDecoration(
                    labelText: 'New start (ISO timestamp with offset)',
                    hintText: '2026-12-11T09:00:00Z',
                  ),
                ),
                TextField(
                  key: const ValueKey('s04c-end'),
                  controller: _end,
                  enabled: !_busy && !_blocked,
                  decoration: const InputDecoration(
                    labelText: 'New end (ISO timestamp with offset)',
                    hintText: '2026-12-11T10:00:00Z',
                  ),
                ),
                TextField(
                  key: const ValueKey('s04c-reason'),
                  controller: _reason,
                  enabled: !_busy && !_blocked,
                  maxLength: 255,
                  decoration: const InputDecoration(
                    labelText: 'Cancellation reason (optional)',
                  ),
                ),
                CheckboxListTile(
                  key: const ValueKey('s04c-acknowledge'),
                  value: _acknowledged,
                  onChanged: _busy || _blocked
                      ? null
                      : (value) =>
                            setState(() => _acknowledged = value ?? false),
                  title: const Text(
                    'I confirm this single appointment change.',
                  ),
                ),
                ElevatedButton(
                  key: const ValueKey('s04c-reschedule'),
                  onPressed: enabled
                      ? () => unawaited(_submit(_MutationAction.reschedule))
                      : null,
                  child: const Text('Submit reschedule once'),
                ),
                ElevatedButton(
                  key: const ValueKey('s04c-cancel'),
                  onPressed: enabled
                      ? () => unawaited(_submit(_MutationAction.cancel))
                      : null,
                  child: const Text('Submit cancellation once'),
                ),
                if (widget.appointment.status ==
                    AppointmentRecordStatus.scheduled)
                  ElevatedButton(
                    key: const ValueKey('s04d-confirm'),
                    onPressed: enabled
                        ? () => unawaited(_submit(_MutationAction.confirm))
                        : null,
                    child: const Text('Confirm appointment once'),
                  ),
                if (widget.appointment.status ==
                    AppointmentRecordStatus.confirmed) ...[
                  TextField(
                    key: const ValueKey('s04d-notes'),
                    controller: _completionNotes,
                    enabled: !_busy && !_blocked,
                    maxLength: 2000,
                    decoration: const InputDecoration(
                      labelText: 'Completion notes (optional)',
                    ),
                  ),
                  ElevatedButton(
                    key: const ValueKey('s04d-complete'),
                    onPressed: enabled
                        ? () => unawaited(_submit(_MutationAction.complete))
                        : null,
                    child: const Text('Mark completed once'),
                  ),
                  ElevatedButton(
                    key: const ValueKey('s04d-no-show'),
                    onPressed: enabled
                        ? () => unawaited(_submit(_MutationAction.noShow))
                        : null,
                    child: const Text('Mark no-show once'),
                  ),
                ],
              ],
              if (_busy)
                const CircularProgressIndicator(key: ValueKey('s04c-busy')),
              if (_message != null)
                Text(_message!, key: const ValueKey('s04c-message')),
              if (_confirmedRecord != null)
                Text(
                  'Server-confirmed appointment #${_confirmedRecord!.id}.',
                  key: const ValueKey('s04c-confirmed'),
                ),
              if (_uncertain) ...[
                TextButton(
                  key: const ValueKey('s04c-recheck'),
                  onPressed: _busy ? null : () => unawaited(_recheck()),
                  child: const Text('Recheck server (read only)'),
                ),
                Text(
                  _readMatches
                      ? 'Server state observed; mutation retry remains blocked.'
                      : 'Operation outcome not confirmed; mutation retry remains blocked.',
                  key: const ValueKey('s04c-uncertain'),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
