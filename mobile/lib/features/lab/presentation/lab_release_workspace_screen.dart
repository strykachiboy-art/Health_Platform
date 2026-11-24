import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/lab/data/lab_release_remote_data_source.dart';

/// Standalone staged workspace; router and notification integration belong to Run B.
class LabReleaseWorkspaceScreen extends StatefulWidget {
  const LabReleaseWorkspaceScreen({
    super.key,
    required this.source,
    this.initialOrderId,
  });
  final LabReleaseRemoteDataSource source;
  final int? initialOrderId;
  @override
  State<LabReleaseWorkspaceScreen> createState() =>
      _LabReleaseWorkspaceScreenState();
}

class _LabReleaseWorkspaceScreenState extends State<LabReleaseWorkspaceScreen> {
  final _order = TextEditingController(),
      _value = TextEditingController(),
      _reason = TextEditingController();
  LabReleaseSession? _session;
  LabReleaseStatus? _status;
  LabReleasePreview? _preview;
  LabReleasePage? _page;
  LabReleasedReport? _detail;
  final _changes = <LabCorrection>[];
  int? _item;
  String _flag = 'normal',
      _withdrawal = 'clinical_correction',
      _rejection = 'incorrect_correction',
      _method = 'telephone';
  String? _message;
  bool _busy = false, _confirmed = false, _communicated = false;
  int _generation = 0;
  bool _initialUsed = false;
  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_reset);
    unawaited(_connect());
  }

  @override
  void didUpdateWidget(covariant LabReleaseWorkspaceScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source) ||
        oldWidget.initialOrderId != widget.initialOrderId) {
      oldWidget.source.guard.removeListener(_reset);
      widget.source.guard.addListener(_reset);
      _initialUsed = false;
      _reset();
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.guard.removeListener(_reset);
    for (final c in [_order, _value, _reason]) {
      c.dispose();
    }
    super.dispose();
  }

  bool _current(int g) =>
      mounted &&
      g == _generation &&
      widget.source.guard.canAccessProtectedRoutes;
  void _clearDecision() {
    _status = null;
    _preview = null;
    _detail = null;
    _changes.clear();
    _item = null;
    _value.clear();
    _reason.clear();
    _confirmed = false;
    _communicated = false;
  }

  void _reset() {
    if (!mounted) {
      return;
    }
    setState(() {
      _generation++;
      _session = null;
      _page = null;
      _clearDecision();
      _order.clear();
      _busy = false;
      _message = null;
    });
    if (widget.source.guard.canAccessProtectedRoutes) {
      unawaited(_connect());
    }
  }

  Future<void> _connect() async {
    final g = _generation;
    try {
      final s = await widget.source.connect();
      if (!_current(g)) {
        return;
      }
      setState(() => _session = s);
      final initialId = _initialUsed ? null : widget.initialOrderId;
      _initialUsed = true;
      if (s.role == 'patient') {
        final id = initialId;
        if (id != null) {
          await _patientDetail(id);
        } else {
          await _patientPage(1);
        }
      } else if (initialId != null) {
        _order.text = '$initialId';
        await _loadOrder();
      }
    } on Object {
      if (_current(g)) {
        setState(() => _message = 'Laboratory release session unavailable.');
      }
    }
  }

  String _error(Object e) {
    if (e is LabReleaseUncertain) {
      return 'Outcome unconfirmed. Further writes are held. Reconcile with the clinic before another attempt.';
    }
    if (e is LabReleaseUnavailable) {
      return 'Session or action unavailable. Reopen the record.';
    }
    if (e is ApiException) {
      if (e.statusCode == 503) {
        return 'Patient laboratory release is currently held by the backend.';
      }
      if (e.statusCode == 409) {
        return 'Report unavailable or changed. Additional clinical review may be required.';
      }
      if (e.statusCode == 403 || e.statusCode == 404) {
        return 'Laboratory report unavailable or not authorized.';
      }
      if (e.statusCode == 422) {
        return 'The requested clinical decision was not accepted.';
      }
    }
    return 'Laboratory response unavailable. No action will be retried automatically.';
  }

  Future<void> _perform(
    Future<void> Function(LabReleaseSession, int) action,
  ) async {
    final s = _session;
    if (s == null || _busy) {
      return;
    }
    final g = _generation;
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await s.check();
      if (!_current(g)) {
        return;
      }
      await action(s, g);
    } on Object catch (e) {
      if (_current(g)) {
        setState(() {
          _clearDecision();
          _page = null;
          _message = _error(e);
          if (e is LabReleaseUnavailable) {
            _session = null;
          }
        });
      }
    } finally {
      if (_current(g)) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _loadOrder() => _perform((s, g) async {
    final id = int.tryParse(_order.text.trim());
    if (id == null || id <= 0 || id > 2147483647) {
      throw const FormatException();
    }
    setState(_clearDecision);
    final result = await s.status(id);
    if (_current(g)) {
      setState(() => _status = result);
    }
  });
  Future<void> _loadPreview(LabPreviewKind kind) => _perform((s, g) async {
    final status = _status;
    if (status == null) {
      return;
    }
    setState(() {
      _preview = null;
      _confirmed = false;
      _communicated = false;
    });
    final result = await s.preview(status.orderId, kind);
    if (_current(g)) {
      setState(() => _preview = result);
    }
  });
  Future<void> _patientPage(int page) => _perform((s, g) async {
    setState(() {
      _page = null;
      _detail = null;
    });
    final result = await s.patientPage(page);
    if (_current(g)) {
      setState(() => _page = result);
    }
  });
  Future<void> _patientDetail(int id) => _perform((s, g) async {
    setState(() => _detail = null);
    final result = await s.patientDetail(id);
    if (_current(g)) {
      setState(() => _detail = result);
    }
  });
  Future<void> _decision(Future<void> Function(LabReleaseSession) submit) =>
      _perform((s, g) async {
        // The exact preview / status and form values are captured by the button callback.
        await submit(s);
        if (_current(g)) {
          setState(() {
            _clearDecision();
            _message = 'Decision accepted by the backend. Reload the order to review current status.';
          });
        }
      });
  void _addChange() {
    final id = _item;
    final value = _value.text.trim();
    if (id == null ||
        value.isEmpty ||
        value.length > 150 ||
        _changes.length >= 100) {
      return;
    }
    setState(() {
      _changes.removeWhere((c) => c.itemId == id);
      _changes.add(LabCorrection(itemId: id, value: value, flag: _flag));
      _value.clear();
      _confirmed = false;
    });
  }

  Widget _report(LabReleasedReport r) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        'Report ${r.orderId} · Version ${r.version}',
        style: Theme.of(context).textTheme.titleLarge,
      ),
      if (_session?.role == 'doctor')
        Text(
          'Patient record ${r.patientId} — confirm identity before release.',
        ),
      Text('Verified: ${r.verifiedAt}'),
      Text('Completed: ${r.completedAt}'),
      if (r.correctionVerifiedAt != null)
        Text('Correction verified: ${r.correctionVerifiedAt}'),
      if (r.releasedAt != null) Text('Released: ${r.releasedAt}'),
      if (r.critical)
        const Text(
          'Critical result — follow the clinical instructions given by your care team.',
        ),
      for (final item in r.results)
        Card(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  item['test_name']!,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                Text(
                  'Result: ${item['result_value']} ${item['unit'] ?? "(unit not recorded)"}',
                ),
                Text(
                  'Reference range: ${item['reference_range'] ?? "Not recorded"}',
                ),
                Text('Laboratory flag: ${item['flag']}'),
                Text('LOINC: ${item['loinc_code'] ?? "Not recorded"}'),
                Text('Recorded: ${item['resulted_at']}'),
              ],
            ),
          ),
        ),
    ],
  );
  Widget _choices(
    String label,
    String value,
    Set<String> choices,
    ValueChanged<String> changed,
  ) => Row(
    children: [
      Expanded(child: Text(label)),
      Expanded(
        child: DropdownButton<String>(
          isExpanded: true,
          value: value,
          items: [
            for (final x in choices)
              DropdownMenuItem(value: x, child: Text(x.replaceAll('_', ' '))),
          ],
          onChanged: _busy
              ? null
              : (x) {
                  if (x != null) {
                    setState(() {
                      changed(x);
                      _confirmed = false;
                      _communicated = false;
                    });
                  }
                },
        ),
      ),
    ],
  );
  Widget _confirm(String label) => CheckboxListTile(
    value: _confirmed,
    onChanged: _busy ? null : (v) => setState(() => _confirmed = v ?? false),
    title: Text(label),
    controlAffinity: ListTileControlAffinity.leading,
  );
  Widget _staff() {
    final s = _status, p = _preview;
    final writable = !_busy && !widget.source.writesHeld;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          key: const ValueKey('lab-release-order'),
          controller: _order,
          enabled: !_busy,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(
            labelText: 'Existing laboratory order ID',
          ),
          onChanged: (_) => setState(_clearDecision),
        ),
        OutlinedButton(
          onPressed: _busy ? null : _loadOrder,
          child: const Text('Load release status'),
        ),
        if (s != null) ...[
          Text('Order ${s.orderId} · ${s.state} · Version ${s.version}'),
          if (s.pending)
            const Text('Verified correction awaiting Doctor decision.'),
          if (s.rejected)
            const Text('Correction rejected. Original evidence is preserved.'),
          if (_session?.role == 'doctor') ...[
            if (s.state == 'unreleased')
              Wrap(
                spacing: 8,
                children: [
                  OutlinedButton(
                    onPressed: _busy
                        ? null
                        : () => _loadPreview(LabPreviewKind.initial),
                    child: const Text('Review initial report'),
                  ),
                  OutlinedButton(
                    onPressed: _busy
                        ? null
                        : () => _loadPreview(LabPreviewKind.critical),
                    child: const Text('Review critical report'),
                  ),
                ],
              ),
            if (s.pending)
              OutlinedButton(
                onPressed: _busy
                    ? null
                    : () => _loadPreview(LabPreviewKind.replacement),
                child: const Text('Review corrected report'),
              ),
            if (s.state == 'released') ...[
              _choices(
                'Withdrawal reason',
                _withdrawal,
                labWithdrawalReasons,
                (v) => _withdrawal = v,
              ),
              _confirm(
                'I confirm withdrawal of the currently approved report.',
              ),
              FilledButton(
                onPressed: writable && _confirmed
                    ? () {
                        final reason = _withdrawal;
                        unawaited(
                          _decision(
                            (a) => a.withdraw(s, reason, confirmed: true),
                          ),
                        );
                      }
                    : null,
                child: const Text('Withdraw report'),
              ),
            ],
            if (p != null) ...[
              _report(p.report),
              if (p.kind == LabPreviewKind.critical) ...[
                _choices(
                  'Direct communication method',
                  _method,
                  labCommunicationMethods,
                  (v) => _method = v,
                ),
                CheckboxListTile(
                  value: _communicated,
                  onChanged: _busy
                      ? null
                      : (v) => setState(() {
                          _communicated = v ?? false;
                          _confirmed = false;
                        }),
                  title: const Text(
                    'I have directly communicated the critical result to the patient or authorized recipient. A notification alone is not sufficient.',
                  ),
                ),
              ],
              if (p.kind == LabPreviewKind.replacement)
                _choices(
                  'Rejection reason',
                  _rejection,
                  labRejectionReasons,
                  (v) => _rejection = v,
                ),
              _confirm(
                'I reviewed the exact report and confirm my selected clinical decision.',
              ),
              FilledButton(
                onPressed:
                    writable &&
                        _confirmed &&
                        (p.kind != LabPreviewKind.critical || _communicated)
                    ? () {
                        final method = p.kind == LabPreviewKind.critical
                            ? _method
                            : null;
                        unawaited(
                          _decision(
                            (a) => a.publish(
                              p,
                              confirmed: true,
                              communicationMethod: method,
                            ),
                          ),
                        );
                      }
                    : null,
                child: const Text('Authorize patient release'),
              ),
              if (p.kind == LabPreviewKind.replacement)
                OutlinedButton(
                  onPressed: writable && _confirmed
                      ? () {
                          final reason = _rejection;
                          unawaited(
                            _decision(
                              (a) => a.reject(p, reason, confirmed: true),
                            ),
                          );
                        }
                      : null,
                  child: const Text('Reject correction'),
                ),
            ],
          ] else if (s.state == 'withdrawn' &&
              !s.pending &&
              s.items.isNotEmpty) ...[
            Text(
              'Patient record ${s.patientId} — confirm identity before verifying a correction.',
            ),
            const Text(
              'Correction verification — use the original order item identity and independently verified corrected values. Rejected values are not carried forward. Safety-review holds are enforced by the backend.',
            ),
            DropdownButton<int>(
              key: const ValueKey('lab-correction-item'),
              isExpanded: true,
              value: _item,
              hint: const Text('Select original test item'),
              items: [
                for (final i in s.items)
                  DropdownMenuItem(
                    value: i.id,
                    child: Text(
                      '${i.name} (#${i.id}) — original ${i.originalValue ?? "unavailable"}',
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
              ],
              onChanged: _busy
                  ? null
                  : (v) => setState(() {
                      _item = v;
                      _value.clear();
                      _confirmed = false;
                    }),
            ),
            TextField(
              controller: _value,
              enabled: !_busy,
              maxLength: 150,
              decoration: const InputDecoration(
                labelText: 'Verified corrected value',
              ),
              onChanged: (_) => setState(() => _confirmed = false),
            ),
            _choices('Verified flag', _flag, {
              'normal',
              'abnormal',
            }, (v) => _flag = v),
            OutlinedButton(
              onPressed: _busy ? null : _addChange,
              child: const Text('Add or replace correction item'),
            ),
            for (final c in _changes)
              ListTile(
                title: Text('Item ${c.itemId}: ${c.value} (${c.flag})'),
                trailing: IconButton(
                  onPressed: _busy
                      ? null
                      : () => setState(() {
                          _changes.remove(c);
                          _confirmed = false;
                        }),
                  icon: const Icon(Icons.close),
                  tooltip: 'Remove draft item',
                ),
              ),
            TextField(
              controller: _reason,
              enabled: !_busy,
              maxLength: 1000,
              decoration: const InputDecoration(
                labelText: 'Clinical correction reason',
              ),
              onChanged: (_) => setState(() => _confirmed = false),
            ),
            _confirm('I independently verified these corrected results.'),
            FilledButton(
              onPressed:
                  writable &&
                      _confirmed &&
                      _changes.isNotEmpty &&
                      _reason.text.trim().length >= 5
                  ? () {
                      final changes = List<LabCorrection>.of(_changes),
                          reason = _reason.text;
                      unawaited(
                        _decision(
                          (a) => a.correct(s, changes, reason, confirmed: true),
                        ),
                      );
                    }
                  : null,
              child: const Text('Verify correction'),
            ),
          ],
        ],
      ],
    );
  }

  Widget _patient() => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      OutlinedButton(
        onPressed: _busy ? null : () => _patientPage(1),
        child: const Text('Refresh released results'),
      ),
      if (_page case final page?) ...[
        if (page.items.isEmpty)
          const Text('No released laboratory reports on this page.'),
        for (final r in page.items)
          ListTile(
            title: Text(
              'Laboratory report ${r.orderId} · Version ${r.version}',
            ),
            subtitle: Text('${r.count} tests · ${r.releasedAt}'),
            onTap: _busy ? null : () => _patientDetail(r.orderId),
          ),
        Row(
          children: [
            TextButton(
              onPressed: _busy || page.page <= 1
                  ? null
                  : () => _patientPage(page.page - 1),
              child: const Text('Previous'),
            ),
            Text('Page ${page.page}'),
            TextButton(
              onPressed: _busy || !page.hasNext
                  ? null
                  : () => _patientPage(page.page + 1),
              child: const Text('Next'),
            ),
          ],
        ),
      ],
      if (_detail case final detail?) ...[
        _report(detail),
        const OutlinedButton(
          onPressed: null,
          child: Text('Released PDF copy (held)'),
        ),
      ],
    ],
  );
  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: Text(
        _session?.role == 'patient' ? 'My Lab Results' : 'Laboratory release',
      ),
    ),
    body: SafeArea(
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            'Online only. Backend authorization and clinical release holds apply.',
          ),
          if (_session == null)
            const Text('Laboratory release session unavailable.')
          else if (_session!.role == 'patient')
            _patient()
          else
            _staff(),
          if (_busy) const LinearProgressIndicator(),
          if (_message != null)
            Text(_message!, key: const ValueKey('lab-release-message')),
          if (widget.source.writesHeld && _session?.role != 'patient')
            const Text('Further clinical writes held pending reconciliation.'),
        ],
      ),
    ),
  );
}
