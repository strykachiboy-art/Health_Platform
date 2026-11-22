import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/administration/data/wd09_privileged_read_remote_data_source.dart';

/// Read-only, backend-authorized audit log or user directory.
/// Pages and details are erased on session/clinic switch before refresh.
class Wd09PrivilegedReadScreen extends StatefulWidget {
  const Wd09PrivilegedReadScreen({
    super.key,
    required this.kind,
    required this.source,
    this.onManageUser,
  });

  final Wd09ReadKind kind;
  final Wd09PrivilegedReadRemoteDataSource source;
  final ValueChanged<int>? onManageUser;

  @override
  State<Wd09PrivilegedReadScreen> createState() =>
      _Wd09PrivilegedReadScreenState();
}

class _Wd09PrivilegedReadScreenState extends State<Wd09PrivilegedReadScreen> {
  Wd09ReadPage? _page;
  Wd09ReadItem? _detail;
  bool _busy = false;
  String? _error;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.sessionNavigationGuard.addListener(_changed);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant Wd09PrivilegedReadScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (identical(oldWidget.source, widget.source) &&
        oldWidget.kind == widget.kind) {
      return;
    }
    oldWidget.source.sessionNavigationGuard.removeListener(_changed);
    widget.source.sessionNavigationGuard.addListener(_changed);
    _generation++;
    _page = null;
    _detail = null;
    _error = null;
    _busy = false;
    unawaited(_load());
  }

  @override
  void dispose() {
    _generation++;
    widget.source.sessionNavigationGuard.removeListener(_changed);
    super.dispose();
  }

  void _changed() {
    if (!mounted) return;
    _generation++;
    setState(() {
      _page = null;
      _detail = null;
      _busy = false;
      _error = 'Session or clinic changed. Records have been cleared.';
    });
  }

  bool _current(
    int generation,
    int revision,
    Wd09PrivilegedReadRemoteDataSource source,
  ) =>
      mounted &&
      identical(widget.source, source) &&
      _generation == generation &&
      source.sessionNavigationGuard.canAccessProtectedRoutes &&
      source.sessionNavigationGuard.revision == revision;

  static String _message(Object error) {
    if (error is Wd09ReadUnavailable) {
      return 'Access unavailable. Sign in with the authorized role.';
    }
    if (error is ApiException &&
        const <int>{401, 403, 404}.contains(error.statusCode)) {
      return 'Access denied by the server.';
    }
    return 'The records could not be verified from the server.';
  }

  Future<void> _load({int page = 1}) async {
    if (_busy) return;
    final source = widget.source;
    final generation = ++_generation;
    final revision = source.sessionNavigationGuard.revision;
    setState(() {
      _page = null;
      _detail = null;
      _busy = true;
      _error = null;
    });
    try {
      final result = await source.loadPage(kind: widget.kind, page: page);
      if (!_current(generation, revision, source)) return;
      setState(() {
        _page = result;
        _busy = false;
      });
    } on Object catch (error) {
      if (!_current(generation, revision, source)) return;
      setState(() {
        _error = _message(error);
        _busy = false;
      });
    }
  }

  Future<void> _open(Wd09ReadItem row) async {
    if (_busy) return;
    final source = widget.source;
    final generation = ++_generation;
    final revision = source.sessionNavigationGuard.revision;
    setState(() {
      _detail = null;
      _busy = true;
      _error = null;
    });
    try {
      final item = await source.loadDetail(kind: widget.kind, id: row.id);
      if (!_current(generation, revision, source)) return;
      setState(() {
        _detail = item;
        _busy = false;
      });
    } on Object catch (error) {
      if (!_current(generation, revision, source)) return;
      setState(() {
        _error = _message(error);
        _busy = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final audit = widget.kind == Wd09ReadKind.auditLogs;
    final page = _page;
    return ListView(
      key: ValueKey(audit ? 'wd09-audit-workspace' : 'wd09-users-workspace'),
      padding: const EdgeInsets.all(20),
      children: [
        Text(
          audit ? 'Audit logs' : 'User access directory',
          style: Theme.of(context).textTheme.headlineSmall,
        ),
        const SizedBox(height: 8),
        Text(
          audit
              ? 'Admin sees only assigned-clinic audit records. Super Admin can inspect system-wide records. Backend audit redaction remains authoritative.'
              : 'Super Admin directory. Account status, role, and clinic assignments are read from the backend.',
        ),
        const SizedBox(height: 12),
        if (_busy) const LinearProgressIndicator(key: ValueKey('wd09-loading')),
        if (_error != null) Text(_error!, key: const ValueKey('wd09-error')),
        if (!_busy && page == null)
          TextButton(
            onPressed: () => unawaited(_load()),
            child: const Text('Retry online'),
          ),
        if (page != null) ...[
          Text(
            '${page.total} records · page ${page.page}',
            key: const ValueKey('wd09-page-count'),
          ),
          if (page.items.isEmpty) const Text('No records found.'),
          for (final row in page.items)
            Card(
              child: ListTile(
                key: ValueKey('wd09-item-${row.id}'),
                title: Text(row.title),
                subtitle: Text(row.subtitle),
                trailing: const Icon(Icons.chevron_right),
                onTap: _busy ? null : () => unawaited(_open(row)),
              ),
            ),
          Row(
            children: [
              OutlinedButton(
                onPressed: _busy || page.page <= 1
                    ? null
                    : () => unawaited(_load(page: page.page - 1)),
                child: const Text('Previous'),
              ),
              const SizedBox(width: 12),
              OutlinedButton(
                onPressed: _busy || !page.hasNext
                    ? null
                    : () => unawaited(_load(page: page.page + 1)),
                child: const Text('Next'),
              ),
            ],
          ),
        ],
        if (_detail != null) ...[
          const Divider(),
          Text(
            'Verified record details',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          SelectableText(
            _detail!.details,
            key: const ValueKey('wd09-verified-details'),
          ),
          if (!audit && widget.onManageUser != null)
            FilledButton.icon(
              key: const ValueKey('wd09b-open-user-management'),
              onPressed: _busy
                  ? null
                  : () => widget.onManageUser?.call(_detail!.id),
              icon: const Icon(Icons.manage_accounts_outlined),
              label: const Text('Manage this user'),
            ),
        ],
      ],
    );
  }
}
