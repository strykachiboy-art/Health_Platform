import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_templates.dart';

class ConsultationTemplatesScreen extends StatefulWidget {
  const ConsultationTemplatesScreen({super.key, required this.source});
  final ConsultationTemplateSource source;
  @override
  State<ConsultationTemplatesScreen> createState() => _ConsultationTemplatesScreenState();
}

class _ConsultationTemplatesScreenState extends State<ConsultationTemplatesScreen> {
  final _name = TextEditingController();
  final _specialty = TextEditingController();
  final _structure = TextEditingController();
  ConsultationTemplatePage? _page;
  String? _message;
  bool _busy = false;
  int _generation = 0;
  bool _blocked = false;
  @override
  void initState() {
    super.initState();
    widget.source.consultations.sessionNavigationGuard.addListener(_invalidate);
    WidgetsBinding.instance.addPostFrameCallback((_) { if (mounted) _load(); });
  }
  @override
  void dispose() {
    widget.source.consultations.sessionNavigationGuard.removeListener(_invalidate);
    _name.dispose(); _specialty.dispose(); _structure.dispose();
    super.dispose();
  }
  void _invalidate() {
    if (!mounted) return;
    _generation++;
    _name.clear(); _specialty.clear(); _structure.clear();
    setState(() { _page = null; _busy = false; _blocked = true; _message = 'Session changed. Reopen this workspace.'; });
  }
  bool _current(int g, int rev) => mounted && _generation == g && widget.source.current(rev);
  Future<void> _load({int page = 1}) async {
    if (_busy) return;
    final g = ++_generation;
    final rev = widget.source.consultations.sessionNavigationGuard.revision;
    setState(() { _busy = true; _page = null; _message = null; });
    try {
      final result = await widget.source.list(page: page);
      if (_current(g, rev)) setState(() { _page = result; _busy = false; });
    } on Object {
      if (_current(g, rev)) setState(() { _busy = false; _message = 'Template access unavailable or denied.'; });
    }
  }
  Future<void> _create() async {
    if (_busy || _blocked || _page == null) return;
    final rev = widget.source.consultations.sessionNavigationGuard.revision;
    final g = _generation;
    final n = _name.text; final s = _specialty.text; final j = _structure.text;
    try { ConsultationTemplateSource.draft(n, s, j); } on Object {
      setState(() => _message = 'Enter a valid name and nonempty JSON template object.'); return;
    }
    final accepted = await showDialog<bool>(context: context, builder: (ctx) => AlertDialog(
      title: const Text('Create reusable template?'),
      content: const Text('Confirm this is reusable documentation structure with no patient-specific information. The server validates and owns the new template.'),
      actions: [TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Confirm create'))],
    ));
    if (accepted != true || !_current(g, rev) || _busy || _blocked) return;
    setState(() { _busy = true; _message = null; });
    try {
      final row = await widget.source.create(n, s, j, expectedRevision: rev);
      if (!_current(g, rev)) return;
      _name.clear(); _specialty.clear(); _structure.clear();
      setState(() { _busy = false; _message = 'Server confirmed template #${row.id}. Refresh to view the latest list.'; });
    } on Object {
      if (_current(g, rev)) setState(() { _busy = false; _blocked = true; _message = 'Creation not confirmed. Refresh to inspect server records; no automatic retry.'; });
    }
  }
  @override
  Widget build(BuildContext context) {
    final p = _page;
    return Scaffold(appBar: AppBar(title: const Text('Consultation templates')), body: ListView(padding: const EdgeInsets.all(16), children: [
      const Text('Reusable clinic documentation structures only. Do not enter patient data. No automatic application to a clinical note.'),
      if (_message != null) Text(_message!),
      if (_busy) const LinearProgressIndicator(),
      OutlinedButton(onPressed: _busy ? null : () => _load(page: p?.page ?? 1), child: const Text('Refresh templates')),
      if (p != null) ...[
        for (final row in p.items) ExpansionTile(title: Text(row.name), subtitle: Text(row.clinicId == null ? 'Shared template' : 'Clinic template'), children: [
          if (row.specialty != null) Text(row.specialty!),
          SelectableText(const JsonEncoder.withIndent('  ').convert(row.structure)),
        ]),
        Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
          TextButton(onPressed: _busy || p.page <= 1 ? null : () => _load(page: p.page - 1), child: const Text('Previous')),
          Text('Page ${p.page}'),
          TextButton(onPressed: _busy || !p.hasNext ? null : () => _load(page: p.page + 1), child: const Text('Next')),
        ]),
        const Divider(),
        TextField(controller: _name, enabled: !_busy && !_blocked, maxLength: 150, decoration: const InputDecoration(labelText: 'Template name')),
        TextField(controller: _specialty, enabled: !_busy && !_blocked, maxLength: 100, decoration: const InputDecoration(labelText: 'Specialty (optional)')),
        TextField(controller: _structure, enabled: !_busy && !_blocked, maxLength: 12000, maxLines: 5, decoration: const InputDecoration(labelText: 'Template structure (JSON object)', hintText: '{"sections": ["Chief complaint", "Assessment"]}')),
        FilledButton(onPressed: _busy || _blocked ? null : _create, child: const Text('Review template creation')),
      ],
    ]));
  }
}
