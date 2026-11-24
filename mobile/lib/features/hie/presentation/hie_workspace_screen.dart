import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/hie/data/hie_remote_data_source.dart';

/// Staged HIE workspace. Navigation is wired in Run B with one shared source.
class HieWorkspaceScreen extends StatefulWidget {
  const HieWorkspaceScreen({super.key, required this.source});
  final HieRemoteDataSource source;

  @override
  State<HieWorkspaceScreen> createState() => _HieWorkspaceScreenState();
}

class _HieWorkspaceScreenState extends State<HieWorkspaceScreen> {
  final _id = TextEditingController();
  final _provider = TextEditingController();
  final _endpoint = TextEditingController();
  final _organization = TextEditingController();
  final _facility = TextEditingController();
  String? _role;
  String? _message;
  HieIntegration? _record;
  HieSubmissionPage? _history;
  int? _historyFilter;
  bool _busy = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_reset);
    unawaited(_loadRole());
  }

  @override
  void didUpdateWidget(covariant HieWorkspaceScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source)) {
      oldWidget.source.guard.removeListener(_reset);
      widget.source.guard.addListener(_reset);
      _reset();
    }
  }

  @override
  void dispose() {
    widget.source.guard.removeListener(_reset);
    for (final c in [_id, _provider, _endpoint, _organization, _facility]) {
      c.dispose();
    }
    super.dispose();
  }

  bool _current(int g, int rev) =>
      mounted &&
      g == _generation &&
      widget.source.guard.revision == rev &&
      widget.source.guard.canAccessProtectedRoutes;

  void _reset() {
    if (!mounted) return;
    setState(() {
      _generation++;
      _role = null;
      _record = null;
      _history = null;
      _historyFilter = null;
      _message = null;
      _busy = false;
      for (final c in [_id, _provider, _endpoint, _organization, _facility]) {
        c.clear();
      }
    });
    unawaited(_loadRole());
  }

  Future<void> _loadRole() async {
    final g = _generation;
    final rev = widget.source.guard.revision;
    try {
      final role = await widget.source.role();
      if (_current(g, rev)) setState(() => _role = role);
    } on Object {
      if (mounted && g == _generation) {
        setState(() => _message = 'HIE is unavailable for this session.');
      }
    }
  }

  bool get _admin => _role == 'admin';
  bool get _ready => _role != null && !_busy;
  bool get _canWrite => _ready && _admin && !widget.source.writesBlocked;

  int _recordId() {
    final result = int.tryParse(_id.text.trim());
    if (result == null || result <= 0 || result > 2147483647) {
      throw const FormatException('Enter a known integration ID.');
    }
    return result;
  }

  HieDraft _draft() => HieDraft(
    provider: _provider.text,
    endpoint: _endpoint.text,
    organization: _organization.text,
    facility: _facility.text,
  );

  void _applyRecord(HieIntegration record) {
    _record = record;
    _id.text = '${record.id}';
    if (_admin) {
      _provider.text = record.provider;
      _endpoint.text = record.endpoint ?? '';
      _organization.text = record.organization ?? '';
      _facility.text = record.facility ?? '';
    }
  }

  Future<void> _run(Future<void> Function(int, int) action) async {
    if (!_ready) return;
    final g = _generation;
    final rev = widget.source.guard.revision;
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await action(g, rev);
    } on HieMutationUncertain {
      if (_current(g, rev)) {
        setState(
          () => _message = 'Mutation not confirmed. Reconciliation required; further writes blocked.',
        );
      }
    } on Object {
      if (_current(g, rev)) {
        setState(
          () => _message = 'Request unavailable or rejected. Check the known ID, configuration and session.',
        );
      }
    } finally {
      if (_current(g, rev)) setState(() => _busy = false);
    }
  }

  Future<void> _read() => _run((g, rev) async {
    setState(() {
      _record = null;
      _history = null;
    });
    final record = await widget.source.read(_recordId());
    if (_current(g, rev)) setState(() => _applyRecord(record));
  });

  Future<void> _list({int page = 1, bool reuseFilter = false}) =>
      _run((g, rev) async {
        final filter = reuseFilter
            ? _historyFilter
            : (_id.text.trim().isEmpty ? null : _recordId());
        setState(() => _history = null);
        final result = await widget.source.submissions(
          integrationId: filter,
          page: page,
        );
        if (_current(g, rev)) {
          setState(() {
            _history = result;
            _historyFilter = filter;
          });
        }
      });

  Future<void> _mutate({required bool create, String? status}) async {
    if (!_canWrite) return;
    final rev = widget.source.guard.revision;
    final g = _generation;
    final target = _record;
    if (!create && (target == null || !target.holdConfirmed)) return;
    final draft = status == null ? _draft() : null;
    // Freeze and validate the exact payload before confirmation. No endpoint
    // credentials, tokens, headers or clinical queries are accepted here.
    try {
      draft?.toJson();
    } on Object {
      setState(() => _message = 'Enter valid non-secret configuration.');
      return;
    }
    setState(() => _busy = true);
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(
          create
              ? 'Create pending integration?'
              : 'Confirm integration update?',
        ),
        content: Text(
          create
              ? 'Create a pending record only. No external connection is authorized.'
              : 'Integration #${target!.id}: ${status ?? "update metadata"}. External access remains blocked.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('Confirm'),
          ),
        ],
      ),
    );
    if (!_current(g, rev)) return;
    setState(() => _busy = false);
    if (confirmed != true) return;
    await _run((generation, revision) async {
      final result = create
          ? await widget.source.create(draft!)
          : await widget.source.update(
              target!.id,
              draft: draft,
              status: status,
            );
      if (_current(generation, revision)) {
        setState(() {
          _applyRecord(result);
          _message = 'Backend confirmed staged configuration.';
        });
      }
    });
  }

  Widget _field(TextEditingController controller, String label, int limit) =>
      TextField(
        controller: controller,
        enabled: _ready && _admin,
        maxLength: limit,
        decoration: InputDecoration(labelText: label),
      );

  @override
  Widget build(BuildContext context) {
    final record = _record;
    final history = _history;
    return Scaffold(
      appBar: AppBar(title: const Text('Health information exchange')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            'External HIE access is on hold. Partner approval and sandbox evidence are required.',
          ),
          if (_message != null)
            Text(_message!, key: const ValueKey('hie-message')),
          if (_busy) const LinearProgressIndicator(),
          if (_role != null) ...[
            TextField(
              controller: _id,
              enabled: _ready,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: 'Known integration ID',
              ),
            ),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                OutlinedButton(
                  onPressed: _ready ? () => unawaited(_read()) : null,
                  child: const Text('Read integration'),
                ),
                OutlinedButton(
                  onPressed: _ready ? () => unawaited(_list()) : null,
                  child: const Text('Submission history'),
                ),
              ],
            ),
            if (record != null) ...[
              Text(
                'Integration #${record.id} · ${record.provider} · ${record.status}',
              ),
              Text(
                record.holdConfirmed
                    ? 'Partner approval required'
                    : 'Readiness not verified',
              ),
              Text(
                'Provider registered: ${record.providerRegistered?.toString() ?? "unknown"}',
              ),
            ],
            const OutlinedButton(
              onPressed: null,
              child: Text('Query external patient (held)'),
            ),
            const OutlinedButton(
              onPressed: null,
              child: Text('Query external clinical data (held)'),
            ),
            if (_admin) ...[
              const Divider(),
              const Text(
                'Non-secret pending configuration only. Do not enter credentials.',
              ),
              _field(_provider, 'Provider name', 50),
              _field(_endpoint, 'Approved HTTPS endpoint (optional)', 255),
              _field(_organization, 'External organization ID (optional)', 100),
              _field(_facility, 'External facility ID (optional)', 100),
              const Text('Blank optional fields are omitted, not cleared.'),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  FilledButton(
                    onPressed: _canWrite
                        ? () => unawaited(_mutate(create: true))
                        : null,
                    child: const Text('Create pending'),
                  ),
                  OutlinedButton(
                    onPressed:
                        _canWrite &&
                            record != null &&
                            record.holdConfirmed &&
                            record.status != 'active'
                        ? () => unawaited(_mutate(create: false))
                        : null,
                    child: const Text('Update metadata'),
                  ),
                  for (final status in ['pending', 'suspended', 'disabled'])
                    OutlinedButton(
                      onPressed:
                          _canWrite &&
                              record != null &&
                              record.holdConfirmed &&
                              record.status != status &&
                              !(record.status == 'active' &&
                                  status == 'pending')
                          ? () => unawaited(
                              _mutate(create: false, status: status),
                            )
                          : null,
                      child: Text('Set $status'),
                    ),
                  const OutlinedButton(
                    onPressed: null,
                    child: Text('Activate (held)'),
                  ),
                ],
              ),
              if (widget.source.writesBlocked && !_busy)
                const Text(
                  'Writes blocked; reconcile with clinic administrator before further changes.',
                ),
            ],
            if (history != null) ...[
              const Divider(),
              Text(
                'Submission history · page ${history.page} · ${history.total} records',
              ),
              for (final item in history.items)
                ListTile(
                  title: Text(
                    '#${item.id} · ${item.operation} · ${item.status}',
                  ),
                  subtitle: Text(
                    'Integration #${item.integrationId} · ${item.createdAt}\n'
                    'Failure: ${item.failureClass ?? "none"} · HTTP: ${item.statusCode ?? "—"} · Retries: ${item.retryCount}',
                  ),
                ),
              if (history.items.isEmpty)
                const Text('No matching submission metadata.'),
              Wrap(
                spacing: 8,
                children: [
                  TextButton(
                    onPressed: _ready && history.page > 1
                        ? () => unawaited(
                            _list(page: history.page - 1, reuseFilter: true),
                          )
                        : null,
                    child: const Text('Previous'),
                  ),
                  TextButton(
                    onPressed: _ready && history.hasNext
                        ? () => unawaited(
                            _list(page: history.page + 1, reuseFilter: true),
                          )
                        : null,
                    child: const Text('Next'),
                  ),
                ],
              ),
            ],
          ],
        ],
      ),
    );
  }
}
