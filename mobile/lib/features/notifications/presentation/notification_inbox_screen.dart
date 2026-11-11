import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/notifications/data/notification_models.dart';
import 'package:clinic_system_pro/features/notifications/data/notification_read_actions.dart';
import 'package:clinic_system_pro/features/notifications/data/notification_remote_data_source.dart';

String notificationErrorMessage(Object error) {
  if (error is NotificationSessionUnavailable) {
    return 'Notification session unavailable. Sign in and select a clinic.';
  }
  if (error is NotificationWriteOutcomeUnconfirmed) {
    return 'The write outcome is unconfirmed. Refresh from the server; do not retry this action.';
  }
  if (error is ApiException) {
    if (error.statusCode == 401) return 'Session rejected. Sign in again.';
    if (error.statusCode == 403) return 'Notification access is not permitted.';
    if (error.statusCode == 404) {
      return 'Notification is unavailable or no longer accessible.';
    }
    return 'Unable to confirm notifications online. Refresh to try reading again.';
  }
  return 'Notification data could not be verified. Refresh online.';
}

/// No cached sensitive notification content. A refresh clears stale page data.
class NotificationInboxScreen extends StatefulWidget {
  const NotificationInboxScreen({
    super.key,
    required this.source,
    required this.actions,
    this.onOpenLink,
  });

  final NotificationRemoteDataSource source;
  final NotificationReadActions actions;
  final Future<void> Function(int)? onOpenLink;

  @override
  State<NotificationInboxScreen> createState() =>
      _NotificationInboxScreenState();
}

class _NotificationInboxScreenState extends State<NotificationInboxScreen> {
  ClinicNotificationPage? _page;
  int _generation = 0;
  int _pageNumber = 1;
  bool _unreadOnly = false;
  bool _loading = false;
  bool _writing = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    widget.source.sessionNavigationGuard.addListener(_onSessionChanged);
    unawaited(_load(1));
  }

  @override
  void didUpdateWidget(covariant NotificationInboxScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(widget.source, oldWidget.source)) {
      oldWidget.source.sessionNavigationGuard.removeListener(_onSessionChanged);
      widget.source.sessionNavigationGuard.addListener(_onSessionChanged);
      _onSessionChanged();
    }
  }

  @override
  void dispose() {
    widget.source.sessionNavigationGuard.removeListener(_onSessionChanged);
    _generation++;
    super.dispose();
  }

  void _onSessionChanged() {
    _generation++;
    if (!mounted) return;
    setState(() {
      _page = null;
      _loading = false;
      _writing = false;
      _error =
          'Session changed. Return to sign in or reload with a valid clinic.';
    });
  }

  Future<void> _load(int requestedPage) async {
    if (requestedPage < 1) return;
    final generation = ++_generation;
    if (!mounted) return;
    setState(() {
      _page = null;
      _error = null;
      _loading = true;
      _pageNumber = requestedPage;
    });
    try {
      final result = await widget.source.list(
        page: requestedPage,
        perPage: 20,
        unreadOnly: _unreadOnly,
      );
      if (!mounted || generation != _generation) return;
      setState(() {
        _page = result;
        _loading = false;
      });
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _page = null;
        _error = notificationErrorMessage(error);
        _loading = false;
      });
    }
  }

  Future<void> _readAll() async {
    if (_writing || _loading || _page == null) return;
    final generation = ++_generation;
    setState(() {
      _writing = true;
      _page = null;
      _error = null;
    });
    try {
      await widget.actions.markAll();
      if (!mounted || generation != _generation) return;
      setState(() => _writing = false);
      await _load(1);
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _writing = false;
        _error = notificationErrorMessage(error);
      });
    }
  }

  Future<void> _openDetail(int id) async {
    if (_loading || _writing) return;
    final openLink = widget.onOpenLink;
    if (openLink != null) {
      await openLink(id);
    } else {
      // Keep the older standalone widget contract unchanged.
      await Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => NotificationDetailScreen(
            id: id,
            source: widget.source,
            actions: widget.actions,
          ),
        ),
      );
    }
    if (mounted &&
        widget.source.sessionNavigationGuard.canAccessProtectedRoutes) {
      await _load(_pageNumber);
    }
  }

  @override
  Widget build(BuildContext context) {
    final page = _page;
    return Scaffold(
      key: const ValueKey('p24a-notification-inbox'),
      appBar: AppBar(
        title: const Text('Notifications'),
        actions: [
          IconButton(
            key: const ValueKey('p24a-refresh'),
            tooltip: 'Refresh notifications',
            onPressed: _loading || _writing
                ? null
                : () => unawaited(_load(_pageNumber)),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            SwitchListTile(
              key: const ValueKey('p24a-unread-toggle'),
              title: const Text('Unread only'),
              value: _unreadOnly,
              onChanged: _loading || _writing
                  ? null
                  : (value) {
                      setState(() => _unreadOnly = value);
                      unawaited(_load(1));
                    },
            ),
            if (_loading || _writing)
              const LinearProgressIndicator(key: ValueKey('p24a-loading')),
            if (page != null) ...[
              Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 8,
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        'Page ${page.page} of ${page.pages} · ${page.total} notifications',
                      ),
                    ),
                    OutlinedButton(
                      key: const ValueKey('p24a-read-all'),
                      onPressed:
                          _loading ||
                              _writing ||
                              (page.items.isEmpty && page.total == 0)
                          ? null
                          : () => unawaited(_readAll()),
                      child: const Text('Mark all read'),
                    ),
                  ],
                ),
              ),
            ],
            Expanded(child: _buildContent()),
            if (page != null)
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  TextButton(
                    key: const ValueKey('p24a-previous-page'),
                    onPressed: _loading || _writing || page.page <= 1
                        ? null
                        : () => unawaited(_load(page.page - 1)),
                    child: const Text('Previous'),
                  ),
                  TextButton(
                    key: const ValueKey('p24a-next-page'),
                    onPressed: _loading || _writing || !page.hasNext
                        ? null
                        : () => unawaited(_load(page.page + 1)),
                    child: const Text('Next'),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildContent() {
    if (_loading || _writing) {
      return const Center(child: Text('Confirming notifications online...'));
    }
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                'No notification content is displayed until verified.',
              ),
              const SizedBox(height: 10),
              Text(
                _error!,
                key: const ValueKey('p24a-error'),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 10),
              OutlinedButton(
                key: const ValueKey('p24a-retry-get'),
                onPressed: () => unawaited(_load(_pageNumber)),
                child: const Text('Reload from server'),
              ),
            ],
          ),
        ),
      );
    }
    final page = _page;
    if (page == null) {
      return const Center(child: Text('Notification inbox unavailable.'));
    }
    if (page.items.isEmpty) {
      return const Center(child: Text('No notifications on this page.'));
    }
    return ListView.builder(
      key: const ValueKey('p24a-notification-list'),
      itemCount: page.items.length,
      itemBuilder: (context, index) {
        final item = page.items[index];
        return ListTile(
          key: ValueKey('p24a-notification-${item.id}'),
          leading: Icon(
            item.isRead ? Icons.mark_email_read_outlined : Icons.markunread,
          ),
          title: Text(item.title, maxLines: 1, overflow: TextOverflow.ellipsis),
          subtitle: Text(
            '${item.type} · ${item.priority} · ${item.isRead ? 'Read' : 'Unread'}',
          ),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => unawaited(_openDetail(item.id)),
        );
      },
    );
  }
}

/// Detail can open from the inbox or a validated ID-only deep link.
/// It always performs a fresh owner/clinic-checked backend GET before showing PHI.
class NotificationDetailScreen extends StatefulWidget {
  const NotificationDetailScreen({
    super.key,
    required this.id,
    required this.source,
    required this.actions,
  });

  final int id;
  final NotificationRemoteDataSource source;
  final NotificationReadActions actions;

  @override
  State<NotificationDetailScreen> createState() =>
      _NotificationDetailScreenState();
}

class _NotificationDetailScreenState extends State<NotificationDetailScreen> {
  ClinicNotification? _detail;
  String? _error;
  bool _loading = false;
  bool _writing = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.sessionNavigationGuard.addListener(_clear);
    unawaited(_load());
  }

  @override
  void dispose() {
    widget.source.sessionNavigationGuard.removeListener(_clear);
    _generation++;
    super.dispose();
  }

  void _clear() {
    _generation++;
    if (!mounted) return;
    setState(() {
      _detail = null;
      _loading = false;
      _writing = false;
      _error = 'Session changed. Notification detail is no longer available.';
    });
  }

  Future<void> _load() async {
    final generation = ++_generation;
    setState(() {
      _detail = null;
      _error = null;
      _loading = true;
    });
    try {
      final result = await widget.source.detail(widget.id);
      if (!mounted || generation != _generation) return;
      setState(() {
        _detail = result;
        _loading = false;
      });
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _error = notificationErrorMessage(error);
        _loading = false;
      });
    }
  }

  Future<void> _markRead() async {
    if (_writing || _loading || _detail == null || _detail!.isRead) return;
    final generation = ++_generation;
    setState(() {
      _detail = null;
      _writing = true;
    });
    try {
      final result = await widget.actions.markOne(widget.id);
      if (!mounted || generation != _generation) return;
      setState(() {
        _detail = result;
        _writing = false;
      });
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _writing = false;
        _error = notificationErrorMessage(error);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    return Scaffold(
      key: const ValueKey('p24a-notification-detail'),
      appBar: AppBar(title: const Text('Notification detail')),
      body: SafeArea(
        child: _loading || _writing
            ? const Center(child: CircularProgressIndicator())
            : _error != null
            ? Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        _error!,
                        key: const ValueKey('p24a-detail-error'),
                        textAlign: TextAlign.center,
                      ),
                      const SizedBox(height: 12),
                      TextButton(
                        key: const ValueKey('p24a-detail-reload'),
                        onPressed: () => unawaited(_load()),
                        child: const Text('Refresh detail online'),
                      ),
                    ],
                  ),
                ),
              )
            : detail == null
            ? const Center(child: Text('Notification detail unavailable.'))
            : ListView(
                padding: const EdgeInsets.all(20),
                children: [
                  Text(
                    detail.title,
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  const SizedBox(height: 12),
                  Text(
                    detail.message,
                    key: const ValueKey('p24a-detail-message'),
                  ),
                  const SizedBox(height: 16),
                  Text('Type: ${detail.type}'),
                  Text('Priority: ${detail.priority}'),
                  Text('Channel: ${detail.channel}'),
                  Text('Status: ${detail.status}'),
                  Text('Created: ${detail.createdAt ?? 'Unknown'}'),
                  const SizedBox(height: 20),
                  if (!detail.isRead)
                    FilledButton(
                      key: const ValueKey('p24a-mark-one'),
                      onPressed: () => unawaited(_markRead()),
                      child: const Text('Mark as read'),
                    )
                  else
                    const Text(
                      'Read confirmed by server.',
                      key: ValueKey('p24a-read-confirmed'),
                    ),
                  const SizedBox(height: 12),
                  const Text(
                    'Notification references do not grant access to clinical records.',
                  ),
                ],
              ),
      ),
    );
  }
}
