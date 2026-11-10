// P23H.S02 — standalone clinician-controlled prescription actions.
// No route registration; no automatic clinical writes or retries.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_prescription_detail.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_mutation_remote_data_source.dart';

enum PrescriptionMutationAction { create, cancel, complete }

class PatientPrescriptionMutationScreen extends StatefulWidget {
  const PatientPrescriptionMutationScreen({
    super.key,
    required this.source,
    required this.patientId,
    required this.action,
    this.prescriptionId,
  }) : assert(patientId > 0),
       assert(
         (action == PrescriptionMutationAction.create) ==
             (prescriptionId == null),
       );

  final PatientPrescriptionMutationRemoteDataSource source;
  final int patientId;
  final PrescriptionMutationAction action;
  final int? prescriptionId;

  @override
  State<PatientPrescriptionMutationScreen> createState() =>
      _PatientPrescriptionMutationScreenState();
}

class _PatientPrescriptionMutationScreenState
    extends State<PatientPrescriptionMutationScreen> {
  final _drugId = TextEditingController();
  final _dosage = TextEditingController();
  final _frequency = TextEditingController();
  final _duration = TextEditingController();
  final _quantity = TextEditingController();
  final _routeCode = TextEditingController();
  final _routeDisplay = TextEditingController();
  final _instructions = TextEditingController();
  final _reason = TextEditingController();
  final _justification = TextEditingController();
  String? _priority;
  String? _orderType;
  bool _acknowledged = false;
  bool _reviewedInput = false;
  bool _busy = false;
  bool _attempted = false;
  bool _loading = false;
  String? _message;
  PatientPrescriptionDetail? _record;
  PrescriptionMutationConfirmation? _confirmed;
  int _generation = 0;
  int _guardRevision = 0;

  @override
  void initState() {
    super.initState();
    _guardRevision = widget.source.reader.sessionNavigationGuard.revision;
    widget.source.reader.sessionNavigationGuard.addListener(_sessionChanged);
    if (widget.action != PrescriptionMutationAction.create) {
      unawaited(_read());
    }
  }

  @override
  void didUpdateWidget(covariant PatientPrescriptionMutationScreen old) {
    super.didUpdateWidget(old);
    if (old.patientId != widget.patientId ||
        old.prescriptionId != widget.prescriptionId ||
        old.action != widget.action ||
        !identical(old.source, widget.source)) {
      old.source.reader.sessionNavigationGuard.removeListener(_sessionChanged);
      widget.source.reader.sessionNavigationGuard.addListener(_sessionChanged);
      _generation++;
      _guardRevision = widget.source.reader.sessionNavigationGuard.revision;
      _record = null;
      _confirmed = null;
      _message = null;
      _loading = false;
      // No clinical mutation can be repeated by recycling a State object.
      _attempted = _attempted || old.source.writeAttempted;
      if (widget.action != PrescriptionMutationAction.create) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) unawaited(_read());
        });
      }
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.reader.sessionNavigationGuard.removeListener(_sessionChanged);
    for (final c in <TextEditingController>[
      _drugId,
      _dosage,
      _frequency,
      _duration,
      _quantity,
      _routeCode,
      _routeDisplay,
      _instructions,
      _reason,
      _justification,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  bool get _current =>
      widget.source.reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.source.reader.sessionNavigationGuard.revision == _guardRevision;

  void _sessionChanged() {
    if (!mounted) return;
    _generation++;
    setState(() {
      _record = null;
      _confirmed = null;
      _message = 'Session or clinic changed. Reopen the clinical workflow.';
      _loading = false;
      _attempted = true;
    });
  }

  Future<void> _read() async {
    if (_loading || widget.prescriptionId == null || !_current) return;
    final generation = ++_generation;
    setState(() {
      _loading = true;
      _record = null;
      _confirmed = null;
      _message = null;
    });
    try {
      final result = await widget.source.inspect(
        prescriptionId: widget.prescriptionId!,
        patientId: widget.patientId,
      );
      if (!mounted || generation != _generation || !_current) return;
      setState(() => _record = result);
    } on Object {
      if (!mounted || generation != _generation || !_current) return;
      setState(
        () => _message = 'Current prescription evidence unavailable. No clinical write sent.',
      );
    } finally {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
    }
  }

  PrescriptionDraftMedication _draft() {
    final drugId = int.tryParse(_drugId.text.trim());
    final qty = int.tryParse(_quantity.text.trim());
    if (drugId == null ||
        qty == null ||
        _priority == null ||
        _orderType == null ||
        !_reviewedInput) {
      throw const FormatException('Complete all required fields and review.');
    }
    final draft = PrescriptionDraftMedication(
      drugId: drugId,
      dosage: _dosage.text.trim(),
      frequency: _frequency.text.trim(),
      duration: _duration.text.trim(),
      quantity: qty,
      routeCode: _routeCode.text.trim(),
      routeDisplay: _routeDisplay.text.trim(),
      orderPriority: _priority!,
      orderType: _orderType!,
      instructions: _instructions.text.trim().isEmpty
          ? null
          : _instructions.text.trim(),
    );
    draft.toRequestBody();
    if (_acknowledged && _justification.text.trim().isEmpty) {
      throw const FormatException('Clinical safety justification is required.');
    }
    return draft;
  }

  Future<void> _submit() async {
    if (!_current || _busy || _attempted || widget.source.writeAttempted) {
      return;
    }
    PrescriptionDraftMedication? medication;
    if (widget.action == PrescriptionMutationAction.create) {
      try {
        medication = _draft();
      } on Object {
        setState(
          () => _message =
              'Check medication, dose, route, quantity, '
              'priority, care setting and clinical review. Nothing was sent.',
        );
        return;
      }
    } else if (_record == null ||
        _record!.prescription.status.name != 'active') {
      setState(
        () => _message = 'Only a verified active prescription can change.',
      );
      return;
    }
    if (widget.action == PrescriptionMutationAction.cancel &&
        _reason.text.trim().isEmpty) {
      setState(() => _message = 'Enter a cancellation reason.');
      return;
    }
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('Confirm server-owned prescription action'),
        content: Text(
          widget.action == PrescriptionMutationAction.create
              ? 'Create one medication order for patient #${widget.patientId}. '
                    'Verify the medication and clinical safety first. '
                    'This does not dispense or administer medication.'
              : 'Apply ${widget.action.name} to prescription '
                    '#${widget.prescriptionId} for patient #${widget.patientId}. '
                    'This is not a pharmacy dispense operation.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: const Text('Back'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialog, true),
            child: const Text('Confirm once'),
          ),
        ],
      ),
    );
    if (!mounted || accepted != true || !_current || _attempted) return;
    setState(() {
      _busy = true;
      _attempted = true;
      _message = null;
    });
    try {
      final result = switch (widget.action) {
        PrescriptionMutationAction.create => await widget.source.createOnce(
          patientId: widget.patientId,
          medications: [medication!],
          idempotencyKey: const Uuid().v4(),
          clinicalSafetyAcknowledged: _acknowledged,
          clinicalSafetyJustification: _acknowledged
              ? _justification.text.trim()
              : null,
        ),
        PrescriptionMutationAction.cancel => await widget.source.transitionOnce(
          reviewed: _record!,
          cancel: true,
          reason: _reason.text.trim(),
        ),
        PrescriptionMutationAction.complete =>
          await widget.source.transitionOnce(reviewed: _record!, cancel: false),
      };
      if (!mounted || !_current) return;
      setState(() {
        _confirmed = result;
        _message = 'Prescription state confirmed by backend readback.';
      });
    } on PrescriptionMutationScopeUnavailable {
      if (mounted) {
        setState(
          () => _message =
              'Session or role unavailable. Do not repeat this write.',
        );
      }
    } on PrescriptionMutationEvidenceChanged {
      if (mounted) {
        setState(
          () => _message =
              'Prescription changed. Reopen and review server evidence.',
        );
      }
    } on Object {
      if (mounted) {
        setState(
          () => _message =
              'Clinical write outcome unconfirmed. Check server records; '
              'do not repeat this request automatically.',
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _field(
    String key,
    String label,
    TextEditingController controller, {
    bool numeric = false,
    int maxLines = 1,
  }) => TextField(
    key: ValueKey(key),
    controller: controller,
    keyboardType: numeric ? TextInputType.number : TextInputType.text,
    maxLines: maxLines,
    decoration: InputDecoration(labelText: label),
  );

  @override
  Widget build(BuildContext context) {
    final creating = widget.action == PrescriptionMutationAction.create;
    final enabled =
        _current &&
        !_busy &&
        !_loading &&
        !_attempted &&
        !widget.source.writeAttempted &&
        (creating || _record?.prescription.status.name == 'active');
    return Scaffold(
      appBar: AppBar(title: const Text('Prescription clinical action')),
      body: !_current
          ? const Center(
              child: Text('Clinical session unavailable. Reopen workflow.'),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Patient #${widget.patientId} · Action: ${widget.action.name}',
                ),
                const Text(
                  'Online-only. Only backend-confirmed records are authoritative. '
                  'No automatic retry, dispensing, or administration.',
                ),
                if (_loading) const LinearProgressIndicator(),
                if (!creating && _record != null) ...[
                  Text('Server prescription #${_record!.prescription.id}'),
                  Text('Status: ${_record!.prescription.status.name}'),
                  Text(
                    '${_record!.prescription.items.length} medication item(s)',
                  ),
                  const Text(
                    'Review all medication details before confirming.',
                  ),
                ],
                if (creating) ...[
                  _field(
                    'p23h-s02-drug-id',
                    'Verified drug ID',
                    _drugId,
                    numeric: true,
                  ),
                  _field('p23h-s02-dose', 'Clinician-entered dosage', _dosage),
                  _field(
                    'p23h-s02-frequency',
                    'Clinician-entered frequency',
                    _frequency,
                  ),
                  _field(
                    'p23h-s02-duration',
                    'Clinician-entered duration',
                    _duration,
                  ),
                  _field(
                    'p23h-s02-quantity',
                    'Quantity',
                    _quantity,
                    numeric: true,
                  ),
                  _field('p23h-s02-route-code', 'Route code', _routeCode),
                  _field('p23h-s02-route-name', 'Route display', _routeDisplay),
                  _field(
                    'p23h-s02-instructions',
                    'Instructions (optional)',
                    _instructions,
                  ),
                  DropdownButtonFormField<String>(
                    key: const ValueKey('p23h-s02-priority'),
                    initialValue: _priority,
                    decoration: const InputDecoration(
                      labelText: 'Explicit priority',
                    ),
                    items: const [
                      DropdownMenuItem(
                        value: 'routine',
                        child: Text('Routine'),
                      ),
                      DropdownMenuItem(value: 'urgent', child: Text('Urgent')),
                      DropdownMenuItem(value: 'stat', child: Text('STAT')),
                    ],
                    onChanged: _busy
                        ? null
                        : (v) => setState(() => _priority = v),
                  ),
                  DropdownButtonFormField<String>(
                    key: const ValueKey('p23h-s02-type'),
                    initialValue: _orderType,
                    decoration: const InputDecoration(
                      labelText: 'Explicit care setting',
                    ),
                    items: const [
                      DropdownMenuItem(
                        value: 'inpatient',
                        child: Text('Inpatient'),
                      ),
                      DropdownMenuItem(
                        value: 'outpatient',
                        child: Text('Outpatient'),
                      ),
                      DropdownMenuItem(
                        value: 'discharge',
                        child: Text('Discharge'),
                      ),
                    ],
                    onChanged: _busy
                        ? null
                        : (v) => setState(() => _orderType = v),
                  ),
                  CheckboxListTile(
                    key: const ValueKey('p23h-s02-reviewed'),
                    title: const Text(
                      'I verified the patient, medication, dose and route',
                    ),
                    value: _reviewedInput,
                    onChanged: _busy
                        ? null
                        : (v) => setState(() => _reviewedInput = v ?? false),
                  ),
                  CheckboxListTile(
                    key: const ValueKey('p23h-s02-cds-acknowledged'),
                    title: const Text(
                      'Explicit clinical-safety acknowledgement',
                    ),
                    subtitle: const Text(
                      'Only after a clinical assessment. '
                      'The backend may still reject this order.',
                    ),
                    value: _acknowledged,
                    onChanged: _busy
                        ? null
                        : (v) => setState(() => _acknowledged = v ?? false),
                  ),
                  if (_acknowledged)
                    _field(
                      'p23h-s02-justification',
                      'Clinical safety justification',
                      _justification,
                      maxLines: 2,
                    ),
                ],
                if (widget.action == PrescriptionMutationAction.cancel)
                  _field(
                    'p23h-s02-cancel-reason',
                    'Cancellation reason',
                    _reason,
                    maxLines: 2,
                  ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('p23h-s02-message')),
                if (_confirmed != null) ...[
                  Text(
                    'Server-confirmed #${_confirmed!.prescription.prescription.id}',
                    key: const ValueKey('p23h-s02-confirmed'),
                  ),
                  Text(
                    'Server status: ${_confirmed!.prescription.prescription.status.name}',
                  ),
                  if (_confirmed!.interactionWarnings.isNotEmpty)
                    Text(
                      'Backend drug interaction warnings: '
                      '${_confirmed!.interactionWarnings.length}. '
                      'Review warnings in the clinical record; '
                      'confirmation does not mean risk-free.',
                    ),
                ],
                const SizedBox(height: 12),
                FilledButton(
                  key: const ValueKey('p23h-s02-confirm-action'),
                  onPressed: enabled ? _submit : null,
                  child: Text('Review and ${widget.action.name} once'),
                ),
                if (!creating && !_busy)
                  TextButton(
                    key: const ValueKey('p23h-s02-readback'),
                    onPressed: _current ? _read : null,
                    child: const Text('Refresh server evidence (GET only)'),
                  ),
                if (_attempted)
                  const Text(
                    'This screen cannot send another clinical write.',
                    key: ValueKey('p23h-s02-locked'),
                  ),
              ],
            ),
    );
  }
}
