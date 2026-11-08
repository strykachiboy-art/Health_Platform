import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_text_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/models/clinic_chat_read_models.dart';

/// CHAT-C: one authenticated REST mutation at a time, no optimistic delivery.
/// No media, Socket.IO, offline queue, idempotency claim, or automatic replay.
class ClinicChatTextConversationScreen extends StatefulWidget {
  const ClinicChatTextConversationScreen({
    super.key,
    required this.dataSource,
    required this.conversationId,
  });

  final ClinicChatReadRemoteDataSource dataSource;
  final int conversationId;

  @override
  State<ClinicChatTextConversationScreen> createState() =>
      _ClinicChatTextConversationScreenState();
}

class _ClinicChatTextConversationScreenState
    extends State<ClinicChatTextConversationScreen> {
  final _draft = TextEditingController();
  late ClinicChatTextMutationRemoteDataSource _writes;
  ClinicChatConversation? _conversation;
  ClinicChatPage<ClinicChatMessage>? _page;
  ClinicChatMessage? _replying;
  ClinicChatMessage? _editing;
  ClinicMessagePriority _priority = ClinicMessagePriority.normal;
  bool _loading = false;
  bool _busy = false;
  bool _uncertain = false;
  String? _message;
  int _generation = 0;
  int? _signedInUserId;

  @override
  void initState() {
    super.initState();
    _writes = ClinicChatTextMutationRemoteDataSource(read: widget.dataSource);
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant ClinicChatTextConversationScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.dataSource, widget.dataSource) ||
        oldWidget.conversationId != widget.conversationId) {
      oldWidget.dataSource.sessionNavigationGuard.removeListener(
        _onSessionChanged,
      );
      widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
      _writes = ClinicChatTextMutationRemoteDataSource(read: widget.dataSource);
      _clear();
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onSessionChanged);
    _draft.dispose();
    super.dispose();
  }

  void _clear() {
    _generation++;
    _conversation = null;
    _page = null;
    _replying = null;
    _editing = null;
    _priority = ClinicMessagePriority.normal;
    _signedInUserId = null;
    _loading = false;
    _busy = false;
    _uncertain = _writes.outcomeUnconfirmed;
    _message = null;
    _draft.clear();
  }

  void _onSessionChanged() {
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
      widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.dataSource.sessionNavigationGuard.revision == revision;

  bool get _canWrite =>
      mounted &&
      widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes &&
      !_loading &&
      !_busy &&
      !_uncertain &&
      !_writes.writeLocked &&
      _conversation?.status == ClinicChatStatus.active;

  Future<void> _load({int page = 1}) async {
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_generation;
    if (!mounted) {
      return;
    }
    setState(() {
      _loading = true;
      _conversation = null;
      _page = null;
      if (!_uncertain) {
        _message = null;
      }
    });
    if (!guard.canAccessProtectedRoutes) {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
      return;
    }
    try {
      final sessionBefore = await widget.dataSource.loadSession();
      final actorId = sessionBefore.session?.userId;
      if (sessionBefore.disposition != AuthSessionDisposition.ready ||
          actorId == null ||
          actorId <= 0) {
        throw const ClinicChatSessionUnavailable();
      }
      final conversation = await widget.dataSource.getConversation(
        widget.conversationId,
      );
      if (!_valid(generation, revision)) {
        return;
      }
      final pageData = await widget.dataSource.listMessages(
        conversationId: widget.conversationId,
        page: page,
      );
      if (!_valid(generation, revision)) {
        return;
      }
      final sessionAfter = await widget.dataSource.loadSession();
      if (!_valid(generation, revision) ||
          sessionAfter.disposition != AuthSessionDisposition.ready ||
          sessionAfter.session?.userId != actorId ||
          sessionAfter.session?.accessToken !=
              sessionBefore.session?.accessToken ||
          sessionAfter.session?.clinicContextId !=
              sessionBefore.session?.clinicContextId) {
        throw const ClinicChatSessionUnavailable();
      }
      setState(() {
        _signedInUserId = actorId;
        _conversation = conversation;
        _page = pageData;
        _loading = false;
      });
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _conversation = null;
        _page = null;
        _signedInUserId = null;
        _loading = false;
        _replying = null;
        _editing = null;
        _message = 'Conversation unavailable or access changed.';
      });
    }
  }

  void _reply(ClinicChatMessage item) {
    if (!_canWrite || item.status == ClinicMessageStatus.deleted) {
      return;
    }
    setState(() {
      _replying = item;
      _editing = null;
      _draft.clear();
    });
  }

  void _edit(ClinicChatMessage item) {
    if (!_canWrite ||
        item.status == ClinicMessageStatus.deleted ||
        item.kind != ClinicMessageKind.text) {
      return;
    }
    setState(() {
      _editing = item;
      _replying = null;
      _priority = ClinicMessagePriority.normal;
      _draft.text = item.content ?? '';
    });
  }

  void _cancelComposer() {
    if (_busy) {
      return;
    }
    setState(() {
      _editing = null;
      _replying = null;
      _draft.clear();
      _priority = ClinicMessagePriority.normal;
    });
  }

  Future<void> _execute(
    Future<ClinicChatMessage> Function(ClinicChatConversation) action,
  ) async {
    final conversation = _conversation;
    if (!_canWrite || conversation == null) {
      return;
    }
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      final confirmed = await action(conversation);
      if (!mounted ||
          !guard.canAccessProtectedRoutes ||
          guard.revision != revision) {
        return;
      }
      setState(() {
        _editing = null;
        _replying = null;
        _priority = ClinicMessagePriority.normal;
        _draft.clear();
      });
      await _load();
      if (mounted &&
          guard.revision == revision &&
          guard.canAccessProtectedRoutes &&
          _conversation != null) {
        setState(() {
          _message =
              'Server confirmed message #${confirmed.id}. '
              'Delivery is managed separately.';
        });
      }
    } on ClinicChatTextOutcomeUnconfirmed {
      if (mounted &&
          guard.revision == revision &&
          guard.canAccessProtectedRoutes) {
        setState(() {
          _uncertain = true;
          _conversation = null;
          _page = null;
          _draft.clear();
          _editing = null;
          _replying = null;
          _message =
              'Write outcome unconfirmed. Do not resend. '
              'Refresh server history to reconcile before leaving this screen.';
        });
      }
    } on Object {
      if (mounted &&
          guard.revision == revision &&
          guard.canAccessProtectedRoutes) {
        setState(() {
          _message =
              'Action blocked or clinical chat changed. '
              'Refresh and review before another action.';
        });
      }
    } finally {
      if (mounted && guard.revision == revision) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _sendOrEdit() async {
    if (!_canWrite) {
      return;
    }
    final content = _draft.text;
    final editing = _editing;
    if (editing != null) {
      await _execute(
        (convo) => _writes.editText(
          reviewed: convo,
          selected: editing,
          content: content,
        ),
      );
    } else {
      await _execute(
        (convo) => _writes.sendText(
          reviewed: convo,
          content: content,
          replyToMessageId: _replying?.id,
          priority: _priority,
        ),
      );
    }
  }

  Future<void> _delete(ClinicChatMessage selected) async {
    if (!_canWrite) {
      return;
    }
    final approved = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete message?'),
        content: const Text(
          'The server retains its audit history. Confirm this action.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('chat-c-delete-confirm'),
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (!mounted || approved != true || !_canWrite) {
      return;
    }
    await _execute(
      (convo) => _writes.deleteText(reviewed: convo, selected: selected),
    );
  }

  @override
  Widget build(BuildContext context) {
    final guard = widget.dataSource.sessionNavigationGuard;
    final allowed = guard.canAccessProtectedRoutes;
    final conversation = _conversation;
    final page = _page;
    final enabled = _canWrite;
    final editor = _editing;
    final replying = _replying;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Clinic conversation'),
        actions: [
          IconButton(
            key: const ValueKey('chat-c-refresh'),
            tooltip: 'Refresh server history',
            onPressed: allowed && !_loading && !_busy
                ? () => _load(page: page?.page ?? 1)
                : null,
            icon: const Icon(Icons.refresh_outlined),
          ),
        ],
      ),
      body: !allowed
          ? const Center(
              key: ValueKey('chat-c-locked'),
              child: Text('Conversation access unavailable.'),
            )
          : ListView(
              key: const ValueKey('chat-c-history'),
              padding: const EdgeInsets.all(16),
              children: [
                const Text(
                  'Online text messaging · no media or realtime delivery',
                ),
                if (_loading)
                  const LinearProgressIndicator(
                    key: ValueKey('chat-c-loading'),
                  ),
                if (_busy)
                  const LinearProgressIndicator(
                    key: ValueKey('chat-c-writing'),
                  ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('chat-c-message-status')),
                if (_uncertain)
                  const Text(
                    'Further writes are locked for this screen. '
                    'Review the server state before opening a new session.',
                    key: ValueKey('chat-c-unconfirmed'),
                  ),
                if (conversation != null)
                  Text(
                    conversation.displayTitle,
                    key: const ValueKey('chat-c-title'),
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                if (conversation?.status != ClinicChatStatus.active &&
                    conversation != null)
                  const Text('This conversation is not active: read only.'),
                if (!_loading && page != null && page.items.isEmpty)
                  const Text('No messages in this conversation.'),
                if (conversation != null && page != null) ...[
                  for (final item in page.items) ...[
                    ListTile(
                      key: ValueKey('chat-c-message-${item.id}'),
                      title: Text(item.safePreview),
                      subtitle: Text(
                        'Sender #${item.senderId} · ${item.createdAt.toLocal()}'
                        '${item.replyToMessageId == null ? '' : ' · reply #${item.replyToMessageId}'}',
                      ),
                      trailing: item.priority == ClinicMessagePriority.normal
                          ? null
                          : Text(item.priority.name.toUpperCase()),
                    ),
                    if (enabled && item.status != ClinicMessageStatus.deleted)
                      Wrap(
                        spacing: 8,
                        children: [
                          TextButton(
                            key: ValueKey('chat-c-reply-${item.id}'),
                            onPressed: () => _reply(item),
                            child: const Text('Reply'),
                          ),
                          if (item.kind == ClinicMessageKind.text &&
                              item.senderId == _currentUserIdHint)
                            TextButton(
                              key: ValueKey('chat-c-edit-${item.id}'),
                              onPressed: () => _edit(item),
                              child: const Text('Edit'),
                            ),
                          if (item.kind == ClinicMessageKind.text &&
                              item.senderId == _currentUserIdHint)
                            TextButton(
                              key: ValueKey('chat-c-delete-${item.id}'),
                              onPressed: () => _delete(item),
                              child: const Text('Delete'),
                            ),
                        ],
                      ),
                    const Divider(),
                  ],
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      IconButton(
                        key: const ValueKey('chat-c-previous'),
                        tooltip: 'Newer messages',
                        onPressed: page.hasPrevious && !_loading && !_busy
                            ? () => _load(page: page.page - 1)
                            : null,
                        icon: const Icon(Icons.chevron_left),
                      ),
                      Text('Page ${page.page} · ${page.total} messages'),
                      IconButton(
                        key: const ValueKey('chat-c-next'),
                        tooltip: 'Older messages',
                        onPressed: page.hasNext && !_loading && !_busy
                            ? () => _load(page: page.page + 1)
                            : null,
                        icon: const Icon(Icons.chevron_right),
                      ),
                    ],
                  ),
                ],
              ],
            ),
      bottomNavigationBar: !allowed
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (editor != null || replying != null)
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              editor != null
                                  ? 'Editing message #${editor.id}'
                                  : 'Replying to message #${replying!.id}',
                            ),
                          ),
                          TextButton(
                            key: const ValueKey('chat-c-cancel-action'),
                            onPressed: !_busy ? _cancelComposer : null,
                            child: const Text('Cancel'),
                          ),
                        ],
                      ),
                    TextField(
                      key: const ValueKey('chat-c-compose'),
                      controller: _draft,
                      enabled: enabled,
                      maxLines: 3,
                      maxLength: 5000,
                      onChanged: (_) => setState(() {}),
                      decoration: InputDecoration(
                        labelText: editor != null
                            ? 'Edit text message'
                            : 'Write text message',
                      ),
                    ),
                    Row(
                      children: [
                        if (editor == null)
                          DropdownButton<ClinicMessagePriority>(
                            key: const ValueKey('chat-c-priority'),
                            value: _priority,
                            onChanged: enabled
                                ? (value) {
                                    if (value != null) {
                                      setState(() => _priority = value);
                                    }
                                  }
                                : null,
                            items: [
                              for (final priority
                                  in ClinicMessagePriority.values)
                                DropdownMenuItem(
                                  value: priority,
                                  child: Text(priority.name.toUpperCase()),
                                ),
                            ],
                          ),
                        const Spacer(),
                        FilledButton.icon(
                          key: const ValueKey('chat-c-submit'),
                          onPressed:
                              enabled &&
                                  _draft.text.trim().isNotEmpty &&
                                  _draft.text.trim().length <= 5000
                              ? _sendOrEdit
                              : null,
                          icon: const Icon(Icons.send_outlined),
                          label: Text(editor != null ? 'Save edit' : 'Send'),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
    );
  }

  // The server enforces authorization; this is only a UI hint.
  int? get _currentUserIdHint => _signedInUserId;
}
