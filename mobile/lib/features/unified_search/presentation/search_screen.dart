import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/unified_search/data/search_source.dart';

String searchLabel(String s) => s.replaceAll('_', ' ');

class UnifiedSearchScreen extends StatefulWidget {
  const UnifiedSearchScreen({super.key, required this.source});
  final SearchSource source;
  @override
  State<UnifiedSearchScreen> createState() => _UnifiedSearchScreenState();
}

class _UnifiedSearchScreenState extends State<UnifiedSearchScreen> {
  final _text = TextEditingController();
  Timer? _debounce;
  SearchSession? _session;
  SearchPage? _page;
  SearchFilters? _filters;
  String? _kind, _status, _message;
  String _normalized = '';
  bool _opening = true, _busy = false, _navigating = false;
  int _generation = 0;

  bool get _active => _session?.current == true;
  bool _current(int g, SearchSession s) =>
      mounted && g == _generation && identical(s, _session) && s.current;
  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_invalidate);
    unawaited(_open());
  }

  @override
  void didUpdateWidget(covariant UnifiedSearchScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source)) {
      oldWidget.source.guard.removeListener(_invalidate);
      widget.source.guard.addListener(_invalidate);
      _invalidate();
      unawaited(_open());
    }
  }

  @override
  void dispose() {
    _generation++;
    _debounce?.cancel();
    widget.source.guard.removeListener(_invalidate);
    _text.dispose();
    super.dispose();
  }

  Future<void> _open() async {
    final g = ++_generation;
    setState(() {
      _opening = true;
      _message = null;
    });
    try {
      final s = await widget.source.open();
      if (!mounted || g != _generation || !s.current) return;
      setState(() {
        _session = s;
        _opening = false;
      });
    } on Object {
      if (!mounted || g != _generation) return;
      setState(() {
        _opening = false;
        _message = 'Authorized clinic session unavailable.';
      });
    }
  }

  void _invalidate() {
    _generation++;
    _debounce?.cancel();
    if (!mounted) return;
    _text.clear();
    setState(() {
      _session = null;
      _page = null;
      _filters = null;
      _kind = null;
      _status = null;
      _normalized = '';
      _busy = false;
      _opening = false;
      _navigating = false;
      _message = 'Session changed. Reopen unified search.';
    });
  }

  void _schedule({bool force = false}) {
    final normalized = _text.text.trim();
    if (!_active || _navigating || (!force && normalized == _normalized)) {
      return;
    }
    _debounce?.cancel();
    final g = ++_generation;
    setState(() {
      _normalized = normalized;
      _page = null;
      _filters = null;
      _busy = false;
      _message = null;
    });
    if (normalized.runes.length < 2) return;
    try {
      final filters = SearchFilters(
        _text.text,
        resourceType: _kind,
        status: _status,
      );
      setState(() {
        _filters = filters;
        _busy = true;
      });
      _debounce = Timer(searchDebounce, () {
        if (mounted && g == _generation) {
          unawaited(_read(g, filters));
        }
      });
    } on FormatException {
      setState(() => _message = 'Enter a valid search term or #record ID.');
    }
  }

  Future<void> _read(
    int g,
    SearchFilters filters, {
    SearchPage? previous,
  }) async {
    final s = _session;
    if (s == null || !_current(g, s)) return;
    try {
      final page = await s.read(
        filters,
        previous: previous,
        stillWanted: () => _current(g, s),
      );
      if (!_current(g, s)) return;
      setState(() {
        _page = page;
        _busy = false;
        _message = null;
      });
    } on Object catch (e) {
      if (!_current(g, s)) return;
      if (e is SearchUnavailable ||
          (e is ApiException && (e.statusCode == 401 || e.statusCode == 403))) {
        _invalidate();
        return;
      }
      setState(() {
        _page = null;
        _busy = false;
        _message = e is ApiException && e.statusCode == 404
            ? 'Unified search is unavailable or disabled.'
            : e is ApiException && e.statusCode == 429
            ? 'Search rate limit reached. Retry later.'
            : e is ApiException && e.statusCode == 400
            ? 'Search or continuation rejected. Restart the search.'
            : 'Unable to verify search results. Retry online.';
      });
    }
  }

  void _next() {
    final p = _page;
    final f = _filters;
    if (!_active || _busy || _navigating || p?.cursor == null || f == null) {
      return;
    }
    final g = ++_generation;
    setState(() {
      _busy = true;
      _page = null;
      _message = null;
    });
    unawaited(_read(g, f, previous: p));
  }

  Future<void> _navigate(SearchHit hit) async {
    final s = _session;
    if (s == null ||
        !s.current ||
        _busy ||
        _navigating ||
        _page?.items.contains(hit) != true) {
      return;
    }
    final route = hit.routeFor(s.role);
    if (route == null) return;
    final g = ++_generation;
    setState(() => _navigating = true);
    try {
      await s.check();
      if (!_current(g, s)) return;
      // Do not retain the previous query or PHI under the destination route.
      _text.clear();
      setState(() {
        _page = null;
        _filters = null;
        _normalized = '';
        _message = null;
        _busy = false;
      });
      if (!_current(g, s)) return;
      if (!mounted) return;
      await context.push<void>(route);
      if (!_current(g, s)) return;
      await s.check();
      if (_current(g, s)) setState(() => _navigating = false);
    } on Object {
      if (_current(g, s)) _invalidate();
    }
  }

  @override
  Widget build(BuildContext context) {
    final active = _active;
    final available = _session?.available ?? const <String, Set<String>>{};
    final statuses =
        (_kind == null
                ? available.values.expand((s) => s).toSet()
                : available[_kind] ?? <String>{})
            .toList()
          ..sort();
    final page = _page;
    final enabled = active && !_navigating;
    return Scaffold(
      appBar: AppBar(title: const Text('Unified search')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text(
              'Search authorized patient records and clinical worklists. Use a name, patient number, phone, email or #record ID.',
            ),
            const SizedBox(height: 12),
            TextField(
              key: const ValueKey('unified-search-query'),
              controller: _text,
              enabled: enabled,
              maxLength: 120,
              autocorrect: false,
              enableSuggestions: false,
              decoration: InputDecoration(
                labelText: 'Search records',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: IconButton(
                  tooltip: 'Clear search',
                  onPressed: enabled
                      ? () {
                          _text.clear();
                          _schedule(force: true);
                        }
                      : null,
                  icon: const Icon(Icons.clear),
                ),
              ),
              onChanged: (_) => _schedule(),
            ),
            if (active) ...[
              DropdownButtonFormField<String>(
                key: const ValueKey('unified-search-resource'),
                initialValue: _kind ?? '',
                isExpanded: true,
                decoration: const InputDecoration(labelText: 'Resource type'),
                items: [
                  const DropdownMenuItem(
                    value: '',
                    child: Text('All authorized resources'),
                  ),
                  for (final k in available.keys)
                    DropdownMenuItem(value: k, child: Text(searchLabel(k))),
                ],
                onChanged: enabled
                    ? (v) {
                        final kind = v == '' ? null : v;
                        if (kind == _kind) return;
                        setState(() {
                          _kind = kind;
                          _status = null;
                        });
                        _schedule(force: true);
                      }
                    : null,
              ),
              const SizedBox(height: 8),
              DropdownButtonFormField<String>(
                key: ValueKey('unified-search-status-${_kind ?? "all"}'),
                initialValue: _status ?? '',
                isExpanded: true,
                decoration: const InputDecoration(labelText: 'Status'),
                items: [
                  const DropdownMenuItem(
                    value: '',
                    child: Text('All statuses'),
                  ),
                  for (final s in statuses)
                    DropdownMenuItem(value: s, child: Text(searchLabel(s))),
                ],
                onChanged: enabled
                    ? (v) {
                        final status = v == '' ? null : v;
                        if (status == _status) return;
                        setState(() => _status = status);
                        _schedule(force: true);
                      }
                    : null,
              ),
            ],
            const SizedBox(height: 12),
            if (_opening || _busy || _navigating)
              const LinearProgressIndicator(),
            if (_message != null) ...[
              Text(_message!, key: const ValueKey('unified-search-message')),
              if (active && _normalized.runes.length >= 2 && !_busy)
                TextButton(
                  onPressed: () => _schedule(force: true),
                  child: const Text('Restart search'),
                ),
            ],
            if (active &&
                !_busy &&
                _message == null &&
                page == null &&
                _normalized.runes.length < 2)
              const Text('Enter at least two characters.'),
            if (active && page != null) ...[
              if (page.items.isEmpty)
                const Text('No authorized matching records.'),
              for (final hit in page.items)
                Card(
                  child: ListTile(
                    key: ValueKey(
                      'unified-search-result-${hit.resourceType}-${hit.recordId}',
                    ),
                    title: Text(hit.patientName),
                    subtitle: Text(
                      '${hit.patientNumber}\n${hit.isPatient ? "Patient record" : "Operational record"} · ${searchLabel(hit.resourceType)} #${hit.recordId}\n${searchLabel(hit.status)}${hit.admissionId == null ? "" : " · Admission #${hit.admissionId}"}\n${hit.exactRoute
                          ? "Open record"
                          : hit.resourceType == "nursing_task"
                          ? "Open ward worklist"
                          : "Open workflow (not exact record)"}',
                    ),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: enabled ? () => unawaited(_navigate(hit)) : null,
                  ),
                ),
              if (page.cursor != null)
                OutlinedButton(
                  key: const ValueKey('unified-search-next'),
                  onPressed: enabled && !_busy ? _next : null,
                  child: const Text('Next page'),
                ),
            ],
          ],
        ),
      ),
    );
  }
}
