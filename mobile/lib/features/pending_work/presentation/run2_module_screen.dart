import 'dart:async';

import 'package:flutter/material.dart';

import '../../../core/sync/run2_module_catalog.dart';
import '../../../core/sync/run2_workspace.dart';

class Run2ModuleScreen extends StatefulWidget {
  const Run2ModuleScreen({super.key, required this.workspace});
  final Run2Workspace workspace;
  @override
  State<Run2ModuleScreen> createState() => _Run2ModuleScreenState();
}

class _Run2ModuleScreenState extends State<Run2ModuleScreen>
    with WidgetsBindingObserver {
  Run2Workspace get source => widget.workspace;
  String? _role, _read, _draft, _notice;
  List<Run2ModuleItem> _items = const [];
  final _readId = TextEditingController();
  final _draftId = TextEditingController();
  final _fields = <String, TextEditingController>{};
  int? _target, _targetPatient;
  int _generation = 0;
  bool _busy = false;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    source.guard.addListener(_lock);
    _timer = Timer.periodic(
      const Duration(seconds: 1),
      (_) => unawaited(_verify()),
    );
    unawaited(_run(() async {}));
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.detached ||
        state == AppLifecycleState.hidden) {
      source.clearLease();
      _lock();
    }
  }

  Future<void> _verify() async {
    final generation = _generation;
    if (_role == null) return;
    final valid = await source.verifyLease();
    if (!mounted || generation != _generation) return;
    if (!valid) {
      _lock();
      return;
    }
    final now = DateTime.now().toUtc();
    if (_items.any(
      (item) =>
          now.difference(item.capturedAt) >= Run2ModuleCatalog.retention ||
          now.isBefore(item.capturedAt),
    )) {
      setState(
        () => _items = _items
            .where(
              (item) =>
                  !now.isBefore(item.capturedAt) &&
                  now.difference(item.capturedAt) < Run2ModuleCatalog.retention,
            )
            .toList(),
      );
    }
  }

  void _clearInputs() {
    _readId.clear();
    _draftId.clear();
    for (final controller in _fields.values) {
      controller.clear();
    }
    _target = null;
    _targetPatient = null;
  }

  void _lock() {
    if (!mounted) return;
    setState(() {
      _generation++;
      _clearInputs();
      _role = null;
      _read = null;
      _draft = null;
      _items = const [];
      _busy = false;
      _notice = 'Locked. Return to Offline work center and verify online.';
    });
  }

  @override
  void dispose() {
    _generation++;
    source.guard.removeListener(_lock);
    WidgetsBinding.instance.removeObserver(this);
    _timer?.cancel();
    _readId.dispose();
    _draftId.dispose();
    for (final c in _fields.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy || !mounted || !source.leaseValid) return;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _notice = null;
      _items = const [];
    });
    try {
      await action();
      final role = await source.moduleRole();
      final items = await source.moduleItems();
      if (!mounted || generation != _generation || !source.leaseValid) return;
      setState(() {
        _role = role;
        _items = items;
      });
    } on Object {
      if (!mounted || generation != _generation) return;
      if (!source.leaseValid) {
        _lock();
        return;
      }
      setState(() {
        _notice = 'Not confirmed. Check live authorization, resource identity, data policy and input. No clinical submission was made.';
      });
    } finally {
      if (mounted && generation == _generation) {
        setState(() => _busy = false);
      }
    }
  }

  int _id(TextEditingController c) => int.tryParse(c.text.trim()) ?? -1;

  void _selectDraft(String? value) {
    setState(() {
      for (final c in _fields.values) {
        c.clear();
      }
      _draft = value;
      _draftId.clear();
      _target = null;
      _targetPatient = null;
      final spec = Run2ModuleCatalog.drafts[value];
      if (spec != null) {
        for (final field in spec.fields) {
          _fields.putIfAbsent(field.key, TextEditingController.new);
        }
      }
    });
  }

  void _continueDraft(Run2ModuleItem item) {
    if (!item.visible || !item.isDraft || !source.leaseValid) return;
    _selectDraft(item.kind);
    setState(() {
      _draftId.text = item.selector == null || item.selector == 0
          ? ''
          : '${item.selector}';
      _target = item.targetId == 0 ? null : item.targetId;
      _targetPatient = item.targetPatientId;
      for (final e in item.fields.entries) {
        _fields[e.key]?.text = e.value;
      }
      _notice = 'Working draft loaded into the form. Saving creates a new local copy; the older one remains until you delete it.';
    });
  }

  @override
  Widget build(BuildContext context) {
    final role = source.leaseValid ? _role : null;
    final reads = role == null
        ? <ModuleReadSpec>[]
        : Run2ModuleCatalog.readsFor(role, source.managedRecords);
    final drafts = role == null
        ? <ModuleDraftSpec>[]
        : Run2ModuleCatalog.draftsFor(role, source.managedRecords);
    final read = Run2ModuleCatalog.reads[_read];
    final draft = Run2ModuleCatalog.drafts[_draft];
    final anchor = draft == null
        ? null
        : Run2ModuleCatalog.anchor(draft.anchor);
    return Scaffold(
      appBar: AppBar(title: const Text('Module offline work')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text(
              'Historical snapshots and unsigned working drafts only. Never use a stale record to administer medication, issue blood, treat a patient, settle payment or dispatch a vehicle. All clinical submissions require fresh online review in the original module.',
            ),
            if (!source.managedRecords)
              const Text(
                'Managed clinical/financial storage is disabled by deployment policy. Only permitted nonclinical catalogues are available.',
              ),
            if (_busy) const LinearProgressIndicator(),
            if (_notice != null) Text(_notice!),
            if (role == null)
              const Text('No verified offline access.')
            else ...[
              const Divider(),
              const Text(
                'Historical reads — capture online',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
              DropdownButton<String>(
                isExpanded: true,
                value: _read,
                hint: const Text('Select read contract'),
                items: reads
                    .map(
                      (s) =>
                          DropdownMenuItem(value: s.key, child: Text(s.label)),
                    )
                    .toList(),
                onChanged: _busy
                    ? null
                    : (v) => setState(() {
                        _read = v;
                        _readId.clear();
                      }),
              ),
              if (read != null && read.requiresId)
                TextField(
                  controller: _readId,
                  enabled: !_busy,
                  keyboardType: TextInputType.number,
                  decoration: InputDecoration(labelText: read.selector),
                ),
              OutlinedButton(
                onPressed: _busy || read == null
                    ? null
                    : () => _run(() async {
                        await source.captureModuleSnapshot(
                          read.key,
                          read.requiresId ? _id(_readId) : 0,
                        );
                        _readId.clear();
                      }),
                child: const Text('Capture encrypted historical snapshot'),
              ),
              if (drafts.isNotEmpty) ...[
                const Divider(),
                const Text(
                  'Unsigned documentation drafts',
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
                DropdownButton<String>(
                  isExpanded: true,
                  value: _draft,
                  hint: const Text('Choose document type'),
                  items: drafts
                      .map(
                        (s) => DropdownMenuItem(
                          value: s.key,
                          child: Text(s.label),
                        ),
                      )
                      .toList(),
                  onChanged: _busy ? null : _selectDraft,
                ),
                if (draft != null) ...[
                  if (anchor != null) ...[
                    TextField(
                      controller: _draftId,
                      enabled: !_busy,
                      keyboardType: TextInputType.number,
                      onChanged: (_) => setState(() {
                        _target = null;
                        _targetPatient = null;
                        for (final f in _fields.values) {
                          f.clear();
                        }
                      }),
                      decoration: InputDecoration(labelText: anchor.selector),
                    ),
                    OutlinedButton(
                      onPressed: _busy
                          ? null
                          : () => _run(() async {
                              final generation = _generation;
                              final verified = await source.prepareModuleDraft(
                                draft.key,
                                _id(_draftId),
                              );
                              final patient = await source.verifiedDraftPatient(
                                draft.key,
                                _id(_draftId),
                              );
                              if (mounted &&
                                  generation == _generation &&
                                  source.leaseValid) {
                                setState(() {
                                  _target = verified;
                                  _targetPatient = patient;
                                });
                              }
                            }),
                      child: const Text('Verify exact target online'),
                    ),
                    if (_target != null)
                      Text(
                        'Verified target record: $_target. Confirm this identity in the original clinical module.',
                      ),
                    if (_targetPatient != null)
                      Text('Verified patient reference: $_targetPatient'),
                  ],
                  if (anchor == null || _target != null) ...[
                    const Text(
                      'Working copy only. Do not enter passwords, tokens, or credential material. Review and re-enter into the existing online form when connected.',
                    ),
                    for (final field in draft.fields)
                      TextField(
                        key: ValueKey('${draft.key}-${field.key}'),
                        controller: _fields[field.key],
                        enabled: !_busy,
                        maxLength: field.limit,
                        maxLines: field.kind == 'text' && field.limit > 255
                            ? 3
                            : 1,
                        decoration: InputDecoration(
                          labelText:
                              '${field.label}${field.required ? ' *' : ''}',
                        ),
                      ),
                    FilledButton(
                      onPressed: _busy
                          ? null
                          : () => _run(() async {
                              await source.saveModuleDraft(
                                draft.key,
                                anchor == null ? 0 : _id(_draftId),
                                {
                                  for (final f in draft.fields)
                                    f.key: _fields[f.key]!.text,
                                },
                              );
                              for (final c in _fields.values) {
                                c.clear();
                              }
                            }),
                      child: const Text('Save encrypted working draft'),
                    ),
                  ],
                ],
              ],
              const Divider(),
              const Text(
                'Retained module work (24-hour retention)',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
              OutlinedButton(
                onPressed: _busy ? null : () => _run(() async {}),
                child: const Text('Reload local work'),
              ),
              if (_items.isEmpty)
                const Text('No retained module work in this scope.'),
              for (final item in _items)
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          item.label,
                          style: const TextStyle(fontWeight: FontWeight.bold),
                        ),
                        Text(
                          '${item.isDraft ? 'UNSIGNED LOCAL DRAFT — NOT SUBMITTED' : 'HISTORICAL — NOT CURRENT'}\nCaptured: ${item.capturedAt.toLocal()}',
                        ),
                        if (item.visible) ...[
                          if (item.selector != null && item.selector != 0)
                            Text('Selected reference: ${item.selector}'),
                          if (item.targetId != null && item.targetId != 0)
                            Text(
                              'Verified target at drafting: ${item.targetId}',
                            ),
                          if (item.targetPatientId != null)
                            Text(
                              'Patient reference at drafting: ${item.targetPatientId}',
                            ),
                          for (final row in item.rows) ...[
                            const Divider(),
                            for (final e in row.entries)
                              Text(
                                '${e.key.replaceAll('_', ' ')}: ${e.value ?? 'Not recorded'}',
                              ),
                          ],
                          for (final e in item.fields.entries)
                            Text('${e.key.replaceAll('_', ' ')}: ${e.value}'),
                        ] else
                          const Text(
                            'Contents locked until fresh target-specific online authorization.',
                          ),
                        Wrap(
                          spacing: 8,
                          children: [
                            if (item.isDraft && item.visible)
                              OutlinedButton(
                                onPressed: _busy
                                    ? null
                                    : () => _continueDraft(item),
                                child: const Text('Continue as new local copy'),
                              ),
                            OutlinedButton(
                              onPressed: _busy
                                  ? null
                                  : () => _run(
                                      () => source.reopenModuleItemOnline(
                                        item.id,
                                      ),
                                    ),
                              child: const Text('Revalidate target online'),
                            ),
                            OutlinedButton(
                              onPressed: _busy
                                  ? null
                                  : () => _run(
                                      () => source.deleteModuleItem(
                                        item.id,
                                        item.type,
                                      ),
                                    ),
                              child: const Text('Delete local copy'),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}
