// P23F.S05: explicit nurse-only hold/refusal/missed-dose documentation.
// Do not infer a missed or late dose from the device clock.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';

import 'package:clinic_system_pro/features/emar/data/emar_terminal_outcome_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

class EmarTerminalOutcomePanel extends StatefulWidget {
  const EmarTerminalOutcomePanel({
    super.key,
    required this.adapter,
    required this.reviewed,
  });

  final EmarTerminalOutcomeRemoteDataSource adapter;
  final EmarAdministration reviewed;

  @override
  State<EmarTerminalOutcomePanel> createState() =>
      _EmarTerminalOutcomePanelState();
}

class _EmarTerminalOutcomePanelState extends State<EmarTerminalOutcomePanel> {
  final TextEditingController _reason = TextEditingController();
  EmarTerminalAction _action = EmarTerminalAction.hold;
  bool _attested = false;
  bool _submitted = false;
  bool _busy = false;
  bool _invalidated = false;
  bool _confirmed = false;
  String? _message;

  bool get _available =>
      !_invalidated &&
      widget.adapter.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.reviewed.isPending;

  @override
  void initState() {
    super.initState();
    widget.adapter.sessionNavigationGuard.addListener(_invalidate);
  }

  @override
  void didUpdateWidget(covariant EmarTerminalOutcomePanel old) {
    super.didUpdateWidget(old);
    if (!identical(old.adapter, widget.adapter) ||
        !identical(old.reviewed, widget.reviewed)) {
      old.adapter.sessionNavigationGuard.removeListener(_invalidate);
      widget.adapter.sessionNavigationGuard.addListener(_invalidate);
      _invalidate();
    }
  }

  void _invalidate() {
    _invalidated = true;
    _attested = false;
    _message = null;
    _reason.clear();
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    widget.adapter.sessionNavigationGuard.removeListener(_invalidate);
    _reason.dispose();
    super.dispose();
  }

  Future<void> _record() async {
    final reason = _reason.text.trim();
    if (!_available ||
        !_attested ||
        _busy ||
        _submitted ||
        widget.adapter.attemptStarted ||
        reason.isEmpty ||
        reason.length > 500) {
      return;
    }
    setState(() {
      _submitted = true;
      _busy = true;
      _message = 'Submitting once. Do not repeat this clinical action.';
    });
    try {
      final result = await widget.adapter.recordOnce(
        reviewed: widget.reviewed,
        action: _action,
        reason: reason,
        operationKey: const Uuid().v4(),
      );
      if (!mounted || _invalidated) return;
      setState(() {
        _busy = false;
        _confirmed = true;
        _message =
            'Server confirmed administration #${result.id} as '
            '${result.status.name}. Reopen the chart for an updated history.';
      });
    } on Object {
      if (!mounted || _invalidated) return;
      setState(() {
        _busy = false;
        _message =
            'Outcome unconfirmed or clinical context changed. '
            'Do NOT repeat this operation. Check the server record '
            'with the clinical team.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final reason = _reason.text.trim();
    final enabled =
        _available && !_busy && !_submitted && !widget.adapter.attemptStarted;
    return Scaffold(
      appBar: AppBar(title: const Text('Medication outcome documentation')),
      body: !_available
          ? const Center(
              key: ValueKey('p23f-s05-session-unavailable'),
              child: Text(
                'eMAR context unavailable. Reopen patient and admission.',
              ),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Text(
                  'Online only. A clinical outcome must be '
                  'recorded from the real bedside assessment; '
                  'barcode scanning does not record a dose.',
                ),
                const SizedBox(height: 12),
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
                const SizedBox(height: 12),
                DropdownButton<EmarTerminalAction>(
                  key: const ValueKey('p23f-s05-action'),
                  value: _action,
                  isExpanded: true,
                  onChanged: enabled
                      ? (value) {
                          if (value != null) {
                            setState(() {
                              _action = value;
                              _attested = false;
                            });
                          }
                        }
                      : null,
                  items: [
                    for (final action in EmarTerminalAction.values)
                      DropdownMenuItem(
                        value: action,
                        child: Text(action.label),
                      ),
                  ],
                ),
                if (_action == EmarTerminalAction.miss)
                  const Text(
                    'Missed/omitted is a documented clinical '
                    'outcome, not a device-computed late-dose status.',
                  ),
                TextField(
                  key: const ValueKey('p23f-s05-reason'),
                  controller: _reason,
                  enabled: enabled,
                  maxLength: 500,
                  maxLines: 3,
                  onChanged: (_) => setState(() => _attested = false),
                  decoration: const InputDecoration(
                    labelText: 'Clinical reason (required)',
                  ),
                ),
                CheckboxListTile(
                  key: const ValueKey('p23f-s05-attestation'),
                  value: _attested,
                  onChanged: enabled && reason.isNotEmpty
                      ? (value) => setState(() => _attested = value == true)
                      : null,
                  title: Text(
                    'I reviewed the patient and confirm '
                    'the ${_action.label.toLowerCase()} outcome and reason.',
                  ),
                ),
                FilledButton(
                  key: const ValueKey('p23f-s05-submit'),
                  onPressed: enabled && _attested && reason.isNotEmpty
                      ? () => unawaited(_record())
                      : null,
                  child: const Text('Record this outcome ONCE'),
                ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('p23f-s05-message')),
                if (_submitted)
                  Text(
                    _confirmed
                        ? 'Server readback confirmed the terminal outcome.'
                        : 'This submission is closed. No automatic retry.',
                  ),
              ],
            ),
    );
  }
}
