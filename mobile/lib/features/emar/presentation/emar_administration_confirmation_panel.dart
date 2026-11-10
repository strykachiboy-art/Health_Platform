// P23F.S04: administer only after a separate, explicit nurse attestation.
// A barcode verification alone must never mark a dose administered.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';

import 'package:clinic_system_pro/features/emar/data/emar_medication_administration_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/emar_barcode_verification.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

class EmarAdministrationConfirmationPanel extends StatefulWidget {
  const EmarAdministrationConfirmationPanel({
    super.key,
    required this.adapter,
    required this.reviewed,
    required this.verification,
    this.onConfirmed,
  });

  final EmarMedicationAdministrationRemoteDataSource adapter;
  final EmarAdministration reviewed;
  final EmarBarcodeVerification verification;
  final void Function(EmarAdministration record)? onConfirmed;

  @override
  State<EmarAdministrationConfirmationPanel> createState() =>
      _EmarAdministrationConfirmationPanelState();
}

class _EmarAdministrationConfirmationPanelState
    extends State<EmarAdministrationConfirmationPanel> {
  final TextEditingController _notes = TextEditingController();
  bool _attested = false;
  bool _submitted = false;
  bool _busy = false;
  bool _invalidated = false;
  bool _confirmed = false;
  String? _message;

  bool get _available =>
      !_invalidated &&
      widget.adapter.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.reviewed.isPending &&
      widget.verification.verified &&
      widget.verification.administrationId == widget.reviewed.id;

  @override
  void initState() {
    super.initState();
    widget.adapter.sessionNavigationGuard.addListener(_invalidate);
  }

  @override
  void didUpdateWidget(covariant EmarAdministrationConfirmationPanel old) {
    super.didUpdateWidget(old);
    if (!identical(old.adapter, widget.adapter) ||
        !identical(old.reviewed, widget.reviewed) ||
        !identical(old.verification, widget.verification)) {
      old.adapter.sessionNavigationGuard.removeListener(_invalidate);
      widget.adapter.sessionNavigationGuard.addListener(_invalidate);
      _invalidate();
    }
  }

  void _invalidate() {
    _invalidated = true;
    _attested = false;
    _message = null;
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    widget.adapter.sessionNavigationGuard.removeListener(_invalidate);
    _notes.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_available ||
        !_attested ||
        _busy ||
        _submitted ||
        widget.adapter.attemptStarted) {
      return;
    }
    setState(() {
      _submitted = true;
      _busy = true;
      _message = 'Submitting once; do not repeat this clinical action.';
    });
    try {
      final result = await widget.adapter.administerOnce(
        reviewed: widget.reviewed,
        verified: widget.verification,
        operationKey: const Uuid().v4(),
        notes: _notes.text,
      );
      if (!mounted || _invalidated) return;
      setState(() {
        _busy = false;
        _confirmed = true;
        _message =
            'Server confirmed administration #${result.id}. '
            'This action is closed. Return to the current chart.';
      });
      widget.onConfirmed?.call(result);
    } on Object {
      if (!mounted || _invalidated) return;
      setState(() {
        _busy = false;
        _message =
            'Outcome unconfirmed or clinical evidence changed. '
            'Do NOT administer again or retry this action. '
            'Reconcile the server record with the clinical team.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final enabled =
        _available && !_submitted && !_busy && !widget.adapter.attemptStarted;
    return Card(
      key: const ValueKey('p23f-s04-administration-panel'),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Separate medication administration confirmation',
              style: TextStyle(fontWeight: FontWeight.bold),
            ),
            Text(
              'Patient #${widget.reviewed.patientId} · '
              'Admission #${widget.reviewed.admissionId} · '
              'Administration #${widget.reviewed.id}',
            ),
            Text(
              'Dose: ${widget.reviewed.doseValue ?? 'unknown'} '
              '${widget.reviewed.doseUnit ?? ''} · '
              'Route: ${widget.reviewed.routeCode ?? 'unknown'}',
            ),
            Text(
              'Successful server verification event '
              '#${widget.verification.id}',
            ),
            const Text('A scan is NOT proof that a dose was administered.'),
            TextField(
              key: const ValueKey('p23f-s04-notes'),
              controller: _notes,
              enabled: enabled,
              maxLength: 4000,
              decoration: const InputDecoration(
                labelText: 'Administration notes (optional)',
              ),
            ),
            CheckboxListTile(
              key: const ValueKey('p23f-s04-attestation'),
              value: _attested,
              onChanged: enabled
                  ? (value) => setState(() => _attested = value == true)
                  : null,
              title: const Text(
                'I confirm I actually administered '
                'this medication dose to this patient according to '
                'the authorized clinical protocol.',
              ),
            ),
            FilledButton(
              key: const ValueKey('p23f-s04-submit'),
              onPressed: enabled && _attested
                  ? () => unawaited(_submit())
                  : null,
              child: const Text('Record dose as administered ONCE'),
            ),
            if (_message != null)
              Text(_message!, key: const ValueKey('p23f-s04-message')),
            if (_submitted)
              Text(
                _confirmed
                    ? 'Administration confirmed by server readback.'
                    : 'This submission attempt is closed. No automatic retry.',
              ),
          ],
        ),
      ),
    );
  }
}
