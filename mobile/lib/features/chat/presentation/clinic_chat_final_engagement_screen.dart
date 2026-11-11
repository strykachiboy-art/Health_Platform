import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/chat/data/clinic_chat_final_engagement_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';

/// A deliberate server-owned chat engagement workflow. No auto-read on view,
/// no implicit message delivery, no optimistic unread/reaction counters.
class ClinicChatFinalEngagementScreen extends StatefulWidget {
  const ClinicChatFinalEngagementScreen({
    super.key,
    required this.read,
    required this.conversationId,
    this.messageId,
  });

  final ClinicChatReadRemoteDataSource read;
  final int conversationId;
  final int? messageId;

  @override
  State<ClinicChatFinalEngagementScreen> createState() =>
      _ClinicChatFinalEngagementScreenState();
}

class _ClinicChatFinalEngagementScreenState
    extends State<ClinicChatFinalEngagementScreen> {
  late ClinicChatFinalEngagementRemoteDataSource _source;
  final _mentionedUser = TextEditingController();
  ClinicChatAuthoritativeUnread? _unread;
  List<ClinicChatReactionRecord> _reactions = const [];
  List<ClinicChatMentionRecord> _mentions = const [];
  String _reaction = 'ack';
  String? _error;
  bool _loading = false;
  bool _busy = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    _source = ClinicChatFinalEngagementRemoteDataSource(read: widget.read);
    widget.read.sessionNavigationGuard.addListener(_onGuardChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_load());
    });
  }

  @override
  void dispose() {
    _generation++;
    widget.read.sessionNavigationGuard.removeListener(_onGuardChanged);
    _mentionedUser.dispose();
    super.dispose();
  }

  void _onGuardChanged() {
    _generation++;
    if (!mounted) return;
    setState(() {
      _unread = null;
      _reactions = const [];
      _mentions = const [];
      _error = null;
      _loading = false;
      _busy = false;
    });
  }

  bool _current(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.read.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.read.sessionNavigationGuard.revision == revision;

  Future<void> _load() async {
    if (_busy ||
        _source.outcomeUnconfirmed ||
        !widget.read.sessionNavigationGuard.canAccessProtectedRoutes) {
      return;
    }
    final revision = widget.read.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _loading = true;
      _error = null;
      _unread = null;
      _reactions = const [];
      _mentions = const [];
    });
    try {
      final unread = await _source.loadUnread(widget.conversationId);
      final messageId = widget.messageId;
      final reactions = messageId == null
          ? <ClinicChatReactionRecord>[]
          : await _source.loadReactions(widget.conversationId, messageId);
      final mentions = messageId == null
          ? <ClinicChatMentionRecord>[]
          : await _source.loadMentions(widget.conversationId, messageId);
      if (!_current(generation, revision)) return;
      setState(() {
        _unread = unread;
        _reactions = reactions;
        _mentions = mentions;
        _loading = false;
      });
    } on Object {
      if (!_current(generation, revision)) return;
      setState(() {
        _loading = false;
        _error = 'Server chat state unavailable or access changed.';
      });
    }
  }

  Future<void> _write(Future<void> Function() action) async {
    if (_busy ||
        _loading ||
        _source.writeLocked ||
        _unread == null ||
        !widget.read.sessionNavigationGuard.canAccessProtectedRoutes) {
      return;
    }
    final revision = widget.read.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await action();
      if (!_current(generation, revision)) return;
      setState(() => _busy = false);
      await _load();
    } on Object {
      if (!_current(generation, revision)) return;
      setState(() {
        _busy = false;
        _unread = null;
        _reactions = const [];
        _mentions = const [];
        _error = _source.outcomeUnconfirmed
            ? 'Action outcome unconfirmed. Do not retry in this screen.'
            : 'Action denied or data changed. Review the server state.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final allowed = widget.read.sessionNavigationGuard.canAccessProtectedRoutes;
    final active =
        allowed &&
        !_loading &&
        !_busy &&
        !_source.writeLocked &&
        _unread != null;
    final message = widget.messageId;
    return Scaffold(
      key: const ValueKey('p24g-final-engagement'),
      appBar: AppBar(title: const Text('Chat server state')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text(
              'Unread counts and message engagement are verified by '
              'the backend. Opening a message does not mark it read.',
            ),
            const SizedBox(height: 12),
            if (!allowed)
              const Text(
                'Chat unavailable after session change.',
                key: ValueKey('p24g-final-session-locked'),
              )
            else ...[
              OutlinedButton.icon(
                key: const ValueKey('p24g-final-refresh'),
                onPressed: _loading || _busy || _source.outcomeUnconfirmed
                    ? null
                    : _load,
                icon: const Icon(Icons.refresh),
                label: const Text('Refresh server state'),
              ),
              if (_loading) const LinearProgressIndicator(),
              if (_error != null)
                Text(_error!, key: const ValueKey('p24g-final-error')),
              if (_unread != null) ...[
                Text(
                  'Unread messages: ${_unread!.unreadCount}',
                  key: const ValueKey('p24g-final-unread-count'),
                ),
                Text(
                  _unread!.lastReadMessageId == null
                      ? 'No read acknowledgement recorded.'
                      : 'Last acknowledged message: ${_unread!.lastReadMessageId}',
                ),
              ],
              if (message != null && _unread != null) ...[
                const Divider(),
                Text('Selected message #$message'),
                FilledButton.icon(
                  key: const ValueKey('p24g-final-acknowledge'),
                  onPressed: active
                      ? () => _write(() async {
                          await _source.acknowledge(
                            widget.conversationId,
                            message,
                          );
                        })
                      : null,
                  icon: const Icon(Icons.done_all),
                  label: const Text('Mark through this message as read'),
                ),
                const SizedBox(height: 12),
                Text(
                  'Reactions: ${_reactions.map((r) => r.reaction).join(', ')}',
                  key: const ValueKey('p24g-final-reactions'),
                ),
                for (final item in _reactions)
                  if (item.userId == _unread?.userId)
                    TextButton(
                      key: ValueKey('p24g-final-remove-reaction-${item.id}'),
                      onPressed: active
                          ? () => _write(() async {
                              await _source.removeOwnReaction(
                                widget.conversationId,
                                message,
                                item,
                              );
                            })
                          : null,
                      child: Text('Remove my ${item.reaction} reaction'),
                    ),
                DropdownButton<String>(
                  value: _reaction,
                  onChanged: active
                      ? (value) {
                          if (value != null) setState(() => _reaction = value);
                        }
                      : null,
                  items: const ['ack', 'like', 'heart', 'thanks']
                      .map(
                        (value) => DropdownMenuItem<String>(
                          value: value,
                          child: Text(value),
                        ),
                      )
                      .toList(),
                ),
                FilledButton(
                  key: const ValueKey('p24g-final-react'),
                  onPressed: active
                      ? () => _write(() async {
                          await _source.react(
                            widget.conversationId,
                            message,
                            _reaction,
                          );
                        })
                      : null,
                  child: const Text('Add reaction'),
                ),
                const SizedBox(height: 12),
                Text(
                  'Mentioned participant IDs: '
                  '${_mentions.map((m) => m.userId).join(', ')}',
                  key: const ValueKey('p24g-final-mentions'),
                ),
                TextField(
                  key: const ValueKey('p24g-final-mention-user'),
                  controller: _mentionedUser,
                  enabled: active,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText:
                        'Accepted participant user ID (own messages only)',
                  ),
                ),
                FilledButton(
                  key: const ValueKey('p24g-final-mention'),
                  onPressed: active
                      ? () {
                          final id = int.tryParse(_mentionedUser.text.trim());
                          if (id == null || id < 1) {
                            setState(
                              () => _error = 'Enter a valid participant ID.',
                            );
                            return;
                          }
                          unawaited(
                            _write(() async {
                              await _source.mentionUser(
                                widget.conversationId,
                                message,
                                id,
                              );
                            }),
                          );
                        }
                      : null,
                  child: const Text('Mention verified participant'),
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }
}
