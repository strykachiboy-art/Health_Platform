import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';

import '../data/sa_workspace_contract.dart';
import '../data/sa_workspace_remote.dart';

/// Online-only extension of the existing administration hub. The server owns
/// authorization. No new queue, session store, cache, or offline snapshot.
class SaWorkspaceScreen extends StatefulWidget {
  const SaWorkspaceScreen({super.key, required this.source});
  final SaWorkspaceRemote source;
  @override
  State<SaWorkspaceScreen> createState() => _SaWorkspaceScreenState();
}

class _SaWorkspaceScreenState extends State<SaWorkspaceScreen> {
  SaSession? _session;
  SaOperation _operation = SaOperation.operations;
  final _fields = <String, TextEditingController>{};
  final _choices = <String, String>{};
  SaReview? _review;
  SaResult? _result;
  String? _error;
  bool _busy = true;
  int _generation = 0;
  static const _encoder = JsonEncoder.withIndent('  ');

  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_invalidate);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant SaWorkspaceScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source.guard, widget.source.guard)) {
      oldWidget.source.guard.removeListener(_invalidate);
      widget.source.guard.addListener(_invalidate);
    }
    if (!identical(oldWidget.source, widget.source)) {
      _invalidate();
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    widget.source.guard.removeListener(_invalidate);
    _generation++;
    for (final c in _fields.values) {
      c.dispose();
    }
    super.dispose();
  }

  void _clearFields() {
    for (final c in _fields.values) {
      c.clear();
    }
    _choices.clear();
  }

  void _invalidate() {
    if (!mounted) return;
    _generation++;
    _clearFields();
    setState(() {
      _session = null;
      _review = null;
      _result = null;
      _error = null;
      _busy = false;
    });
  }

  bool _valid(int g, SaSession s) =>
      mounted &&
      g == _generation &&
      widget.source.current(s) &&
      identical(_session, s);
  Future<void> _load() async {
    final g = ++_generation;
    setState(() {
      _busy = true;
      _session = null;
      _result = null;
      _review = null;
      _error = null;
    });
    try {
      final s = await widget.source.session();
      if (!mounted || g != _generation || !widget.source.current(s)) return;
      setState(() {
        _session = s;
        _operation = (widget.source.adminMode || widget.source.doctorMode)
            ? SaOperation.staffMemberships
            : SaOperation.operations;
        _busy = false;
      });
    } on Object {
      if (mounted && g == _generation) {
        setState(() {
          _busy = false;
          _error = widget.source.doctorMode
              ? 'An authorized Doctor session is required.'
              : widget.source.adminMode
              ? 'An authorized clinic Admin session is required.'
              : 'An authorized Super Admin session is required.';
        });
      }
    }
  }

  void _select(SaOperation? next) {
    if (next == null || _busy || _review != null) return;
    _clearFields();
    setState(() {
      _operation = next;
      _result = null;
      _error = null;
    });
  }

  String get _workspaceTitle => widget.source.doctorMode
      ? 'Doctor clinic resources and personal records'
      : widget.source.adminMode
      ? 'Clinic administration workspace'
      : 'Super Admin workspace';

  String _title(SaOperation op) {
    if (!widget.source.doctorMode) return op.title;
    return switch (op) {
      SaOperation.excuses => 'My filtered excuses',
      SaOperation.excuseDetail => 'My excuse details',
      SaOperation.excuseCreate => 'Submit my excuse',
      SaOperation.leaves => 'My leave requests',
      SaOperation.leaveDetail => 'My leave details',
      _ => op.title,
    };
  }

  Iterable<SaField> get _visibleFields => _operation.fields.where((f) =>
      !(widget.source.doctorMode &&
          (_operation == SaOperation.excuses || _operation == SaOperation.leaves) &&
          f.key == 'staff_id'));

  Map<String, String> _values() => {
    for (final f in _visibleFields)
      f.key: f.kind == SaKind.choice
          ? _choices[f.key] ?? ''
          : _fields[f.key]?.text ?? '',
  };
  Future<void> _request() async {
    final s = _session;
    if (_busy || s == null || !widget.source.current(s)) return;
    final g = ++_generation;
    final op = _operation;
    final fields = _values();
    setState(() {
      _busy = true;
      _error = null;
      _result = null;
      _review = null;
    });
    try {
      if (op.writes) {
        final r = await widget.source.review(s, op, fields);
        if (_valid(g, s)) setState(() => _review = r);
      } else {
        final r = await widget.source.read(s, op, fields);
        if (_valid(g, s)) setState(() => _result = r);
      }
    } on FormatException catch (e) {
      if (_valid(g, s)) setState(() => _error = e.message);
    } on Object {
      if (_valid(g, s)) {
        setState(
          () => _error = 'Request unavailable or denied by the server. Check context and access.',
        );
      }
    } finally {
      if (_valid(g, s)) setState(() => _busy = false);
    }
  }

  Future<void> _submit() async {
    final s = _session;
    final review = _review;
    if (_busy || s == null || review == null || !widget.source.current(s)) {
      return;
    }
    final g = ++_generation;
    setState(() {
      _busy = true;
      _review = null;
      _result = null;
      _error = null;
    });
    try {
      final r = await widget.source.submit(review);
      if (_valid(g, s)) {
        setState(() {
          _result = r;
          _clearFields();
        });
      }
    } on Object {
      if (_valid(g, s)) {
        setState(
          () => _error = 'Change not confirmed. It may have reached the server; inspect the server record before further action. No automatic retry.',
        );
      }
    } finally {
      if (_valid(g, s)) setState(() => _busy = false);
    }
  }

  Widget _record(Map<String, dynamic> data) => Card(
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final e in data.entries)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Text(
                '${e.key.replaceAll('_', ' ')}: ${e.value == null
                    ? '—'
                    : e.value is Map || e.value is List
                    ? _encoder.convert(e.value)
                    : e.value}',
              ),
            ),
        ],
      ),
    ),
  );
  Widget _field(SaField f) {
    final enabled = !_busy && _review == null;
    if (f.kind == SaKind.choice) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(f.label),
          DropdownButton<String>(
            key: ValueKey('sa-field-${f.key}'),
            isExpanded: true,
            value: _choices[f.key],
            hint: Text(f.required ? 'Choose one' : 'Optional'),
            items: [
              if (!f.required)
                const DropdownMenuItem(value: '', child: Text('Not specified')),
              for (final option in f.choices)
                DropdownMenuItem(value: option, child: Text(option)),
            ],
            onChanged: enabled
                ? (v) => setState(() => _choices[f.key] = v ?? '')
                : null,
          ),
        ],
      );
    }
    final c = _fields.putIfAbsent(f.key, TextEditingController.new);
    return TextField(
      key: ValueKey('sa-field-${f.key}'),
      controller: c,
      enabled: enabled,
      decoration: InputDecoration(
        labelText: f.label,
        helperText: f.required ? 'Required' : null,
      ),
      maxLines: f.kind == SaKind.object || f.kind == SaKind.salaries ? 4 : 1,
      keyboardType: f.kind == SaKind.id || f.kind == SaKind.integer
          ? TextInputType.number
          : TextInputType.text,
    );
  }

  @override
  Widget build(BuildContext context) {
    final s = _session;
    if (s == null || !widget.source.current(s)) {
      return Scaffold(
        appBar: AppBar(
          title: Text(_workspaceTitle),
        ),
        body: Center(
          child: _busy
              ? const CircularProgressIndicator()
              : Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      _error ?? 'Administration session unavailable.',
                      key: const ValueKey('sa-unavailable'),
                    ),
                    TextButton(
                      onPressed: () => unawaited(_load()),
                      child: const Text('Check session'),
                    ),
                  ],
                ),
        ),
      );
    }
    final available = SaOperation.values
        .where((op) => widget.source.allows(s, op))
        .toList();
    final review = _review;
    final result = _result;
    return Scaffold(
      appBar: AppBar(
          title: Text(_workspaceTitle),
        ),
      body: ListView(
        key: const ValueKey('sa-workspace'),
        padding: const EdgeInsets.all(20),
        children: [
          Text(
            s.clinic == null
                ? 'Platform scope — no clinic selected'
                : 'Selected clinic: ${s.clinic}',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const Text(
            'Online only. Flask authorizes every request. Use existing directories and list operations for identifiers; confirm server records before changes.',
          ),
          if (widget.source.doctorMode)
            const Text(
              'Only authorized clinic records, personal staff records and ward resources are offered. HIE management, payroll, staff approvals, inventory and integration configuration remain unavailable here.',
            ),
          if (!widget.source.staging && !widget.source.doctorMode)
            const Text(
              'Release-held changes are available only in an explicitly approved staging build. Transfers are same-organization only. Secrets and clinical governance controls are not offered in this workspace.',
            ),
          if (s.clinic == null)
            const Text(
              'Select a clinic in your account before staff, leave, excuse or payroll operations.',
            ),
          const SizedBox(height: 16),
          DropdownButton<SaOperation>(
            key: const ValueKey('sa-operation'),
            isExpanded: true,
            value: _operation,
            items: [
              for (final op in available)
                DropdownMenuItem(
                  value: op,
                  child: Text(_title(op), overflow: TextOverflow.ellipsis),
                ),
            ],
            onChanged: _busy || review != null ? null : _select,
          ),
          Text(
            _operation.group,
            style: Theme.of(context).textTheme.titleMedium,
          ),
          if (_operation == SaOperation.rules)
            const Text(
              'Global rules, first server page. The current backend strict query schema does not accept numeric pagination strings; do not interpret this as all rules.',
            ),
          if (_operation == SaOperation.ruleCreate ||
              _operation == SaOperation.ruleUpdate)
            const Text(
              'Staging only. Enter clinician-approved configuration; the backend validates rule semantics. No hard-rule edits or clinic-rule writes are offered here.',
            ),
          if (_operation == SaOperation.myExcuses ||
              _operation == SaOperation.excuseCreate)
            const Text(
              'Requires an actual linked staff profile in the selected clinic. Platform context alone does not create that profile.',
            ),
          if (_operation == SaOperation.payrollGenerate)
            const Text(
              'Staging only. Enter approved exact salary amounts. Existing records may be skipped; this does not send payments.',
            ),
          for (final f in _visibleFields)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: _field(f),
            ),
          if (review == null)
            FilledButton(
              key: const ValueKey('sa-request'),
              onPressed: _busy ? null : () => unawaited(_request()),
              child: Text(_operation.writes ? 'Review change' : 'Load'),
            ),
          if (_busy) const LinearProgressIndicator(),
          if (review != null) ...[
            const Divider(),
            Text(
              'Review before sending',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            Text(_title(review.request.operation)),
            _record(review.request.values),
            for (final row in review.preview) _record(row),
            const Text(
              'Confirm only after checking these server records and exact inputs. A changed session or record cancels the request.',
            ),
            Wrap(
              spacing: 8,
              children: [
                OutlinedButton(
                  onPressed: _busy
                      ? null
                      : () => setState(() => _review = null),
                  child: const Text('Cancel'),
                ),
                FilledButton(
                  key: const ValueKey('sa-confirm'),
                  onPressed: _busy ? null : () => unawaited(_submit()),
                  child: const Text('Confirm change'),
                ),
              ],
            ),
          ],
          if (_error != null) Text(_error!, key: const ValueKey('sa-error')),
          if (result != null) ...[
            const Divider(),
            Text(
              result.note.isEmpty ? 'Server response' : result.note,
              key: const ValueKey('sa-result'),
            ),
            if (result.rows.isEmpty)
              const Text('No matching records returned.'),
            for (final row in result.rows) _record(row),
          ],
        ],
      ),
    );
  }
}
