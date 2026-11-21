import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/administration/data/wd05_administration_read_remote_data_source.dart';

/// Read-only server worklist. No local statistics, edits, exports or replay.
class Wd05AdministrationReadScreen extends StatefulWidget {
  const Wd05AdministrationReadScreen({
    super.key,
    required this.section,
    required this.source,
  });

  final Wd05AdministrationSection section;
  final Wd05AdministrationReadRemoteDataSource source;

  @override
  State<Wd05AdministrationReadScreen> createState() =>
      _Wd05AdministrationReadScreenState();
}

class _Wd05AdministrationReadScreenState
    extends State<Wd05AdministrationReadScreen> {
  Wd05AdministrationReadPage? _page;
  String? _error;
  bool _loading = true;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.sessionNavigationGuard.addListener(_changed);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant Wd05AdministrationReadScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source) ||
        oldWidget.section != widget.section) {
      oldWidget.source.sessionNavigationGuard.removeListener(_changed);
      widget.source.sessionNavigationGuard.addListener(_changed);
      _generation++;
      unawaited(_load());
    }
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
      _error = 'Session changed. Reopen this worklist after authentication.';
      _loading = false;
    });
  }

  Future<void> _load({int page = 1}) async {
    final generation = ++_generation;
    final source = widget.source;
    final revision = source.sessionNavigationGuard.revision;
    setState(() {
      _loading = true;
      _page = null;
      _error = null;
    });
    try {
      final result = await source.load(section: widget.section, page: page);
      if (!_current(generation, source, revision)) return;
      setState(() {
        _page = result;
        _loading = false;
      });
    } on Object catch (failure) {
      if (!_current(generation, source, revision)) return;
      setState(() {
        _error = _message(failure);
        _loading = false;
      });
    }
  }

  bool _current(
    int generation,
    Wd05AdministrationReadRemoteDataSource source,
    int revision,
  ) =>
      mounted &&
      identical(widget.source, source) &&
      generation == _generation &&
      source.sessionNavigationGuard.canAccessProtectedRoutes &&
      source.sessionNavigationGuard.revision == revision;

  String _message(Object error) {
    if (error is Wd05AdministrationReadUnavailable) {
      return 'Administrative access unavailable. Select an authorized clinic.';
    }
    if (error is ApiException &&
        const <int>{401, 403, 404}.contains(error.statusCode)) {
      return 'Server denied administrative access or the record is unavailable.';
    }
    return 'Administrative records could not be verified from Flask.';
  }

  @override
  Widget build(BuildContext context) {
    final page = _page;
    final active =
        widget.source.sessionNavigationGuard.canAccessProtectedRoutes;
    return Scaffold(
      appBar: AppBar(title: Text(widget.section.title)),
      body: ListView(
        key: const ValueKey('wd05-admin-read-workspace'),
        padding: const EdgeInsets.all(20),
        children: [
          const Text(
            'Live server read-only worklist. No local counts, edits, '
            'payments, downloads or automatic retries.',
            key: ValueKey('wd05-admin-read-warning'),
          ),
          const SizedBox(height: 12),
          if (_loading) const Center(child: CircularProgressIndicator()),
          if (_error != null)
            Text(_error!, key: const ValueKey('wd05-admin-read-error')),
          if (page != null) ...[
            Text('Verified records: ${page.items.length} of ${page.total}'),
            if (page.items.isEmpty)
              const Text('No server records returned for this clinic.'),
            for (final item in page.items)
              ListTile(
                key: ValueKey('wd05-admin-read-item-${item.id}'),
                title: Text(item.title),
                subtitle: Text(item.description),
              ),
            Row(
              children: [
                if (page.paged && page.page > 1)
                  TextButton(
                    key: const ValueKey('wd05-admin-read-previous'),
                    onPressed: _loading || !active
                        ? null
                        : () => unawaited(_load(page: page.page - 1)),
                    child: const Text('Previous'),
                  ),
                if (page.hasNext)
                  TextButton(
                    key: const ValueKey('wd05-admin-read-next'),
                    onPressed: _loading || !active
                        ? null
                        : () => unawaited(_load(page: page.page + 1)),
                    child: const Text('Next'),
                  ),
              ],
            ),
          ],
          if (!_loading && active)
            OutlinedButton(
              key: const ValueKey('wd05-admin-read-refresh'),
              onPressed: () => unawaited(_load()),
              child: const Text('Refresh from Flask'),
            ),
        ],
      ),
    );
  }
}
