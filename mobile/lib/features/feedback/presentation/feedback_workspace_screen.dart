import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/feedback/data/feedback_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';

/// Online-only feedback workspace. Backend decides which records are visible.
class FeedbackWorkspaceScreen extends StatefulWidget {
  const FeedbackWorkspaceScreen({super.key, required this.source});

  final FeedbackRemoteDataSource source;

  @override
  State<FeedbackWorkspaceScreen> createState() =>
      _FeedbackWorkspaceScreenState();
}

class _FeedbackWorkspaceScreenState extends State<FeedbackWorkspaceScreen> {
  FeedbackPage<FeedbackRecord>? _page;
  bool _loading = false;
  String? _error;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant FeedbackWorkspaceScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source)) {
      oldWidget.source.sessionNavigationGuard.removeListener(_sessionChanged);
      widget.source.sessionNavigationGuard.addListener(_sessionChanged);
      _sessionChanged();
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.sessionNavigationGuard.removeListener(_sessionChanged);
    super.dispose();
  }

  void _sessionChanged() {
    _generation++;
    if (!mounted) {
      return;
    }
    setState(() {
      _page = null;
      _error = null;
      _loading = false;
    });
    if (widget.source.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  bool _current(int token, int revision) =>
      mounted &&
      token == _generation &&
      widget.source.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.source.sessionNavigationGuard.revision == revision;

  Future<void> _load({int page = 1}) async {
    final guard = widget.source.sessionNavigationGuard;
    final token = ++_generation;
    final revision = guard.revision;
    if (!mounted || !guard.canAccessProtectedRoutes) {
      return;
    }
    setState(() {
      _loading = true;
      _page = null;
      _error = null;
    });
    try {
      final result = await widget.source.list(page: page);
      if (!_current(token, revision)) {
        return;
      }
      setState(() {
        _page = result;
        _loading = false;
      });
    } on Object {
      if (!_current(token, revision)) {
        return;
      }
      setState(() {
        _loading = false;
        _error = 'Feedback unavailable. Refresh after checking your access.';
      });
    }
  }

  Future<void> _create() async {
    if (_loading ||
        !widget.source.sessionNavigationGuard.canAccessProtectedRoutes) {
      return;
    }
    final completed = await Navigator.of(context).push<bool>(
      MaterialPageRoute<bool>(
        builder: (_) => FeedbackCreateScreen(source: widget.source),
      ),
    );
    if (mounted && completed == true) {
      unawaited(_load());
    }
  }

  Future<void> _open(int feedbackId) async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) =>
            FeedbackDetailScreen(source: widget.source, feedbackId: feedbackId),
      ),
    );
    if (mounted &&
        widget.source.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  @override
  Widget build(BuildContext context) {
    final allowed =
        widget.source.sessionNavigationGuard.canAccessProtectedRoutes;
    final page = _page;
    return Scaffold(
      key: const ValueKey('p24h-feedback-workspace'),
      appBar: AppBar(
        title: const Text('Feedback'),
        actions: [
          IconButton(
            key: const ValueKey('p24h-feedback-refresh'),
            onPressed: allowed && !_loading
                ? () => _load(page: page?.page ?? 1)
                : null,
            tooltip: 'Refresh feedback',
            icon: const Icon(Icons.refresh_outlined),
          ),
        ],
      ),
      body: !allowed
          ? const Center(child: Text('Feedback unavailable. Sign in again.'))
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Text('Server-authorized feedback · online only'),
                const SizedBox(height: 12),
                FilledButton.icon(
                  key: const ValueKey('p24h-feedback-create'),
                  onPressed: !_loading && !widget.source.writeLocked
                      ? _create
                      : null,
                  icon: const Icon(Icons.add_comment_outlined),
                  label: const Text('Submit feedback'),
                ),
                if (widget.source.outcomeUnconfirmed)
                  const Text(
                    'A previous write has an unknown outcome. Do not resubmit.',
                    key: ValueKey('p24h-feedback-uncertain'),
                  ),
                if (_loading) const LinearProgressIndicator(),
                if (_error != null)
                  Text(_error!, key: const ValueKey('p24h-feedback-error')),
                if (page != null && page.items.isEmpty)
                  const Text('No feedback in your authorized scope.'),
                if (page != null) ...[
                  for (final item in page.items)
                    ListTile(
                      key: ValueKey('p24h-feedback-item-${item.id}'),
                      title: Text(item.subject),
                      subtitle: Text('${item.status} · ${item.type}'),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => _open(item.id),
                    ),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      IconButton(
                        key: const ValueKey('p24h-feedback-previous'),
                        tooltip: 'Previous feedback page',
                        onPressed: !_loading && page.hasPrevious
                            ? () => _load(page: page.page - 1)
                            : null,
                        icon: const Icon(Icons.chevron_left),
                      ),
                      Text('Page ${page.page} · ${page.total} feedback items'),
                      IconButton(
                        key: const ValueKey('p24h-feedback-next'),
                        tooltip: 'Next feedback page',
                        onPressed: !_loading && page.hasNext
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

class FeedbackCreateScreen extends StatefulWidget {
  const FeedbackCreateScreen({super.key, required this.source});

  final FeedbackRemoteDataSource source;

  @override
  State<FeedbackCreateScreen> createState() => _FeedbackCreateScreenState();
}

class _FeedbackCreateScreenState extends State<FeedbackCreateScreen> {
  final _subject = TextEditingController();
  final _message = TextEditingController();
  String _type = feedbackTypes.first;
  String _category = feedbackCategories.first;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _subject.dispose();
    _message.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_busy ||
        widget.source.writeLocked ||
        !widget.source.sessionNavigationGuard.canAccessProtectedRoutes) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.source.create(
        type: _type,
        category: _category,
        subject: _subject.text,
        message: _message.text,
      );
      if (mounted &&
          widget.source.sessionNavigationGuard.canAccessProtectedRoutes) {
        Navigator.of(context).pop(true);
      }
    } on FeedbackOutcomeUnconfirmed {
      if (mounted) {
        setState(
          () => _error = 'Outcome unknown. Do not resubmit; have an administrator reconcile server records.',
        );
      }
    } on Object {
      if (mounted) {
        setState(
          () => _error =
              'Feedback submission blocked. Review your access and fields.',
        );
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final enabled =
        widget.source.sessionNavigationGuard.canAccessProtectedRoutes &&
        !_busy &&
        !widget.source.writeLocked;
    return Scaffold(
      key: const ValueKey('p24h-feedback-create-screen'),
      appBar: AppBar(title: const Text('Submit feedback')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text(
              'Feedback is submitted online. It is not placed into an offline queue.',
            ),
            DropdownButtonFormField<String>(
              key: const ValueKey('p24h-type'),
              initialValue: _type,
              decoration: const InputDecoration(labelText: 'Feedback type'),
              items: [
                for (final item in feedbackTypes)
                  DropdownMenuItem(
                    value: item,
                    child: Text(item.replaceAll('_', ' ')),
                  ),
              ],
              onChanged: enabled
                  ? (value) {
                      if (value != null) {
                        setState(() => _type = value);
                      }
                    }
                  : null,
            ),
            DropdownButtonFormField<String>(
              key: const ValueKey('p24h-category'),
              initialValue: _category,
              decoration: const InputDecoration(labelText: 'Category'),
              items: [
                for (final item in feedbackCategories)
                  DropdownMenuItem(
                    value: item,
                    child: Text(item.replaceAll('_', ' ')),
                  ),
              ],
              onChanged: enabled
                  ? (value) {
                      if (value != null) {
                        setState(() => _category = value);
                      }
                    }
                  : null,
            ),
            TextField(
              key: const ValueKey('p24h-subject'),
              controller: _subject,
              enabled: enabled,
              maxLength: 200,
              decoration: const InputDecoration(labelText: 'Subject'),
            ),
            TextField(
              key: const ValueKey('p24h-message'),
              controller: _message,
              enabled: enabled,
              maxLines: 5,
              maxLength: 10000,
              decoration: const InputDecoration(labelText: 'Message'),
            ),
            if (_busy) const LinearProgressIndicator(),
            if (_error != null)
              Text(_error!, key: const ValueKey('p24h-create-error')),
            FilledButton(
              key: const ValueKey('p24h-submit'),
              onPressed: enabled ? _submit : null,
              child: const Text('Submit once'),
            ),
          ],
        ),
      ),
    );
  }
}

class FeedbackDetailScreen extends StatefulWidget {
  const FeedbackDetailScreen({
    super.key,
    required this.source,
    required this.feedbackId,
  });

  final FeedbackRemoteDataSource source;
  final int feedbackId;

  @override
  State<FeedbackDetailScreen> createState() => _FeedbackDetailScreenState();
}

class _FeedbackDetailScreenState extends State<FeedbackDetailScreen> {
  final _comment = TextEditingController();
  final _resolution = TextEditingController();
  FeedbackRecord? _record;
  FeedbackPage<FeedbackComment>? _comments;
  bool _loading = false;
  bool _busy = false;
  String? _error;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void dispose() {
    _generation++;
    widget.source.sessionNavigationGuard.removeListener(_sessionChanged);
    _comment.dispose();
    _resolution.dispose();
    super.dispose();
  }

  void _sessionChanged() {
    _generation++;
    if (!mounted) {
      return;
    }
    setState(() {
      _record = null;
      _comments = null;
      _error = null;
      _loading = false;
    });
    if (widget.source.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  Future<void> _load({int commentPage = 1}) async {
    final guard = widget.source.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_generation;
    if (!mounted || !guard.canAccessProtectedRoutes) {
      return;
    }
    setState(() {
      _record = null;
      _comments = null;
      _error = null;
      _loading = true;
    });
    try {
      final record = await widget.source.detail(widget.feedbackId);
      final comments = await widget.source.comments(
        widget.feedbackId,
        page: commentPage,
      );
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _record = record;
        _comments = comments;
        _loading = false;
      });
    } on Object {
      if (!mounted || generation != _generation || guard.revision != revision) {
        return;
      }
      setState(() {
        _loading = false;
        _error = 'Feedback detail unavailable or access changed.';
      });
    }
  }

  Future<void> _addComment() async {
    if (_busy || _record == null || widget.source.writeLocked) {
      return;
    }
    final revision = widget.source.sessionNavigationGuard.revision;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.source.addComment(widget.feedbackId, _comment.text);
      if (!mounted ||
          widget.source.sessionNavigationGuard.revision != revision) {
        return;
      }
      _comment.clear();
      await _load();
    } on FeedbackOutcomeUnconfirmed {
      if (mounted) {
        setState(() => _error = 'Comment outcome unknown. Do not resubmit.');
      }
    } on Object {
      if (mounted) {
        setState(() => _error = 'Comment blocked or access changed.');
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  static List<String> _allowedNext(String current) => switch (current) {
    'open' => ['triaged', 'rejected'],
    'triaged' => ['in_progress', 'rejected'],
    'in_progress' => ['resolved', 'rejected'],
    'resolved' => ['closed', 'reopened'],
    'closed' => ['reopened'],
    'reopened' => ['triaged', 'in_progress'],
    'rejected' => ['reopened'],
    _ => <String>[],
  };

  Future<void> _changeStatus(String status) async {
    if (_busy || _record == null || widget.source.writeLocked) {
      return;
    }
    final approved = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Confirm feedback status'),
        content: Text('Change status to $status?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('p24h-status-confirm'),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Confirm'),
          ),
        ],
      ),
    );
    if (!mounted || approved != true) {
      return;
    }
    final revision = widget.source.sessionNavigationGuard.revision;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.source.changeStatus(
        widget.feedbackId,
        status: status,
        resolutionNote: status == 'resolved' ? _resolution.text : null,
      );
      if (!mounted ||
          widget.source.sessionNavigationGuard.revision != revision) {
        return;
      }
      _resolution.clear();
      await _load();
    } on FeedbackOutcomeUnconfirmed {
      if (mounted) {
        setState(() => _error = 'Status outcome unknown. Do not repeat.');
      }
    } on Object {
      if (mounted) {
        setState(
          () => _error = 'Status transition blocked. Refresh and review.',
        );
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final allowed =
        widget.source.sessionNavigationGuard.canAccessProtectedRoutes;
    final record = allowed ? _record : null;
    final comments = allowed ? _comments : null;
    final canMutate =
        allowed && !_busy && !_loading && !widget.source.writeLocked;
    return Scaffold(
      key: const ValueKey('p24h-feedback-detail-screen'),
      appBar: AppBar(
        title: const Text('Feedback detail'),
        actions: [
          IconButton(
            key: const ValueKey('p24h-detail-refresh'),
            tooltip: 'Refresh feedback detail',
            onPressed: allowed && !_busy ? _load : null,
            icon: const Icon(Icons.refresh_outlined),
          ),
        ],
      ),
      body: !allowed
          ? const Center(child: Text('Feedback hidden. Session unavailable.'))
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                if (_loading) const LinearProgressIndicator(),
                if (_busy) const LinearProgressIndicator(),
                if (_error != null)
                  Text(_error!, key: const ValueKey('p24h-detail-error')),
                if (record != null) ...[
                  Text(
                    record.subject,
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  Text(
                    'Status: ${record.status} · Priority: ${record.priority}',
                  ),
                  const SizedBox(height: 12),
                  Text(
                    record.message,
                    key: const ValueKey('p24h-detail-message'),
                  ),
                  if (record.resolutionNote != null)
                    Text('Resolution: ${record.resolutionNote}'),
                  const Divider(),
                  // UI is a hint only; backend enforces admin/super-admin RBAC.
                  FutureBuilder<bool>(
                    future: _isManager(),
                    builder: (context, snapshot) {
                      if (snapshot.data != true) {
                        return const SizedBox.shrink();
                      }
                      return Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          TextField(
                            key: const ValueKey('p24h-resolution-note'),
                            controller: _resolution,
                            maxLength: 10000,
                            enabled: canMutate,
                            decoration: const InputDecoration(
                              labelText:
                                  'Resolution note (required to resolve)',
                            ),
                          ),
                          for (final next in _allowedNext(record.status))
                            OutlinedButton(
                              key: ValueKey('p24h-status-$next'),
                              onPressed: canMutate
                                  ? () => _changeStatus(next)
                                  : null,
                              child: Text('Set ${next.replaceAll('_', ' ')}'),
                            ),
                        ],
                      );
                    },
                  ),
                  const Divider(),
                  Text(
                    'Comments',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ],
                if (comments != null) ...[
                  for (final item in comments.items)
                    ListTile(
                      key: ValueKey('p24h-comment-${item.id}'),
                      title: Text(item.body),
                      subtitle: Text('User #${item.authorId}'),
                    ),
                  Row(
                    children: [
                      IconButton(
                        key: const ValueKey('p24h-comments-previous'),
                        tooltip: 'Previous comments',
                        onPressed: canMutate && comments.hasPrevious
                            ? () => _load(commentPage: comments.page - 1)
                            : null,
                        icon: const Icon(Icons.chevron_left),
                      ),
                      Text(
                        'Page ${comments.page} · ${comments.total} comments',
                      ),
                      IconButton(
                        key: const ValueKey('p24h-comments-next'),
                        tooltip: 'Next comments',
                        onPressed: canMutate && comments.hasNext
                            ? () => _load(commentPage: comments.page + 1)
                            : null,
                        icon: const Icon(Icons.chevron_right),
                      ),
                    ],
                  ),
                ],
                if (record != null) ...[
                  TextField(
                    key: const ValueKey('p24h-comment-input'),
                    controller: _comment,
                    enabled: canMutate,
                    maxLength: 10000,
                    maxLines: 3,
                    decoration: const InputDecoration(
                      labelText: 'Add a comment',
                    ),
                  ),
                  FilledButton(
                    key: const ValueKey('p24h-comment-send'),
                    onPressed: canMutate ? _addComment : null,
                    child: const Text('Post comment once'),
                  ),
                ],
                if (widget.source.outcomeUnconfirmed)
                  const Text(
                    'Write outcome unknown. Actions locked until reconciliation.',
                    key: ValueKey('p24h-detail-uncertain'),
                  ),
              ],
            ),
    );
  }

  Future<bool> _isManager() async {
    try {
      final guard = widget.source.sessionNavigationGuard;
      if (!guard.canAccessProtectedRoutes) {
        return false;
      }
      final before = guard.revision;
      final state = await widget.source.loadSession();
      return mounted &&
          guard.canAccessProtectedRoutes &&
          guard.revision == before &&
          state.disposition == AuthSessionDisposition.ready &&
          const <String>[
            'admin',
            'super_admin',
          ].contains(state.session?.role.toLowerCase());
    } on Object {
      return false;
    }
  }
}
