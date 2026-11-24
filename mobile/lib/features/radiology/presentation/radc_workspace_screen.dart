import 'dart:async';

import 'package:flutter/material.dart';

import '../data/radc_staged_remote_data_source.dart';

class RadcWorkspaceScreen extends StatefulWidget {
  const RadcWorkspaceScreen({
    super.key,
    required this.source,
    required this.patientOnly,
    this.initialExamId,
  });
  final RadcSource source;
  final bool patientOnly;
  final int? initialExamId;
  @override
  State<RadcWorkspaceScreen> createState() => _RadcWorkspaceScreenState();
}

class _RadcWorkspaceScreenState extends State<RadcWorkspaceScreen> {
  final _examField = TextEditingController(),
      _pubField = TextEditingController(),
      _textField = TextEditingController(),
      _reasonField = TextEditingController(),
      _withdrawVersion = TextEditingController();
  RadcSession? _session;
  RadcWorklist? _work;
  RadcPreview? _preview;
  RadcReport? _report;
  RadcViewer? _viewer;
  List<RadcSummary> _items = const [];
  int? _exam, _publication;
  String? _notice, _acquisition;
  bool _busy = false;
  int _generation = 0;
  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_clear);
    unawaited(_connect());
  }

  @override
  void didUpdateWidget(covariant RadcWorkspaceScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source)) {
      oldWidget.source.guard.removeListener(_clear);
      widget.source.guard.addListener(_clear);
      _clear();
    } else if (oldWidget.patientOnly != widget.patientOnly ||
        oldWidget.initialExamId != widget.initialExamId) {
      _clear();
    }
  }

  void _clearFields() {
    _examField.clear();
    _pubField.clear();
    _textField.clear();
    _reasonField.clear();
    _withdrawVersion.clear();
  }

  void _reset() {
    _work = null;
    _preview = null;
    _report = null;
    _viewer = null;
    _acquisition = null;
    _textField.clear();
    _reasonField.clear();
    _withdrawVersion.clear();
  }

  void _clear() {
    _generation++;
    _clearFields();
    if (!mounted) {
      return;
    }
    setState(() {
      _session = null;
      _items = const [];
      _exam = null;
      _publication = null;
      _reset();
      _busy = false;
      _notice = 'Session changed. Staged details cleared.';
    });
  }

  @override
  void dispose() {
    widget.source.guard.removeListener(_clear);
    _generation++;
    _examField.dispose();
    _pubField.dispose();
    _textField.dispose();
    _reasonField.dispose();
    _withdrawVersion.dispose();
    super.dispose();
  }

  int? _id(String value) {
    final n = int.tryParse(value);
    return n != null && n > 0 && n <= 2147483647 && '$n' == value ? n : null;
  }

  bool _valid(int g, int rev) =>
      mounted &&
      g == _generation &&
      widget.source.guard.revision == rev &&
      widget.source.guard.canAccessProtectedRoutes;
  Future<void> _connect() async {
    if (_busy || !widget.source.guard.canAccessProtectedRoutes) {
      return;
    }
    final g = ++_generation, rev = widget.source.guard.revision;
    setState(() {
      _busy = true;
      _notice = null;
      _session = null;
      _items = const [];
      _reset();
      _exam = null;
      _publication = null;
      _clearFields();
    });
    try {
      final s = await widget.source.connect();
      if (s.role != (widget.patientOnly ? 'patient' : 'doctor')) {
        throw const RadcUnavailable();
      }
      await s.check();
      if (!_valid(g, rev)) {
        return;
      }
      setState(() {
        _session = s;
        if (!widget.patientOnly && widget.initialExamId != null) {
          _exam = widget.initialExamId;
          _examField.text = '${widget.initialExamId}';
        }
      });
      if (widget.patientOnly) {
        final id = widget.initialExamId;
        if (id == null) {
          final list = await s.patientList();
          if (_valid(g, rev)) {
            setState(() => _items = list);
          }
        } else {
          final r = await s.patientDetail(id);
          if (_valid(g, rev)) {
            setState(() => _report = r);
          }
        }
      }
    } on Object {
      if (_valid(g, rev)) {
        setState(
          () => _notice = 'Staged radiology unavailable or not authorized.',
        );
      }
    } finally {
      if (_valid(g, rev)) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _act(
    Future<void> Function(RadcSession, bool Function()) f,
  ) async {
    final s = _session;
    if (s == null || _busy || !widget.source.guard.canAccessProtectedRoutes) {
      return;
    }
    final g = ++_generation, rev = widget.source.guard.revision;
    bool current() => _valid(g, rev);
    setState(() {
      _busy = true;
      _notice = null;
    });
    try {
      await s.check();
      if (!current()) {
        return;
      }
      await f(s, current);
    } on RadcUncertain {
      if (current()) {
        setState(() {
          _reset();
          _notice = 'Write outcome uncertain. Read back online; further writes are held.';
        });
      }
    } on Object {
      if (current()) {
        setState(() {
          _reset();
          _items = const [];
          _notice = 'Staged radiology unavailable or not authorized.';
        });
      }
    } finally {
      if (current()) {
        setState(() => _busy = false);
      }
    }
  }

  void _select() {
    final e = _id(_examField.text), p = _id(_pubField.text);
    if (e == null || (_pubField.text.isNotEmpty && p == null)) {
      setState(
        () => _notice = 'Enter canonical positive examination and optional publication IDs.',
      );
      return;
    }
    setState(() {
      _reset();
      _exam = e;
      _publication = p;
      _notice = 'Selected scope. Backend authorization is required.';
    });
  }

  void _readWork() {
    final e = _exam;
    if (e == null) {
      return;
    }
    unawaited(
      _act((s, ok) async {
        final r = await s.worklist(e);
        if (ok()) {
          setState(() {
            _reset();
            _work = r;
            _publication = r.current ? r.publicationId : null;
            _pubField.text = _publication == null ? '' : '$_publication';
          });
        }
      }),
    );
  }

  void _publish() {
    final w = _work;
    if (w == null) {
      return;
    }
    unawaited(
      _act((s, ok) async {
        final r = await s.publish(w);
        if (ok()) {
          setState(() {
            _reset();
            _work = r;
            _publication = r.publicationId;
            _pubField.text = '$_publication';
          });
        }
      }),
    );
  }

  void _acquire(bool ct) {
    final e = _exam, p = _publication;
    if (e == null || p == null) {
      return;
    }
    unawaited(
      _act((s, ok) async {
        final v = await s.acquire(e, p, ct: ct);
        if (ok()) {
          setState(() {
            _preview = null;
            _report = null;
            _viewer = null;
            _acquisition = v;
          });
        }
      }),
    );
  }

  void _readViewer() {
    final e = _exam, p = _publication;
    if (e == null || p == null) {
      return;
    }
    unawaited(
      _act((s, ok) async {
        final v = await s.viewer(e, p);
        if (ok()) {
          setState(() => _viewer = v);
        }
      }),
    );
  }

  void _readReport() {
    final e = _exam, p = _publication;
    if (e == null || p == null) {
      return;
    }
    unawaited(
      _act((s, ok) async {
        final r = await s.status(e, p);
        if (ok()) {
          setState(() {
            _preview = null;
            _report = r;
            _withdrawVersion.text = '${r.version}';
          });
        }
      }),
    );
  }

  void _readPreview(bool copy) {
    final e = _exam, p = _publication;
    if (e == null || p == null) {
      return;
    }
    unawaited(
      _act((s, ok) async {
        final v = await s.preview(e, p, copy: copy);
        if (ok()) {
          setState(() {
            _preview = v;
            _report = null;
          });
        }
      }),
    );
  }

  void _approve() {
    final v = _preview;
    if (v == null) {
      return;
    }
    setState(() => _preview = null);
    unawaited(
      _act((s, ok) async {
        final r = await s.approve(v);
        if (ok()) {
          setState(() {
            _report = r;
            _withdrawVersion.text = '${r.version}';
          });
        }
      }),
    );
  }

  void _amend() {
    final r = _report, p = _publication;
    if (r == null || p == null) {
      return;
    }
    final text = _textField.text, reason = _reasonField.text;
    unawaited(
      _act((s, ok) async {
        final n = await s.amend(r, p, text, reason);
        if (ok()) {
          setState(() {
            _report = n;
            _preview = null;
            _textField.clear();
            _reasonField.clear();
            _withdrawVersion.text = '${n.version}';
          });
        }
      }),
    );
  }

  void _withdraw() {
    final e = _exam, p = _publication, v = _id(_withdrawVersion.text);
    if (e == null || p == null || v == null) {
      return;
    }
    unawaited(
      _act((s, ok) async {
        await s.withdraw(e, p, v);
        if (ok()) {
          setState(() {
            _reset();
            _notice =
                'Staged copy withdrawn. Read status online before amendment.';
          });
        }
      }),
    );
  }

  void _patientDetail(int e) {
    setState(() => _report = null);
    unawaited(
      _act((s, ok) async {
        final r = await s.patientDetail(e);
        if (ok()) {
          setState(() => _report = r);
        }
      }),
    );
  }

  Widget _button(String label, VoidCallback? action) =>
      OutlinedButton(onPressed: action, child: Text(label));
  Widget _field(
    TextEditingController c,
    String label, {
    bool number = false,
    int? max,
    int lines = 1,
  }) => TextField(
    controller: c,
    enabled: !_busy,
    keyboardType: number ? TextInputType.number : TextInputType.text,
    maxLength: max,
    maxLines: lines,
    decoration: InputDecoration(labelText: label),
  );
  @override
  Widget build(BuildContext context) {
    final ready =
        _session != null &&
        widget.source.guard.canAccessProtectedRoutes &&
        !_busy;
    final canWrite = ready && !widget.source.writesHeld;
    final selected = _exam != null && _publication != null;
    final w = _work, r = _report, v = _preview, viewer = _viewer;
    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.patientOnly
              ? 'My staged radiology copies'
              : 'Staged radiology workspace',
        ),
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text(
              'SYNTHETIC TEST ONLY — NOT FOR DIAGNOSIS, SIGNATURE OR CHART RELEASE',
              key: ValueKey('radc-boundary'),
            ),
            const SizedBox(height: 12),
            _button(
              'Refresh session and readback',
              _busy ? null : () => unawaited(_connect()),
            ),
            if (_busy) const LinearProgressIndicator(),
            if (_notice != null)
              Text(_notice!, key: const ValueKey('radc-notice')),
            if (_session != null && widget.patientOnly) ...[
              for (final item in _items)
                ListTile(
                  title: Text('Test exam ${item.examId} · v${item.version}'),
                  subtitle: Text(item.releasedAt),
                  onTap: ready ? () => _patientDetail(item.examId) : null,
                ),
              if (r != null) ...[
                Text('Test copy v${r.version}'),
                Text(r.text, key: const ValueKey('radc-report-text')),
                Text('Released: ${r.releasedAt ?? "Unavailable"}'),
              ],
              const Text(
                'Controlled PDF endpoint exists; native download and printing remain held.',
              ),
            ],
            if (_session != null && !widget.patientOnly) ...[
              _field(_examField, 'Examination ID', number: true),
              _field(
                _pubField,
                'Publication ID (optional for worklist)',
                number: true,
              ),
              _button('Select exam scope', ready ? _select : null),
              if (_exam != null)
                Text(
                  'Selected exam $_exam · publication ${_publication ?? "not selected"}',
                ),
              _button(
                'Read local worklist',
                ready && _exam != null ? _readWork : null,
              ),
              if (w != null) ...[
                Text(
                  'Local worklist: ${w.state} · exam version ${w.examVersion}',
                ),
                _button(
                  'Publish local intent once',
                  canWrite &&
                          const {
                            'not_published',
                            'superseded',
                          }.contains(w.state)
                      ? _publish
                      : null,
                ),
              ],
              const Text(
                'External worklist dispatch and modality receipt are not available to this client.',
              ),
              const Divider(),
              Wrap(
                spacing: 8,
                children: [
                  _button(
                    'Validate staged SC once',
                    canWrite && selected ? () => _acquire(false) : null,
                  ),
                  _button(
                    'Validate staged CT once',
                    canWrite && selected ? () => _acquire(true) : null,
                  ),
                  _button(
                    'Read scoped CT viewer descriptor',
                    ready && selected ? _readViewer : null,
                  ),
                ],
              ),
              if (_acquisition != null)
                Text('Acquisition: $_acquisition (non-diagnostic)'),
              if (viewer != null)
                Text(
                  'Validated CT descriptor: ${viewer.instanceCount} instances · ${viewer.studyUid}',
                ),
              const Text(
                'OHIF browser launch and diagnostic image viewing remain held.',
              ),
              const Divider(),
              Wrap(
                spacing: 8,
                children: [
                  _button(
                    'Read draft freeze preview',
                    ready && selected ? () => _readPreview(false) : null,
                  ),
                  _button(
                    'Read staged report status',
                    ready && selected ? _readReport : null,
                  ),
                  _button(
                    'Read patient-copy preview',
                    ready && selected ? () => _readPreview(true) : null,
                  ),
                ],
              ),
              if (v != null) ...[
                Text(
                  'Review ${v.copy ? "patient test copy" : "draft freeze"} · v${v.version}',
                ),
                Text(v.text, key: const ValueKey('radc-preview-text')),
                _button(
                  v.copy
                      ? 'Approve this test copy once'
                      : 'Freeze this test draft once',
                  canWrite ? _approve : null,
                ),
              ],
              if (r != null) ...[
                Text(
                  'Frozen test report v${r.version} · ${r.released ? "copy released" : "copy not released"}',
                ),
                Text(r.text, key: const ValueKey('radc-report-text')),
                if (!r.released) ...[
                  _field(
                    _textField,
                    'Amended SYNTHETIC report text',
                    max: 12000,
                    lines: 5,
                  ),
                  _field(_reasonField, 'Amendment reason', max: 1000),
                  _button('Append amendment once', canWrite ? _amend : null),
                ],
              ],
              _field(
                _withdrawVersion,
                'Exact current copy version for withdrawal',
                number: true,
              ),
              _button(
                'Withdraw test copy once',
                canWrite && selected ? _withdraw : null,
              ),
              if (widget.source.writesHeld)
                const Text('Writes held. No automatic replay.'),
            ],
          ],
        ),
      ),
    );
  }
}
