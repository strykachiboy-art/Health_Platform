import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/nursing/data/models/nursing_note_workflow.dart';
import 'package:clinic_system_pro/features/nursing/data/nursing_note_workflow_remote_data_source.dart';

/// P23E.E nursing notes. Online-only; one confirmed clinical write per screen.
/// Signed notes are immutable; changes use the explicit amendment contract.
class NursingNotesScreen extends StatefulWidget {
  const NursingNotesScreen({super.key, required this.dataSource});

  final NursingNoteWorkflowRemoteDataSource dataSource;

  @override
  State<NursingNotesScreen> createState() => _NursingNotesScreenState();
}

class _NursingNotesScreenState extends State<NursingNotesScreen> {
  final _patient = TextEditingController();
  final _text = TextEditingController();
  final _reason = TextEditingController();
  NursingNoteKind _kind = NursingNoteKind.progress;
  NursingNoteReview? _review;
  NursingNoteEntry? _selected;
  List<NursingNoteAmendment> _amendments = const [];
  bool _busy = false;
  String? _message;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
  }

  @override
  void didUpdateWidget(covariant NursingNotesScreen oldWidget) {
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
    _text.clear();
    _reason.clear();
    _kind = NursingNoteKind.progress;
    _review = null;
    _selected = null;
    _amendments = const [];
    _busy = false;
    _message = null;
  }

  void _onSessionChanged() {
    if (!mounted) {
      return;
    }
    setState(_clear);
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onSessionChanged);
    _patient.dispose();
    _text.dispose();
    _reason.dispose();
    super.dispose();
  }

  bool _valid(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.dataSource.sessionNavigationGuard.revision == revision;

  void _patientChanged(String _) {
    if (!mounted) {
      return;
    }
    setState(() {
      _generation++;
      _review = null;
      _selected = null;
      _amendments = const [];
      _text.clear();
      _reason.clear();
      _message = 'Patient changed. Review the active admission again.';
    });
  }

  Future<void> _inspect() async {
    if (_busy || widget.dataSource.writeAttempted) {
      return;
    }
    final patientId = int.tryParse(_patient.text.trim());
    if (patientId == null || patientId <= 0 || patientId > 2147483647) {
      setState(() {
        _review = null;
        _selected = null;
        _message = 'Enter a valid patient ID.';
      });
      return;
    }
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _review = null;
      _selected = null;
      _amendments = const [];
      _text.clear();
      _reason.clear();
      _message = null;
    });
    try {
      final review = await widget.dataSource.inspect(patientId: patientId);
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _review = review;
        if (review == null) {
          _message = 'No active admission found for this patient.';
        }
      });
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _message = 'Nursing notes unavailable. No write sent.';
      });
    }
  }

  void _select(NursingNoteEntry? note) {
    if (_busy || widget.dataSource.writeAttempted) {
      return;
    }
    setState(() {
      _selected = note;
      _amendments = const [];
      _text.text = note?.text ?? '';
      _kind = note?.kind ?? NursingNoteKind.progress;
      _reason.clear();
      _message = null;
    });
  }

  Future<void> _perform(
    String label,
    Future<NursingNoteEntry> Function(NursingNoteReview) work,
  ) async {
    final review = _review;
    if (_busy ||
        review == null ||
        !review.mayWrite ||
        widget.dataSource.writeAttempted) {
      return;
    }
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;
    final confirm = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Confirm $label?'),
        content: Text(
          'Admission ${review.admission.id}, patient ${review.admission.patientId}. '
          'This sends one live clinical write. No offline storage, '
          'automatic replay, or additional write on this screen. '
          'Signing finalizes a note; amendments preserve clinical history.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('p23e-e-confirm'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Confirm once'),
          ),
        ],
      ),
    );
    if (confirm != true ||
        !_valid(generation, revision) ||
        _busy ||
        widget.dataSource.writeAttempted) {
      return;
    }
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      final result = await work(review);
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _review = null;
        _selected = null;
        _text.clear();
        _reason.clear();
        _message =
            'Server confirmed note ${result.id}. '
            'Return to the ward dashboard to start a new workflow.';
      });
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _review = null;
        _selected = null;
        _text.clear();
        _reason.clear();
        _message = widget.dataSource.writeAttempted
            ? 'Outcome not confirmed. Do not resend, sign again, or amend '
                  'until the authoritative note and history are reconciled.'
            : 'Clinical state changed or validation failed before write. '
                  'Review the admission again.';
      });
    }
  }

  Future<void> _history(NursingNoteEntry note) async {
    final review = _review;
    if (_busy || review == null) {
      return;
    }
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;
    setState(() => _busy = true);
    try {
      final amendments = await widget.dataSource.amendments(
        reviewed: review,
        note: note,
      );
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _amendments = amendments;
        _message =
            'Note ${note.id}: ${amendments.length} recorded amendment(s).';
      });
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _message = 'Amendment history unavailable. No write sent.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final available =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;
    final locked = widget.dataSource.writeAttempted;
    final review = _review;
    final selected = _selected;
    final canWrite =
        available && !locked && !_busy && review != null && review.mayWrite;

    return Scaffold(
      appBar: AppBar(title: const Text('Nursing notes')),
      body: !available
          ? const Center(
              key: ValueKey('p23e-e-unavailable'),
              child: Text('Nursing session unavailable.'),
            )
          : ListView(
              key: const ValueKey('p23e-e-screen'),
              padding: const EdgeInsets.all(16),
              children: [
                const Text('Online clinical documentation · no queued writes'),
                TextField(
                  key: const ValueKey('p23e-e-patient'),
                  controller: _patient,
                  enabled: !locked && !_busy,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Patient ID'),
                  onChanged: _patientChanged,
                ),
                FilledButton(
                  key: const ValueKey('p23e-e-review'),
                  onPressed: !_busy && !locked ? _inspect : null,
                  child: const Text('Review active admission and notes'),
                ),
                if (_busy)
                  const LinearProgressIndicator(
                    key: ValueKey('p23e-e-loading'),
                  ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('p23e-e-message')),
                if (locked)
                  const Text(
                    'Clinical writes locked for this screen. Reconcile before reopening.',
                    key: ValueKey('p23e-e-write-locked'),
                  ),
                if (review != null) ...[
                  const Divider(),
                  Text(
                    'Patient ${review.admission.patientId} · '
                    'Admission ${review.admission.id}',
                  ),
                  if (!review.mayWrite)
                    const Text(
                      'Read-only. Nursing note changes require nurse role.',
                    ),
                  Text(
                    'Nursing notes (${review.notes.length})',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  if (review.notes.isEmpty)
                    const Text('No nursing notes for this admission.'),
                  for (final note in review.notes) ...[
                    ListTile(
                      key: ValueKey('p23e-e-note-${note.id}'),
                      title: Text('Note ${note.id} · ${note.kind.name}'),
                      subtitle: Text(
                        '${note.status.name} · '
                        '${note.recordStatus.name} · '
                        'version ${note.version} / ${note.recordVersion}',
                      ),
                      selected: selected?.id == note.id,
                      onTap: !_busy && !locked ? () => _select(note) : null,
                    ),
                    if (selected?.id == note.id) ...[
                      Text('Observed: ${note.observedAt.toLocal()}'),
                      Text(
                        note.text,
                        key: ValueKey('p23e-e-detail-${note.id}'),
                      ),
                      if (note.mayAmend) ...[
                        TextButton(
                          key: ValueKey('p23e-e-history-${note.id}'),
                          onPressed: !_busy ? () => _history(note) : null,
                          child: const Text('View amendment history'),
                        ),
                        for (final revision in _amendments)
                          ListTile(
                            key: ValueKey('p23e-e-amendment-${revision.id}'),
                            title: Text(
                              'Amendment version ${revision.newVersion}',
                            ),
                            subtitle: Text(
                              '${revision.reason} · ${revision.amendedAt.toLocal()}',
                            ),
                          ),
                      ],
                      const Divider(),
                    ],
                  ],
                  OutlinedButton(
                    key: const ValueKey('p23e-e-new'),
                    onPressed: !locked && !_busy ? () => _select(null) : null,
                    child: const Text('Prepare new draft'),
                  ),
                  Text(
                    selected == null
                        ? 'New nursing note draft'
                        : 'Selected note ${selected.id}',
                  ),
                  DropdownButton<NursingNoteKind>(
                    key: const ValueKey('p23e-e-type'),
                    value: _kind,
                    onChanged:
                        canWrite && (selected == null || selected.mayEditDraft)
                        ? (value) {
                            if (value != null) {
                              setState(() => _kind = value);
                            }
                          }
                        : null,
                    items: [
                      for (final option in NursingNoteKind.values)
                        DropdownMenuItem(
                          value: option,
                          child: Text(option.name),
                        ),
                    ],
                  ),
                  TextField(
                    key: const ValueKey('p23e-e-text'),
                    controller: _text,
                    enabled: canWrite,
                    maxLines: 5,
                    maxLength: 20000,
                    onChanged: (_) => setState(() {}),
                    decoration: InputDecoration(
                      labelText: selected != null && selected.mayAmend
                          ? 'Replacement clinical document'
                          : 'Nursing note text',
                    ),
                  ),
                  if (selected == null)
                    FilledButton(
                      key: const ValueKey('p23e-e-create'),
                      onPressed: canWrite
                          ? () => _perform(
                              'create nursing draft',
                              (s) => widget.dataSource.createDraft(
                                reviewed: s,
                                kind: _kind,
                                text: _text.text,
                              ),
                            )
                          : null,
                      child: const Text('Create draft'),
                    ),
                  if (selected != null && selected.mayEditDraft) ...[
                    OutlinedButton(
                      key: const ValueKey('p23e-e-edit'),
                      onPressed: canWrite
                          ? () => _perform(
                              'edit draft',
                              (s) => widget.dataSource.editDraft(
                                reviewed: s,
                                selected: selected,
                                kind: _kind,
                                text: _text.text,
                              ),
                            )
                          : null,
                      child: const Text('Save draft changes'),
                    ),
                    FilledButton(
                      key: const ValueKey('p23e-e-sign'),
                      onPressed:
                          canWrite &&
                              selected.text == _text.text &&
                              selected.kind == _kind
                          ? () => _perform(
                              'sign nursing note',
                              (s) => widget.dataSource.signDraft(
                                reviewed: s,
                                selected: selected,
                              ),
                            )
                          : null,
                      child: const Text('Sign note'),
                    ),
                  ],
                  if (selected != null && selected.mayAmend) ...[
                    const Text(
                      'Signed note is immutable. Amendments create an auditable '
                      'new record version; the original is preserved.',
                    ),
                    TextField(
                      key: const ValueKey('p23e-e-reason'),
                      controller: _reason,
                      enabled: canWrite,
                      maxLength: 10000,
                      decoration: const InputDecoration(
                        labelText: 'Amendment reason (required)',
                      ),
                    ),
                    FilledButton(
                      key: const ValueKey('p23e-e-amend'),
                      onPressed: canWrite
                          ? () => _perform(
                              'amend signed nursing note',
                              (s) => widget.dataSource.amendSigned(
                                reviewed: s,
                                selected: selected,
                                reason: _reason.text,
                                replacementText: _text.text,
                              ),
                            )
                          : null,
                      child: const Text('Submit amendment'),
                    ),
                  ],
                ],
              ],
            ),
    );
  }
}
