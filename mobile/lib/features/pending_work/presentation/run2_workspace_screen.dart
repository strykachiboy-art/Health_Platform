import 'dart:async';

import 'package:flutter/material.dart';

import '../../../core/sync/run2_policy.dart';
import '../../../core/sync/run2_transport.dart';
import '../../../core/sync/run2_workspace.dart';
import 'run2_module_screen.dart';

/// Attached to the existing authenticated status frame. No new route or role
/// grant; the page independently requires the actual verified tenant session.
class Run2OfflineButton extends StatelessWidget {
  const Run2OfflineButton({super.key, required this.workspace});
  final Run2Workspace workspace;
  @override
  Widget build(BuildContext context) => OutlinedButton.icon(
    key: const ValueKey('run2-offline-center'),
    icon: const Icon(Icons.cloud_off_outlined),
    label: const Text('Offline work center'),
    onPressed: () {
      if (workspace.guard.canAccessProtectedRoutes) {
        Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => Run2WorkspaceScreen(workspace: workspace),
          ),
        );
      }
    },
  );
}

class Run2WorkspaceScreen extends StatefulWidget {
  const Run2WorkspaceScreen({super.key, required this.workspace});
  final Run2Workspace workspace;
  @override
  State<Run2WorkspaceScreen> createState() => _Run2WorkspaceScreenState();
}

class _Run2WorkspaceScreenState extends State<Run2WorkspaceScreen> {
  Run2View? _view;
  List<Run2ConversationChoice> _chats = const [];
  Timer? _timer;
  int _generation = 0;
  bool _busy = false;
  String? _notice;
  String? _domain;
  String _intent = 'Review online';
  String _feedback = 'navigation';
  String _chat = 'greeting';
  int? _conversation;
  Run2Workspace get source => widget.workspace;

  @override
  void initState() {
    super.initState();
    source.guard.addListener(_lock);
    _timer = Timer.periodic(const Duration(seconds: 2), (_) {
      if (!_busy && (_view != null || _chats.isNotEmpty)) {
        unawaited(_checkVisibleLease());
      }
    });
    if (source.leaseValid) {
      unawaited(_run(() async {}));
    }
  }

  @override
  void dispose() {
    _generation++;
    source.guard.removeListener(_lock);
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _checkVisibleLease() async {
    final generation = _generation;
    final valid = await source.verifyLease();
    if (mounted && generation == _generation && !valid) {
      _lock();
    }
  }

  void _lock() {
    if (!mounted) {
      return;
    }
    source.clearLease();
    setState(() {
      _generation++;
      _view = null;
      _chats = const [];
      _conversation = null;
      _domain = null;
      _busy = false;
      _notice = 'Offline access locked. Verify the current session online.';
    });
  }

  Future<void> _run(Future<void> Function() action) async {
    if (!mounted || _busy || !source.guard.canAccessProtectedRoutes) {
      return;
    }
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _notice = null;
    });
    try {
      await action();
      final value = await source.view();
      if (!mounted || generation != _generation || !source.leaseValid) {
        return;
      }
      setState(() {
        _view = value;
        if (!Run2Policy.domains(value.role).contains(_domain)) {
          _domain = null;
        }
      });
    } on Object {
      if (mounted && generation == _generation) {
        setState(() {
          _view = null;
          _chats = const [];
          _conversation = null;
          _notice = 'Action not confirmed. Check local work and the server before creating a duplicate.';
        });
      }
    } finally {
      if (mounted && generation == _generation) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _loadChats() async {
    final generation = _generation;
    final value = await source.loadOrdinaryChats();
    if (mounted && _generation == generation && source.leaseValid) {
      setState(() {
        _chats = value;
        _conversation = null;
      });
    }
  }

  Future<void> _confirm(String message, Future<void> Function() action) async {
    final generation = _generation;
    final approved = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('Queue this exact request?'),
        content: Text(message),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialog).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialog).pop(true),
            child: const Text('Queue'),
          ),
        ],
      ),
    );
    if (mounted &&
        approved == true &&
        generation == _generation &&
        source.leaseValid) {
      await _run(action);
    }
  }

  static String _state(Run2WorkItem item) {
    if (item.localOnly) {
      return item.type == Run2Policy.snapshot
          ? 'Historical snapshot — not live status'
          : 'Local reminder — not submitted';
    }
    return switch (item.state) {
      'acknowledged' =>
        'Server record confirmed; recipient delivery not implied',
      'queued' => 'Queued locally — not yet sent',
      'retryScheduled' => 'Retry scheduled with the original key',
      'inFlight' => 'Sending; outcome pending',
      'reconciliationRequired' ||
      'reviewRequired' ||
      'userInterventionRequired' =>
        'Server review required — do not recreate or discard',
      'rejected' => 'Rejected; review required',
      'cancelled' => 'Cancelled locally',
      _ => 'Status unknown; review required',
    };
  }

  @override
  Widget build(BuildContext context) {
    final current = source.leaseValid ? _view : null;
    final domains =
        current == null ? <String>[] : Run2Policy.domains(current.role).toList()
          ..sort();
    return Scaffold(
      appBar: AppBar(title: const Text('Offline work center')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text(
              'Local work is not a clinical order, note, payment or proof of server acceptance. Module-specific snapshots and working drafts are available only under their explicit storage policy.',
            ),
            const SizedBox(height: 8),
            FilledButton(
              onPressed: _busy
                  ? null
                  : () => _run(() async {
                      _chats = const [];
                      _conversation = null;
                      await source.refreshOnline();
                    }),
              child: const Text('Verify clinic session online'),
            ),
            if (source.leaseValid)
              OutlinedButton(
                onPressed: _busy ? null : () => _run(() async {}),
                child: const Text('Reload retained local work'),
              ),
            if (_busy) const LinearProgressIndicator(),
            if (_notice != null) Text(_notice!),
            if (current == null)
              const Text(
                'Locked until an authorized clinic session is verified. Access expires after 15 minutes or token expiry; restart requires online verification.',
              ),
            if (current != null) ...[
              if (source.moduleTransport != null)
                OutlinedButton.icon(
                  icon: const Icon(Icons.folder_open_outlined),
                  label: const Text(
                    'Module snapshots and documentation drafts',
                  ),
                  onPressed: _busy
                      ? null
                      : () {
                          Navigator.of(context).push(
                            MaterialPageRoute<void>(
                              builder: (_) =>
                                  Run2ModuleScreen(workspace: source),
                            ),
                          );
                        },
                ),
              const SizedBox(height: 12),
              const Text(
                'Workflow reminders (no patient details)',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
              DropdownButton<String>(
                isExpanded: true,
                value: _domain,
                hint: const Text('Choose your workflow'),
                items: domains
                    .map(
                      (d) => DropdownMenuItem(
                        value: d,
                        child: Text(d.replaceAll('_', ' ')),
                      ),
                    )
                    .toList(),
                onChanged: _busy ? null : (v) => setState(() => _domain = v),
              ),
              DropdownButton<String>(
                isExpanded: true,
                value: _intent,
                items: Run2Policy.intents
                    .map((d) => DropdownMenuItem(value: d, child: Text(d)))
                    .toList(),
                onChanged: _busy
                    ? null
                    : (v) {
                        if (v != null) {
                          setState(() => _intent = v);
                        }
                      },
              ),
              OutlinedButton(
                onPressed: _busy || _domain == null
                    ? null
                    : () => _run(() => source.saveReminder(_domain!, _intent)),
                child: const Text('Save encrypted reminder'),
              ),
              const Divider(),
              const Text(
                'Untargeted product feedback',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
              DropdownButton<String>(
                isExpanded: true,
                value: _feedback,
                items: Run2Policy.feedbackPresets.entries
                    .map(
                      (e) => DropdownMenuItem(
                        value: e.key,
                        child: Text(e.value[2]),
                      ),
                    )
                    .toList(),
                onChanged: _busy
                    ? null
                    : (v) {
                        if (v != null) {
                          setState(() => _feedback = v);
                        }
                      },
              ),
              Text(Run2Policy.feedbackPresets[_feedback]![3]),
              OutlinedButton(
                onPressed: _busy
                    ? null
                    : () {
                        final preset = _feedback;
                        unawaited(
                          _confirm(
                            'The exact displayed product feedback will synchronize when authorized connectivity returns, within 24 hours. No free text or patient reference is stored.',
                            () => source.queueFeedback(preset),
                          ),
                        );
                      },
                child: const Text('Queue product feedback'),
              ),
              if (Run2Policy.chatRoles.contains(current.role)) ...[
                const Divider(),
                const Text(
                  'Routine non-case messages',
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
                const Text(
                  'Select a conversation from a fresh online list. Only predefined greetings are supported; no clinical, urgent, reply, media or voice content.',
                ),
                OutlinedButton(
                  onPressed: _busy ? null : () => _run(_loadChats),
                  child: const Text('Load eligible conversations online'),
                ),
                if (_chats.isNotEmpty)
                  DropdownButton<int>(
                    isExpanded: true,
                    value: _conversation,
                    hint: const Text('Select conversation'),
                    items: _chats
                        .map(
                          (c) => DropdownMenuItem(
                            value: c.id,
                            child: Text(
                              c.label,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        )
                        .toList(),
                    onChanged: _busy
                        ? null
                        : (v) => setState(() => _conversation = v),
                  ),
                DropdownButton<String>(
                  isExpanded: true,
                  value: _chat,
                  items: Run2Policy.chatPresets.entries
                      .map(
                        (e) => DropdownMenuItem(
                          value: e.key,
                          child: Text(e.value),
                        ),
                      )
                      .toList(),
                  onChanged: _busy
                      ? null
                      : (v) {
                          if (v != null) {
                            setState(() => _chat = v);
                          }
                        },
                ),
                OutlinedButton(
                  onPressed: _busy || _conversation == null
                      ? null
                      : () {
                          final id = _conversation!;
                          final preset = _chat;
                          final label = _chats
                              .firstWhere((c) => c.id == id)
                              .label;
                          unawaited(
                            _confirm(
                              'Send "${Run2Policy.chatPresets[preset]}" to $label when online, within 30 minutes, only if the conversation remains eligible.',
                              () => source.queueChat(id, preset),
                            ),
                          );
                        },
                  child: const Text('Queue routine message'),
                ),
              ],
              const Divider(),
              const Text(
                'Local work and delivery status',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
              OutlinedButton(
                onPressed: _busy ? null : () => _run(() async {}),
                child: const Text('Refresh local status'),
              ),
              if (current.items.isEmpty)
                const Text('No retained work for this scope.'),
              for (final item in current.items)
                ListTile(
                  title: Text(item.description),
                  subtitle: Text(
                    '${_state(item)}\nCaptured: ${item.capturedAt.toLocal()}',
                  ),
                  trailing: item.type == Run2Policy.draft
                      ? IconButton(
                          tooltip: 'Delete local reminder',
                          icon: const Icon(Icons.delete_outline),
                          onPressed: _busy
                              ? null
                              : () =>
                                    _run(() => source.deleteReminder(item.id)),
                        )
                      : null,
                ),
            ],
          ],
        ),
      ),
    );
  }
}
