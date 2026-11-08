import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/nursing/data/models/ward_admission_workflow.dart';
import 'package:clinic_system_pro/features/nursing/data/ward_admission_workflow_remote_data_source.dart';

/// Staff-reviewed online admission and transfer. One clinical POST per screen.
/// Requires fresh server preflight and explicit user confirmation.
class WardAdmissionsTransfersScreen extends StatefulWidget {
  const WardAdmissionsTransfersScreen({super.key, required this.dataSource});

  final WardAdmissionWorkflowRemoteDataSource dataSource;

  @override
  State<WardAdmissionsTransfersScreen> createState() =>
      _WardAdmissionsTransfersScreenState();
}

class _WardAdmissionsTransfersScreenState
    extends State<WardAdmissionsTransfersScreen> {
  final _patient = TextEditingController();
  final _bed = TextEditingController();
  final _reason = TextEditingController();
  WardAdmissionSnapshot? _review;
  WardAdmissionReceipt? _receipt;
  bool _busy = false;
  String? _message;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
  }

  @override
  void didUpdateWidget(covariant WardAdmissionsTransfersScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.dataSource, widget.dataSource)) {
      oldWidget.dataSource.sessionNavigationGuard.removeListener(
        _onSessionChanged,
      );
      widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
      _clear();
    }
  }

  void _clear() {
    _generation++;
    _patient.clear();
    _bed.clear();
    _reason.clear();
    _review = null;
    _receipt = null;
    _message = null;
    _busy = false;
  }

  void _onSessionChanged() {
    if (!mounted) return;
    setState(_clear);
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onSessionChanged);
    _patient.dispose();
    _bed.dispose();
    _reason.dispose();
    super.dispose();
  }

  bool _valid(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.dataSource.sessionNavigationGuard.revision == revision;

  void _invalidateReview() {
    if (!mounted || _review == null) return;
    setState(() {
      _generation++;
      _review = null;
      _message = 'Inputs changed. Review live status again.';
    });
  }

  Future<void> _inspect() async {
    if (_busy || widget.dataSource.writeAttempted) return;
    final patientId = int.tryParse(_patient.text.trim());
    final bedId = int.tryParse(_bed.text.trim());
    if (patientId == null || patientId <= 0 || bedId == null || bedId <= 0) {
      setState(() {
        _review = null;
        _message = 'Enter a valid patient ID and destination bed ID.';
      });
      return;
    }
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _review = null;
      _receipt = null;
      _message = null;
    });
    try {
      final review = await widget.dataSource.inspect(
        patientId: patientId,
        bedId: bedId,
      );
      if (!_valid(generation, revision)) return;
      setState(() {
        _review = review;
        _busy = false;
      });
    } on Object {
      if (!_valid(generation, revision)) return;
      setState(() {
        _busy = false;
        _message = 'Live admission/bed status unavailable. No write sent.';
      });
    }
  }

  String _actionLabel(WardAdmissionAction action) => switch (action) {
    WardAdmissionAction.reserve => 'Reserve bed',
    WardAdmissionAction.admitDirect => 'Admit patient',
    WardAdmissionAction.admitFromReservation => 'Admit from reservation',
    WardAdmissionAction.transfer => 'Transfer patient',
  };

  Future<void> _perform(WardAdmissionAction action) async {
    final review = _review;
    if (_busy ||
        review == null ||
        widget.dataSource.writeAttempted ||
        !review.allows(action)) {
      return;
    }
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;
    final confirm = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Confirm ${_actionLabel(action).toLowerCase()}?'),
        content: Text(
          'Patient ${review.patientId}, bed ${review.bed.number} '
          '(${review.ward.name}). This is a live clinical action, '
          'not an offline draft. The server will recheck availability.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('p23e-c-confirm'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Confirm once'),
          ),
        ],
      ),
    );
    if (confirm != true ||
        !_valid(generation, revision) ||
        widget.dataSource.writeAttempted) {
      return;
    }
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      final result = await widget.dataSource.perform(
        reviewed: review,
        action: action,
        reason: _reason.text.trim().isEmpty ? null : _reason.text.trim(),
      );
      if (!_valid(generation, revision)) return;
      setState(() {
        _busy = false;
        _review = null;
        _receipt = result;
        _message =
            'Confirmed by server readback. Return to ward dashboard '
            'for a new workflow.';
      });
    } on Object {
      if (!_valid(generation, revision)) return;
      setState(() {
        _busy = false;
        _review = null;
        _message = widget.dataSource.writeAttempted
            ? 'Outcome not confirmed. Do not retry. Reconcile admission, '
                  'reservation and bed status with the server before any action.'
            : 'Preflight did not pass. No write was sent; review again.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final active =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;
    final review = _review;
    final terminal = widget.dataSource.writeAttempted;
    return Scaffold(
      appBar: AppBar(title: const Text('Admissions & transfers')),
      body: !active
          ? const Center(
              key: ValueKey('p23e-c-unavailable'),
              child: Text('Ward clinical session unavailable.'),
            )
          : ListView(
              key: const ValueKey('p23e-c-screen'),
              padding: const EdgeInsets.all(16),
              children: [
                const Text(
                  'Online only · no automatic retries or queued writes.',
                ),
                const SizedBox(height: 12),
                TextField(
                  key: const ValueKey('p23e-c-patient'),
                  controller: _patient,
                  enabled: !terminal && !_busy,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Patient ID'),
                  onChanged: (_) => _invalidateReview(),
                ),
                TextField(
                  key: const ValueKey('p23e-c-bed'),
                  controller: _bed,
                  enabled: !terminal && !_busy,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Destination bed ID',
                  ),
                  onChanged: (_) => _invalidateReview(),
                ),
                TextField(
                  key: const ValueKey('p23e-c-reason'),
                  controller: _reason,
                  enabled: !terminal && !_busy,
                  maxLength: 255,
                  decoration: const InputDecoration(
                    labelText: 'Reason (optional)',
                  ),
                ),
                FilledButton(
                  key: const ValueKey('p23e-c-review'),
                  onPressed: !_busy && !terminal ? _inspect : null,
                  child: const Text('Review live admission and bed status'),
                ),
                if (_busy)
                  const LinearProgressIndicator(
                    key: ValueKey('p23e-c-loading'),
                  ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('p23e-c-message')),
                if (terminal)
                  const Text(
                    'Further writes are locked for this screen session.',
                    key: ValueKey('p23e-c-write-locked'),
                  ),
                if (_receipt != null)
                  Text(
                    'Server-confirmed ${_actionLabel(_receipt!.action)} '
                    '(reference ${_receipt!.referenceId}).',
                    key: const ValueKey('p23e-c-confirmed'),
                  ),
                if (review != null) ...[
                  const Divider(),
                  Text('Patient ${review.patientId} · ${review.ward.name}'),
                  Text('Bed ${review.bed.number}: ${review.bed.status.name}'),
                  Text(
                    'Active admission: ${review.activeAdmission?.id ?? 'none'}',
                  ),
                  Text(
                    'Active reservation: ${review.activeReservation?.id ?? 'none'}',
                  ),
                  if (!review.mayWrite)
                    const Text(
                      'Viewing only: an authorized clinician is required.',
                    ),
                  if (!review.canStartNew &&
                      !review.canAdmitReservation &&
                      !review.canTransfer)
                    const Text(
                      'No admission or transfer is currently eligible. '
                      'Refresh and review server status.',
                    ),
                  for (final action in WardAdmissionAction.values)
                    OutlinedButton(
                      key: ValueKey('p23e-c-action-${action.name}'),
                      onPressed: !_busy && !terminal && review.allows(action)
                          ? () => _perform(action)
                          : null,
                      child: Text(_actionLabel(action)),
                    ),
                ],
              ],
            ),
    );
  }
}
