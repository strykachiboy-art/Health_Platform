import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_state_remote_data_source.dart';

/// P24G.S01: an explicit read-only view, not an automatic mark-as-read.
class ClinicChatReadStateScreen extends StatefulWidget {
  const ClinicChatReadStateScreen({
    super.key,
    required this.source,
    required this.conversationId,
  });

  final ClinicChatReadStateRemoteDataSource source;
  final int conversationId;

  @override
  State<ClinicChatReadStateScreen> createState() =>
      _ClinicChatReadStateScreenState();
}

class _ClinicChatReadStateScreenState extends State<ClinicChatReadStateScreen> {
  ClinicChatOwnReadState? _value;
  bool _loading = false;
  String? _error;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.read.sessionNavigationGuard.addListener(_onSessionChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_load());
    });
  }

  @override
  void dispose() {
    _generation++;
    widget.source.read.sessionNavigationGuard.removeListener(_onSessionChanged);
    super.dispose();
  }

  void _onSessionChanged() {
    _generation++;
    if (!mounted) return;
    setState(() {
      _value = null;
      _error = null;
      _loading = false;
    });
  }

  Future<void> _load() async {
    final guard = widget.source.read.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_generation;
    if (!mounted || !guard.canAccessProtectedRoutes) return;
    setState(() {
      _value = null;
      _error = null;
      _loading = true;
    });
    try {
      final value = await widget.source.loadMine(widget.conversationId);
      if (!mounted ||
          generation != _generation ||
          !guard.canAccessProtectedRoutes ||
          revision != guard.revision) {
        return;
      }
      setState(() {
        _loading = false;
        _value = value;
      });
    } on Object {
      if (!mounted || generation != _generation || revision != guard.revision) {
        return;
      }
      setState(() {
        _loading = false;
        _value = null;
        _error = 'Read state unavailable. Access may have changed.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final allowed =
        widget.source.read.sessionNavigationGuard.canAccessProtectedRoutes;
    final value = allowed ? _value : null;
    return Scaffold(
      key: const ValueKey('p24g-read-state-screen'),
      appBar: AppBar(title: const Text('Conversation read state')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            const Text(
              'The server owns each participant’s read marker. '
              'This view does not acknowledge messages or change read state.',
            ),
            const SizedBox(height: 12),
            if (!allowed)
              const Text(
                'Session unavailable. Read state is hidden.',
                key: ValueKey('p24g-read-state-locked'),
              )
            else ...[
              OutlinedButton.icon(
                key: const ValueKey('p24g-read-state-refresh'),
                onPressed: _loading ? null : _load,
                icon: const Icon(Icons.refresh_outlined),
                label: const Text('Refresh server read state'),
              ),
              if (_loading)
                const LinearProgressIndicator(
                  key: ValueKey('p24g-read-state-loading'),
                ),
              if (_error != null)
                Text(_error!, key: const ValueKey('p24g-read-state-error')),
              if (value != null)
                Text(
                  value.lastReadMessageId == null
                      ? 'No last-read message is recorded.'
                      : 'Last-read message ID: ${value.lastReadMessageId}',
                  key: const ValueKey('p24g-read-state-value'),
                ),
              const SizedBox(height: 12),
              const Text(
                'An exact unread count is not provided by this API. '
                'Message IDs and realtime notifications are not an '
                'authoritative unread counter.',
                key: ValueKey('p24g-unread-not-claimed'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
