import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/problems/data/problem_source.dart';

String problemLabel(String value) => value.replaceAll('_', ' ');
String _codingLabel(String value) => value == problemCodingSystems.last
    ? 'SNOMED CT'
    : value == problemCodingSystems.first
    ? 'ICD-10'
    : 'ICD-10-CM';
String _failure(Object e) {
  if (e is ProblemOutcomeUnconfirmed) {
    return 'Write outcome unconfirmed. Review the server record; further problem writes are locked. Do not retry.';
  }
  if (e is ProblemUnavailable) {
    return 'Authorized session or online connection unavailable. Reopen from the authenticated workspace.';
  }
  if (e is ApiException) {
    if (e.statusCode == 409) {
      return 'Problem changed or conflicts with an existing record. Refresh and review before submitting a new action.';
    }
    if (e.statusCode == 404) {
      return 'Problem list or record unavailable. This staged feature may be disabled.';
    }
    if (e.statusCode == 401 || e.statusCode == 403) {
      return 'Problem access denied. Revalidate your session.';
    }
    if (e.statusCode == 400 || e.statusCode == 422) {
      return 'The server rejected this clinical action. Review the clinical information and reason.';
    }
  }
  return 'Unable to verify the structured problem data.';
}

/// Entry is mounted only below a server-verified patient/consultation record.
/// A bound Doctor/Nurse session is required even to display the entry.
class ProblemWorkspaceEntry extends StatefulWidget {
  const ProblemWorkspaceEntry({
    super.key,
    required this.source,
    required this.patientId,
    this.consultationId,
    this.allowWrite = true,
  });
  final ProblemSource source;
  final int patientId;
  final int? consultationId;
  final bool allowWrite;
  @override
  State<ProblemWorkspaceEntry> createState() => _ProblemWorkspaceEntryState();
}

class _ProblemWorkspaceEntryState extends State<ProblemWorkspaceEntry> {
  ProblemSession? _session;
  ProblemPage? _page;
  String? _message;
  int _generation = 0;
  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_invalidate);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant ProblemWorkspaceEntry oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source) ||
        oldWidget.patientId != widget.patientId ||
        oldWidget.consultationId != widget.consultationId) {
      oldWidget.source.guard.removeListener(_invalidate);
      widget.source.guard.addListener(_invalidate);
      _generation++;
      _session = null;
      _page = null;
      _message = null;
      unawaited(_load());
    }
  }

  void _invalidate() {
    _generation++;
    if (mounted) {
      setState(() {
        _session = null;
        _page = null;
        _message = null;
      });
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.guard.removeListener(_invalidate);
    super.dispose();
  }

  Future<void> _load() async {
    final generation = ++_generation;
    ProblemSession? session;
    try {
      session = await widget.source.open();
      if (!mounted || generation != _generation || !session.current) {
        return;
      }
      setState(() {
        _session = session;
        _page = null;
        _message = null;
      });
      final page = await session.list(widget.patientId, limit: 5);
      if (!mounted || generation != _generation || !session.current) {
        return;
      }
      setState(() => _page = page);
    } on Object catch (e) {
      if (!mounted || generation != _generation || session?.current != true) {
        return;
      }
      setState(() {
        _page = null;
        _message = _failure(e);
      });
    }
  }

  Future<void> _open() async {
    final session = _session;
    if (session == null || !session.current) {
      return;
    }
    final generation = _generation;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => ProblemWorkspaceScreen(
          source: widget.source,
          session: session,
          patientId: widget.patientId,
          consultationId: widget.consultationId,
          allowWrite: widget.allowWrite,
        ),
      ),
    );
    if (mounted && generation == _generation && session.current) {
      unawaited(_load());
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = _session;
    if (session == null || !session.current) {
      return const SizedBox.shrink();
    }
    final page = _page;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Structured clinical problems',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const Text(
              'Longitudinal clinical records, separate from legacy notes and allergies.',
            ),
            if (_message != null)
              Text(_message!)
            else if (page == null)
              const LinearProgressIndicator(),
            if (page != null) ...[
              if (page.items.isEmpty)
                const Text('No current structured problems returned.'),
              for (final row in page.items)
                Text(
                  '${row.content.name} · ${problemLabel(row.content.category)} · ${problemLabel(row.content.verificationStatus)} · ${problemLabel(row.content.clinicalStatus)}',
                ),
              if (page.nextAfter != null)
                const Text(
                  'Showing first five current problems; more available.',
                ),
            ],
            OutlinedButton(
              key: const ValueKey('problem-open-workspace'),
              onPressed: _open,
              child: const Text('Review problem list'),
            ),
          ],
        ),
      ),
    );
  }
}

class ProblemWorkspaceScreen extends StatefulWidget {
  const ProblemWorkspaceScreen({
    super.key,
    required this.source,
    required this.session,
    required this.patientId,
    this.consultationId,
    this.allowWrite = true,
  });
  final ProblemSource source;
  final ProblemSession session;
  final int patientId;
  final int? consultationId;
  final bool allowWrite;
  @override
  State<ProblemWorkspaceScreen> createState() => _ProblemWorkspaceScreenState();
}

class _ProblemWorkspaceScreenState extends State<ProblemWorkspaceScreen> {
  String _filter = 'current';
  final List<int> _cursors = [0];
  ProblemPage? _page;
  ProblemRecord? _selected;
  ProblemHistory? _history;
  String? _editAction, _message;
  bool _busy = false;
  int _generation = 0;
  bool get _active => widget.session.current;
  bool get _canWrite =>
      _active &&
      widget.allowWrite &&
      widget.session.canWrite &&
      !widget.source.writesHeld &&
      !_busy &&
      _page != null;
  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_invalidate);
    unawaited(_load());
  }

  @override
  void dispose() {
    _generation++;
    widget.source.guard.removeListener(_invalidate);
    super.dispose();
  }

  void _invalidate() {
    _generation++;
    if (mounted) {
      setState(() {
        _page = null;
        _selected = null;
        _history = null;
        _editAction = null;
        _message = null;
        _busy = false;
      });
    }
  }

  bool _current(int g) => mounted && _active && g == _generation;
  Future<void> _load({bool reset = false, bool preserveMessage = false}) async {
    if (!_active || _busy) {
      return;
    }
    final g = ++_generation;
    setState(() {
      if (reset) {
        _cursors.clear();
        _cursors.add(0);
      }
      _busy = true;
      _page = null;
      _selected = null;
      _history = null;
      _editAction = null;
      if (!preserveMessage) {
        _message = null;
      }
    });
    try {
      final page = await widget.session.list(
        widget.patientId,
        filter: _filter,
        after: _cursors.last,
        limit: 25,
      );
      if (_current(g)) {
        setState(() {
          _page = page;
          _busy = false;
        });
      }
    } on Object catch (e) {
      if (_current(g)) {
        setState(() {
          _message = _failure(e);
          _busy = false;
        });
      }
    }
  }

  Future<void> _detail(ProblemRecord row) async {
    if (_busy || !_active) {
      return;
    }
    final g = ++_generation;
    setState(() {
      _selected = null;
      _history = null;
      _editAction = null;
      _busy = true;
      _message = null;
    });
    try {
      final detail = await widget.session.detail(widget.patientId, row.id);
      if (_current(g)) {
        setState(() {
          _selected = detail;
          _busy = false;
        });
      }
    } on Object catch (e) {
      if (_current(g)) {
        setState(() {
          _page = null;
          _busy = false;
          _message = _failure(e);
        });
      }
    }
  }

  Future<void> _loadHistory() async {
    final row = _selected;
    if (row == null || _busy || !_active) {
      return;
    }
    final g = ++_generation;
    setState(() {
      _busy = true;
      _history = null;
      _message = null;
    });
    try {
      final h = await widget.session.history(row);
      if (_current(g)) {
        setState(() {
          _history = h;
          _busy = false;
        });
      }
    } on Object catch (e) {
      if (_current(g)) {
        setState(() {
          _selected = null;
          _page = null;
          _message = _failure(e);
          _busy = false;
        });
      }
    }
  }

  Future<void> _save(
    ProblemContent? content,
    String reason,
    int? consultationId,
  ) async {
    final action = _editAction;
    if (!_canWrite || action == null) {
      return;
    }
    final prior = _selected;
    final g = ++_generation;
    setState(() {
      _busy = true;
      _message = null;
      _editAction = null;
      _history = null;
    });
    try {
      if (action == 'create' && content != null) {
        await widget.session.create(
          widget.patientId,
          content,
          consultationId: consultationId,
        );
      } else if (prior != null) {
        await widget.session.change(
          prior,
          action,
          reason,
          content: content,
          consultationId: consultationId,
        );
      } else {
        throw const FormatException('No reviewed problem.');
      }
      if (!_current(g)) {
        return;
      }
      setState(() {
        _busy = false;
        _message = 'Server confirmed the clinical problem operation.';
      });
      await _load(reset: true, preserveMessage: true);
    } on Object catch (e) {
      if (_current(g)) {
        setState(() {
          _page = null;
          _selected = null;
          _busy = false;
          _message = _failure(e);
        });
      }
    }
  }

  Widget _content(ProblemRecord row) {
    final c = row.content;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(c.name, style: Theme.of(context).textTheme.titleMedium),
        Text(
          'Category: ${problemLabel(c.category)} · Clinical: ${problemLabel(c.clinicalStatus)} · Verification: ${problemLabel(c.verificationStatus)}',
        ),
        Text(
          'Code: ${c.code == null ? 'Not recorded' : '${_codingLabel(c.codingSystem!)} ${c.code}'}',
        ),
        Text('Severity: ${c.severity ?? 'Not recorded'}'),
        Text(
          'Onset: ${c.onsetDate ?? 'Not recorded'}${c.onsetDescription == null ? '' : ' · ${c.onsetDescription}'}',
        ),
        if (c.resolutionDate != null) Text('Resolved on: ${c.resolutionDate}'),
        if (c.notes != null && c.notes!.isNotEmpty)
          Text('Clinical notes: ${c.notes}'),
        Text('Problem #${row.id} · Version ${row.version}'),
        Text('Recorded: ${row.recordedAt} · Staff #${row.recordedBy}'),
        Text('Last reviewed: ${row.reviewedAt} · Staff #${row.reviewedBy}'),
        if (row.confirmedBy != null)
          Text('Confirmed: ${row.confirmedAt} · Staff #${row.confirmedBy}'),
        Text(
          'Associated consultations: ${row.consultationIds.isEmpty ? 'None' : row.consultationIds.join(', ')}',
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final page = _page,
        selected = _selected,
        history = _history,
        action = _editAction;
    return Scaffold(
      appBar: AppBar(title: const Text('Clinical problem list')),
      body: !_active
          ? const Center(
              child: Text(
                'Session changed. Clinical problem information cleared.',
              ),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Patient #${widget.patientId}${widget.consultationId == null ? '' : ' · Consultation #${widget.consultationId}'}',
                ),
                const Text(
                  'Staged clinical workspace. Backend authorization is authoritative. No automatic diagnosis promotion or treatment changes.',
                ),
                if (widget.source.outcomeUnconfirmed)
                  const Text(
                    'Unconfirmed write: review server state. Further problem writes are locked for this app session.',
                  ),
                if (!widget.session.canWrite || !widget.allowWrite)
                  const Text('Read-only problem list.'),
                if (_message != null)
                  Text(_message!, key: const ValueKey('problem-message')),
                if (_busy) const LinearProgressIndicator(),
                DropdownButton<String>(
                  value: _filter,
                  items: const [
                    DropdownMenuItem(
                      value: 'current',
                      child: Text('Current problems'),
                    ),
                    DropdownMenuItem(
                      value: 'historical',
                      child: Text('Historical problems'),
                    ),
                    DropdownMenuItem(
                      value: 'resolved',
                      child: Text('Resolved problems'),
                    ),
                    DropdownMenuItem(value: 'all', child: Text('All problems')),
                  ],
                  onChanged: _busy || action != null
                      ? null
                      : (v) {
                          if (v != null) {
                            setState(() => _filter = v);
                            unawaited(_load(reset: true));
                          }
                        },
                ),
                OutlinedButton(
                  onPressed: _busy || action != null
                      ? null
                      : () => unawaited(_load(reset: true)),
                  child: const Text('Refresh problem list'),
                ),
                if (page != null) ...[
                  if (_canWrite && action == null)
                    FilledButton(
                      key: const ValueKey('problem-create'),
                      onPressed: () => setState(() {
                        _selected = null;
                        _history = null;
                        _editAction = 'create';
                      }),
                      child: const Text('Create individual problem'),
                    ),
                  if (page.items.isEmpty)
                    const Text('No records returned for this filter.'),
                  for (final row in page.items)
                    Card(
                      child: ListTile(
                        key: ValueKey('problem-row-${row.id}'),
                        title: Text(row.content.name),
                        subtitle: Text(
                          '${problemLabel(row.content.category)} · ${problemLabel(row.content.clinicalStatus)} · ${problemLabel(row.content.verificationStatus)}',
                        ),
                        onTap: _busy || action != null
                            ? null
                            : () => unawaited(_detail(row)),
                      ),
                    ),
                  Wrap(
                    spacing: 8,
                    children: [
                      OutlinedButton(
                        onPressed:
                            _busy || action != null || _cursors.length == 1
                            ? null
                            : () {
                                _cursors.removeLast();
                                unawaited(_load());
                              },
                        child: const Text('Previous page'),
                      ),
                      OutlinedButton(
                        onPressed:
                            _busy || action != null || page.nextAfter == null
                            ? null
                            : () {
                                _cursors.add(page.nextAfter!);
                                unawaited(_load());
                              },
                        child: const Text('Next page'),
                      ),
                    ],
                  ),
                ],
                if (selected != null) ...[
                  const Divider(),
                  _content(selected),
                  if (GoRouter.maybeOf(context) != null)
                    for (final id in selected.consultationIds)
                      TextButton(
                        onPressed: _busy || action != null
                            ? null
                            : () => context.push<void>(
                                '/consultations/patient/${widget.patientId}/$id',
                              ),
                        child: Text('Open consultation #$id'),
                      ),
                  OutlinedButton(
                    onPressed: _busy || action != null
                        ? null
                        : () => unawaited(_loadHistory()),
                    child: const Text('Review amendment history'),
                  ),
                  if (_canWrite && !selected.content.terminal && action == null)
                    Wrap(
                      spacing: 8,
                      children: [
                        for (final a in [
                          'review',
                          'update',
                          'transition',
                          'correct',
                          'associate',
                        ])
                          OutlinedButton(
                            key: ValueKey('problem-action-$a'),
                            onPressed: () => setState(() {
                              _history = null;
                              _editAction = a;
                            }),
                            child: Text(switch (a) {
                              'review' => 'Record review',
                              'update' => 'Update details',
                              'transition' => 'Change status',
                              'correct' => 'Controlled correction',
                              'associate' => 'Associate consultation',
                              _ => a,
                            }),
                          ),
                      ],
                    ),
                  if (selected.content.terminal)
                    const Text(
                      'Refuted or entered-in-error record: history retained; changes prohibited.',
                    ),
                ],
                if (history != null) ...[
                  const Divider(),
                  const Text('Original clinical record'),
                  _content(history.original),
                  if (history.amendments.isEmpty) const Text('No amendments.'),
                  for (final a in history.amendments) ...[
                    const Divider(),
                    Text(
                      'Amendment v${a.snapshot.version} · ${a.at} · Staff #${a.actor}',
                    ),
                    Text('Reason: ${a.reason}'),
                    _content(a.snapshot),
                  ],
                ],
                if (action != null && _canWrite) ...[
                  const Divider(),
                  _ProblemEditForm(
                    key: ValueKey(
                      'problem-edit-$action-${selected?.id}-${selected?.version}',
                    ),
                    action: action,
                    prior: selected?.content,
                    consultationId: widget.consultationId,
                    onCancel: () => setState(() => _editAction = null),
                    onSave: _save,
                  ),
                ],
              ],
            ),
    );
  }
}

class _ProblemEditForm extends StatefulWidget {
  const _ProblemEditForm({
    super.key,
    required this.action,
    required this.prior,
    required this.consultationId,
    required this.onCancel,
    required this.onSave,
  });
  final String action;
  final ProblemContent? prior;
  final int? consultationId;
  final VoidCallback onCancel;
  final Future<void> Function(ProblemContent?, String, int?) onSave;
  @override
  State<_ProblemEditForm> createState() => _ProblemEditFormState();
}

class _ProblemEditFormState extends State<_ProblemEditForm> {
  final _form = GlobalKey<FormState>();
  late final _name = TextEditingController(text: widget.prior?.name ?? '');
  late final _code = TextEditingController(text: widget.prior?.code ?? '');
  late final _onset = TextEditingController(
    text: widget.prior?.onsetDate ?? '',
  );
  late final _approx = TextEditingController(
    text: widget.prior?.onsetDescription ?? '',
  );
  late final _resolution = TextEditingController(
    text: widget.prior?.resolutionDate ?? '',
  );
  late final _notes = TextEditingController(text: widget.prior?.notes ?? '');
  final _reason = TextEditingController();
  late final _consultation = TextEditingController(
    text: widget.consultationId?.toString() ?? '',
  );
  late String? _category = widget.prior?.category,
      _status = widget.prior?.clinicalStatus,
      _verification = widget.prior?.verificationStatus,
      _severity = widget.prior?.severity,
      _system = widget.prior?.codingSystem;
  bool _associate = false, _confirmed = false;
  String? _error;
  bool get _creating => widget.action == 'create';
  bool get _identity => _creating || widget.action == 'correct';
  bool get _details => widget.action != 'transition';
  bool get _statuses => widget.action != 'update';
  bool get _content => !const {'review', 'associate'}.contains(widget.action);
  @override
  void dispose() {
    for (final c in [
      _name,
      _code,
      _onset,
      _approx,
      _resolution,
      _notes,
      _reason,
      _consultation,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  Widget _field(
    String label,
    TextEditingController c,
    int max, {
    bool enabled = true,
    bool mandatory = false,
    int lines = 1,
  }) => TextFormField(
    controller: c,
    enabled: enabled,
    maxLength: max,
    maxLines: lines,
    decoration: InputDecoration(labelText: label),
    validator: (v) => mandatory && (v ?? '').trim().isEmpty ? 'Required' : null,
  );
  Widget _choice(
    String label,
    String? value,
    List<String> options,
    ValueChanged<String?> onChanged, {
    bool mandatory = false,
    bool enabled = true,
    String Function(String)? format,
  }) => DropdownButtonFormField<String>(
    key: ValueKey('$label-$value'),
    initialValue: value,
    decoration: InputDecoration(labelText: label),
    isExpanded: true,
    items: [
      if (!mandatory)
        const DropdownMenuItem<String>(
          value: null,
          child: Text('Not recorded'),
        ),
      for (final item in options)
        DropdownMenuItem(
          value: item,
          child: Text(format?.call(item) ?? problemLabel(item)),
        ),
    ],
    onChanged: enabled ? onChanged : null,
    validator: (v) => mandatory && v == null ? 'Select a value' : null,
  );
  String? _optionalText(TextEditingController c) =>
      c.text.trim().isEmpty ? null : c.text.trim();
  void _submit() {
    if (!_confirmed || !(_form.currentState?.validate() ?? false)) {
      return;
    }
    try {
      final needsLink =
          widget.action == 'associate' || (_creating && _associate);
      final id = needsLink ? int.tryParse(_consultation.text.trim()) : null;
      if (needsLink && (id == null || id <= 0 || id > 2147483647)) {
        throw const FormatException();
      }
      final content = _content
          ? ProblemContent.fromJson({
              'name': _name.text.trim(),
              'code': _optionalText(_code),
              'coding_system': _system,
              'category': _category,
              'clinical_status': _status,
              'verification_status': _verification,
              'severity': _severity,
              'onset_date': _optionalText(_onset),
              'onset_description': _optionalText(_approx),
              'resolution_date': _status == 'resolved'
                  ? _optionalText(_resolution)
                  : null,
              'notes': widget.prior?.notes == _notes.text.trim()
                  ? widget.prior?.notes
                  : _optionalText(_notes),
            })
          : null;
      if (content != null) {
        final today = DateTime.now().toUtc().toIso8601String().substring(0, 10);
        if ([
          content.onsetDate,
          content.resolutionDate,
        ].any((v) => v != null && v.compareTo(today) > 0)) {
          throw const FormatException();
        }
      }
      unawaited(widget.onSave(content, _reason.text.trim(), id));
    } on FormatException {
      setState(
        () => _error = 'Check code and coding system, dates, required values and consultation ID.',
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final old = widget.prior;
    final statusOptions = widget.action == 'transition' && old != null
        ? [old.clinicalStatus, ...problemTransitions[old.clinicalStatus]!]
        : problemStatuses;
    final verificationOptions = _creating
        ? verificationStatuses
              .where((v) => !terminalProblemVerifications.contains(v))
              .toList()
        : widget.action == 'transition'
        ? verificationStatuses.where((v) => v != 'entered_in_error').toList()
        : verificationStatuses;
    return Form(
      key: _form,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${problemLabel(widget.action)} clinical problem',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          if (_content) ...[
            _field(
              'Clinical problem name',
              _name,
              255,
              enabled: _identity,
              mandatory: true,
            ),
            _field('Condition code (optional)', _code, 64, enabled: _identity),
            _choice(
              'Coding system',
              _system,
              problemCodingSystems,
              (v) => setState(() => _system = v),
              enabled: _identity,
              format: _codingLabel,
            ),
            _choice(
              'Category',
              _category,
              problemCategories,
              (v) => setState(() => _category = v),
              enabled: _details,
              mandatory: true,
            ),
            _choice(
              'Clinical status',
              _status,
              statusOptions,
              (v) => setState(() => _status = v),
              enabled: _statuses,
              mandatory: true,
            ),
            _choice(
              'Verification status',
              _verification,
              verificationOptions,
              (v) => setState(() => _verification = v),
              enabled: _statuses,
              mandatory: true,
            ),
            _choice(
              'Severity',
              _severity,
              problemSeverities,
              (v) => setState(() => _severity = v),
              enabled: _details,
            ),
            _field(
              'Onset date (YYYY-MM-DD, optional)',
              _onset,
              10,
              enabled: _details,
            ),
            _field(
              'Approximate onset (optional)',
              _approx,
              255,
              enabled: _details,
            ),
            if (_status == 'resolved')
              _field(
                'Resolution date (YYYY-MM-DD)',
                _resolution,
                10,
                mandatory: true,
              ),
            _field('Clinical notes', _notes, 4000, enabled: _details, lines: 3),
          ],
          if (_creating)
            CheckboxListTile(
              title: const Text('Explicitly associate with a consultation'),
              value: _associate,
              onChanged: (v) => setState(() => _associate = v ?? false),
            ),
          if (widget.action == 'associate' || (_creating && _associate))
            _field(
              'Existing consultation ID',
              _consultation,
              10,
              mandatory: true,
            ),
          if (!_creating)
            _field(
              'Clinical reason for this operation',
              _reason,
              1000,
              mandatory: true,
              lines: 2,
            ),
          const Text(
            'This operation creates or updates the longitudinal problem record only. It does not change treatment, medication orders, or the consultation diagnosis.',
          ),
          CheckboxListTile(
            title: const Text(
              'I have reviewed the clinical information and intend this operation',
            ),
            value: _confirmed,
            onChanged: (v) => setState(() => _confirmed = v ?? false),
          ),
          if (_error != null) Text(_error!),
          Wrap(
            spacing: 8,
            children: [
              TextButton(
                onPressed: widget.onCancel,
                child: const Text('Cancel'),
              ),
              FilledButton(
                key: const ValueKey('problem-submit'),
                onPressed: _confirmed ? _submit : null,
                child: const Text('Submit once'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
