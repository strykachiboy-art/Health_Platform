import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/models/clinic_chat_read_models.dart';

/// CHAT-B: read-only history; no sending, read receipts, local PHI persistence,
/// or claims of realtime delivery until those capabilities are separately gated.
class ClinicChatConversationScreen extends StatefulWidget {
  const ClinicChatConversationScreen({
    super.key,
    required this.dataSource,
    required this.conversationId,
  });

  final ClinicChatReadRemoteDataSource dataSource;
  final int conversationId;

  @override
  State<ClinicChatConversationScreen> createState() =>
      _ClinicChatConversationScreenState();
}

class _ClinicChatConversationScreenState
    extends State<ClinicChatConversationScreen> {
  ClinicChatConversation? _conversation;
  ClinicChatPage<ClinicChatMessage>? _messages;
  bool _loading = false;
  String? _error;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChange);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant ClinicChatConversationScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.dataSource, widget.dataSource) ||
        oldWidget.conversationId != widget.conversationId) {
      oldWidget.dataSource.sessionNavigationGuard.removeListener(
        _onSessionChange,
      );
      widget.dataSource.sessionNavigationGuard.addListener(_onSessionChange);
      _clear();
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onSessionChange);
    super.dispose();
  }

  void _clear() {
    _generation++;
    _conversation = null;
    _messages = null;
    _error = null;
    _loading = false;
  }

  void _onSessionChange() {
    if (!mounted) {
      return;
    }
    setState(_clear);
    if (widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  bool _valid(int generation, int revision) =>
      mounted &&
      _generation == generation &&
      widget.dataSource.sessionNavigationGuard.revision == revision &&
      widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;

  Future<void> _load({int page = 1}) async {
    final guard = widget.dataSource.sessionNavigationGuard;
    final generation = ++_generation;
    final revision = guard.revision;
    if (!mounted) {
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
      _conversation = null;
      _messages = null;
    });
    if (!guard.canAccessProtectedRoutes) {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
      return;
    }
    try {
      // Always re-authorize a conversation immediately before its history GET.
      final conversation = await widget.dataSource.getConversation(
        widget.conversationId,
      );
      if (!_valid(generation, revision)) {
        return;
      }
      final messages = await widget.dataSource.listMessages(
        conversationId: widget.conversationId,
        page: page,
      );
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _conversation = conversation;
        _messages = messages;
        _loading = false;
      });
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _conversation = null;
        _messages = null;
        _loading = false;
        _error = 'Conversation unavailable or access changed.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final allowed =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;
    final conversation = _conversation;
    final page = _messages;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Conversation'),
        actions: [
          IconButton(
            key: const ValueKey('chat-b-history-refresh'),
            tooltip: 'Refresh conversation',
            onPressed: allowed && !_loading
                ? () => _load(page: page?.page ?? 1)
                : null,
            icon: const Icon(Icons.refresh_outlined),
          ),
        ],
      ),
      body: !allowed
          ? const Center(
              key: ValueKey('chat-b-history-locked'),
              child: Text('Conversation access unavailable.'),
            )
          : ListView(
              key: const ValueKey('chat-b-history'),
              padding: const EdgeInsets.all(16),
              children: [
                const Text('Read only · no message can be sent here yet'),
                if (_loading)
                  const LinearProgressIndicator(
                    key: ValueKey('chat-b-history-loading'),
                  ),
                if (_error != null)
                  Text(_error!, key: const ValueKey('chat-b-history-error')),
                if (conversation != null)
                  Text(
                    conversation.displayTitle,
                    style: Theme.of(context).textTheme.titleMedium,
                    key: const ValueKey('chat-b-history-title'),
                  ),
                if (!_loading && page != null && page.items.isEmpty)
                  const Text('No messages in this conversation.'),
                if (page != null && conversation != null) ...[
                  for (final message in page.items)
                    ListTile(
                      key: ValueKey('chat-b-message-${message.id}'),
                      title: Text(message.safePreview),
                      subtitle: Text(
                        'Sender #${message.senderId} · '
                        '${message.createdAt.toLocal()}',
                      ),
                      trailing: message.priority == ClinicMessagePriority.normal
                          ? null
                          : Text(message.priority.name.toUpperCase()),
                    ),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      IconButton(
                        key: const ValueKey('chat-b-history-previous'),
                        tooltip: 'Newer messages',
                        onPressed: page.hasPrevious && !_loading
                            ? () => _load(page: page.page - 1)
                            : null,
                        icon: const Icon(Icons.chevron_left),
                      ),
                      Text('Page ${page.page} · ${page.total} messages'),
                      IconButton(
                        key: const ValueKey('chat-b-history-next'),
                        tooltip: 'Older messages',
                        onPressed: page.hasNext && !_loading
                            ? () => _load(page: page.page + 1)
                            : null,
                        icon: const Icon(Icons.chevron_right),
                      ),
                    ],
                  ),
                ],
              ],
            ),
    );
  }
}
