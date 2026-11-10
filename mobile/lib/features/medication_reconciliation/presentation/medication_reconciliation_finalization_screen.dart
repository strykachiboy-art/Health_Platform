// P23G.S04: deliberately entered, online-only finalization/amendment screen.
// Route integration is deferred; no existing S01-S03.D screen is modified.
import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';

import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_finalization_remote_data_source.dart';

class MedicationReconciliationFinalizationScreen extends StatefulWidget {
  const MedicationReconciliationFinalizationScreen({
    super.key,
    required this.source,
    required this.reconciliationId,
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
  });

  final MedRecS04FinalizationRemoteDataSource source;
  final int reconciliationId;
  final int clinicId;
  final int patientId;
  final int admissionId;

  @override
  State<MedicationReconciliationFinalizationScreen> createState() =>
      _MedicationReconciliationFinalizationScreenState();
}

class _MedRecAmendInput {
  const _MedRecAmendInput(this.reason, this.notes);
  final String reason;
  final String notes;
}

// Own the controllers through the complete dialog dismissal animation.
class _MedRecAmendDialog extends StatefulWidget {
  const _MedRecAmendDialog({required this.originalNotes});
  final String originalNotes;

  @override
  State<_MedRecAmendDialog> createState() => _MedRecAmendDialogState();
}

class _MedRecAmendDialogState extends State<_MedRecAmendDialog> {
  final TextEditingController _reason = TextEditingController();
  late final TextEditingController _notes = TextEditingController(
    text: widget.originalNotes,
  );

  @override
  void dispose() {
    _reason.dispose();
    _notes.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Amend finalized reconciliation notes'),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text(
            'Reason is required. All medication decisions '
            'remain unchanged in this S04 notes-only amendment.',
          ),
          TextField(
            key: const Key('s04-amend-reason'),
            controller: _reason,
            maxLength: 10000,
            maxLines: 2,
            decoration: const InputDecoration(labelText: 'Amendment reason'),
          ),
          TextField(
            key: const Key('s04-amend-notes'),
            controller: _notes,
            maxLength: 10000,
            maxLines: 4,
            decoration: const InputDecoration(labelText: 'Revised notes'),
          ),
        ],
      ),
    ),
    actions: <Widget>[
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: () {
          final reason = _reason.text.trim();
          final notes = _notes.text;
          if (reason.isEmpty || notes == widget.originalNotes) return;
          Navigator.pop(context, _MedRecAmendInput(reason, notes));
        },
        child: const Text('Submit amendment'),
      ),
    ],
  );
}

class _MedicationReconciliationFinalizationScreenState
    extends State<MedicationReconciliationFinalizationScreen> {
  MedRecS04Record? _record;
  bool _busy = false;
  bool _loading = true;
  bool _attempted = false;
  String? _message;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    if (_busy) return;
    setState(() {
      _loading = true;
      _message = null;
    });
    try {
      final current = await widget.source.inspect(
        reconciliationId: widget.reconciliationId,
        clinicId: widget.clinicId,
        patientId: widget.patientId,
        admissionId: widget.admissionId,
      );
      if (!mounted) return;
      setState(() {
        _record = current;
      });
    } on Object {
      if (!mounted) return;
      setState(() {
        _record = null;
        _message = 'Unable to verify current server record. No write sent.';
      });
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _finalize() async {
    final reviewed = _record;
    if (reviewed == null || _busy || _attempted) return;
    if (reviewed.status != 'draft' || !reviewed.hasResolvedDecisions) return;
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Finalize medication reconciliation?'),
        content: const Text(
          'This signs the reviewed medication decisions. '
          'The draft becomes immutable. Confirm only after clinical review.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('Confirm finalization'),
          ),
        ],
      ),
    );
    if (!mounted || accepted != true || _busy || _attempted) return;
    setState(() {
      _busy = true;
      _attempted = true;
      _message = null;
    });
    try {
      final result = await widget.source.finalizeOnce(
        reviewed: reviewed,
        idempotencyKey: const Uuid().v4(),
      );
      if (!mounted) return;
      setState(() {
        _record = result;
        _message = 'Finalization confirmed by the server.';
      });
    } on MedRecS04EvidenceChanged {
      if (!mounted) return;
      setState(() {
        _message = 'The draft changed or is unresolved. Refresh and review.';
      });
    } on MedRecS04ScopeUnavailable {
      if (!mounted) return;
      setState(() {
        _message = 'Session or clinic changed. Refresh and sign in again.';
      });
    } on Object {
      if (!mounted) return;
      setState(() {
        _message =
            'Finalization unconfirmed. Check server state. '
            'Do not retry this operation automatically.';
      });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _amend() async {
    final reviewed = _record;
    if (reviewed == null || _busy || _attempted) return;
    if (!const <String>{'final', 'amended'}.contains(reviewed.status)) {
      return;
    }
    final input = await showDialog<_MedRecAmendInput>(
      context: context,
      builder: (_) => _MedRecAmendDialog(originalNotes: reviewed.notes ?? ''),
    );
    if (!mounted || input == null || _busy || _attempted) return;
    setState(() {
      _busy = true;
      _attempted = true;
      _message = null;
    });
    try {
      final result = await widget.source.amendNotesOnce(
        reviewed: reviewed,
        reason: input.reason,
        notes: input.notes,
      );
      if (!mounted) return;
      setState(() {
        _record = result;
        _message = 'Amendment confirmed by server readback.';
      });
    } on MedRecS04EvidenceChanged {
      if (!mounted) return;
      setState(() => _message = 'Record changed. Refresh and review again.');
    } on MedRecS04ScopeUnavailable {
      if (!mounted) return;
      setState(() => _message = 'Session changed. Amendment not confirmed.');
    } on Object {
      if (!mounted) return;
      setState(() {
        _message =
            'Amendment outcome unconfirmed. Refresh server state. '
            'Do not replay an uncertain write.';
      });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final record = _record;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Medication reconciliation finalization'),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: <Widget>[
                Text(
                  'Patient ${widget.patientId} • Admission ${widget.admissionId}',
                ),
                if (record != null) ...<Widget>[
                  Text('Event: ${record.eventType}'),
                  Text('Status: ${record.status} • Version ${record.version}'),
                  Text('Original author staff: ${record.authorStaffId}'),
                  if (record.noMedicationsReported)
                    const Text('No medications reported'),
                  Text('Notes: ${record.notes ?? '(none)'}'),
                  ...record.items.map(
                    (item) => ListTile(
                      title: Text(item['medication_name'] as String),
                      subtitle: Text('Decision: ${item['decision']}'),
                    ),
                  ),
                  if (record.status == 'draft' && !record.hasResolvedDecisions)
                    const Text(
                      'Unresolved decisions or missing medication evidence. '
                      'Finalization is blocked.',
                    ),
                  if (record.status == 'draft' &&
                      record.hasResolvedDecisions &&
                      !_attempted)
                    FilledButton(
                      key: const Key('s04-finalize'),
                      onPressed: _busy ? null : _finalize,
                      child: const Text('Finalize reviewed decisions'),
                    ),
                  if (const <String>{
                        'final',
                        'amended',
                      }.contains(record.status) &&
                      !_attempted)
                    FilledButton(
                      key: const Key('s04-amend'),
                      onPressed: _busy ? null : _amend,
                      child: const Text('Amend notes with reason'),
                    ),
                  if (_attempted)
                    const Text('Clinical write attempt locked on this screen.'),
                ],
                if (_message != null)
                  Text(_message!, key: const Key('s04-message')),
                OutlinedButton(
                  key: const Key('s04-refresh'),
                  onPressed: _busy ? null : _refresh,
                  child: const Text('Refresh from server'),
                ),
              ],
            ),
    );
  }
}
