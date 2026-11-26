import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/administration/data/wd05_report_read_remote_data_source.dart';

/// Clinic-scoped generated-report metadata only. Never follows file_url,
/// generates reports, downloads to disk, or infers a currency or timezone.
class Wd05GeneratedReportsScreen extends StatefulWidget {
  const Wd05GeneratedReportsScreen({super.key, required this.source});

  final Wd05ReportReadRemoteDataSource source;

  @override
  State<Wd05GeneratedReportsScreen> createState() =>
      _Wd05GeneratedReportsScreenState();
}

class _Wd05GeneratedReportsScreenState
    extends State<Wd05GeneratedReportsScreen> {
  Wd05ReportPage? _snapshot;
  Wd05GeneratedReport? _selectedReport;
  bool _loading = false;
  String? _error;
  String _type = 'all';
  String _format = 'all';
  int _generation = 0;
  final _from = TextEditingController();
  final _to = TextEditingController();

  @override
  void initState() {
    super.initState();
    widget.source.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant Wd05GeneratedReportsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (identical(oldWidget.source, widget.source)) return;
    oldWidget.source.sessionNavigationGuard.removeListener(_sessionChanged);
    widget.source.sessionNavigationGuard.addListener(_sessionChanged);
    _generation++;
    _snapshot = null;
    _selectedReport = null;
    _error = null;
    _loading = false;
    unawaited(_load());
  }

  @override
  void dispose() {
    _generation++;
    widget.source.sessionNavigationGuard.removeListener(_sessionChanged);
    _from.dispose();
    _to.dispose();
    super.dispose();
  }

  void _sessionChanged() {
    if (!mounted) return;
    _generation++;
    setState(() {
      _snapshot = null;
    _selectedReport = null;
      _loading = false;
      _error = 'Report metadata cleared after session change.';
    });
  }

  bool _current(
    int generation,
    int revision,
    Wd05ReportReadRemoteDataSource source,
  ) =>
      mounted &&
      identical(source, widget.source) &&
      _generation == generation &&
      source.sessionNavigationGuard.canAccessProtectedRoutes &&
      source.sessionNavigationGuard.revision == revision;

  String _safeMessage(Object error) {
    if (error is Wd05ReportSessionUnavailable) {
      return 'Report access unavailable. Sign in to the selected clinic.';
    }
    if (error is ArgumentError) {
      return 'Check report date filters (YYYY-MM-DD) and try again.';
    }
    if (error is ApiException &&
        const <int>{401, 403, 404}.contains(error.statusCode)) {
      return 'Report access denied or unavailable.';
    }
    return 'Report metadata could not be verified from the server.';
  }

  Future<void> _detail(Wd05GeneratedReport report) async {
    if (_loading) {
      return;
    }
    final source = widget.source;
    final revision = source.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() { _selectedReport = null; _loading = true; _error = null; });
    try {
      final result = await source.detail(report);
      if (_current(generation, revision, source)) {
        setState(() => _selectedReport = result);
      }
    } on Object catch (error) {
      if (_current(generation, revision, source)) {
        setState(() => _error = _safeMessage(error));
      }
    } finally {
      if (_current(generation, revision, source)) {
        setState(() => _loading = false);
      }
    }
  }

  Future<void> _load({int page = 1}) async {
    if (_loading) return;
    final source = widget.source;
    final revision = source.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _loading = true;
      _error = null;
      _snapshot = null;
    _selectedReport = null;
    });
    try {
      final result = await source.listReports(
        page: page,
        reportType: _type == 'all' ? null : _type,
        reportFormat: _format == 'all' ? null : _format,
        dateFrom: _from.text.trim().isEmpty ? null : _from.text.trim(),
        dateTo: _to.text.trim().isEmpty ? null : _to.text.trim(),
      );
      if (!_current(generation, revision, source)) return;
      setState(() {
        _snapshot = result;
        _loading = false;
      });
    } on Object catch (error) {
      if (!_current(generation, revision, source)) return;
      setState(() {
        _error = _safeMessage(error);
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final snapshot = _snapshot;
    return ListView(
      key: const ValueKey('wd05-reports-workspace'),
      padding: const EdgeInsets.all(20),
      children: [
        Text(
          'Generated reports',
          style: Theme.of(context).textTheme.headlineSmall,
        ),
        const SizedBox(height: 8),
        const Text(
          'Read-only server metadata. Report generation, file downloads, exports, '
          'currency totals and cross-clinic reports are not enabled here.',
          key: ValueKey('wd05-report-scope-notice'),
        ),
        const SizedBox(height: 16),
        DropdownButton<String>(
          key: const ValueKey('wd05-report-type-filter'),
          isExpanded: true,
          value: _type,
          items: [
            const DropdownMenuItem(
              value: 'all',
              child: Text('All supported report types'),
            ),
            for (final type in wd05ReportTypes)
              DropdownMenuItem(value: type, child: Text(type)),
          ],
          onChanged: _loading
              ? null
              : (value) {
                  if (value != null) setState(() => _type = value);
                },
        ),
        DropdownButton<String>(
          key: const ValueKey('wd05-report-format-filter'),
          isExpanded: true,
          value: _format,
          items: [
            const DropdownMenuItem(value: 'all', child: Text('All formats')),
            for (final format in wd05ReportFormats)
              DropdownMenuItem(value: format, child: Text(format)),
          ],
          onChanged: _loading
              ? null
              : (value) {
                  if (value != null) setState(() => _format = value);
                },
        ),
        TextField(
          key: const ValueKey('wd05-report-date-from'),
          controller: _from,
          enabled: !_loading,
          decoration: const InputDecoration(
            labelText: 'From date (YYYY-MM-DD)',
          ),
        ),
        TextField(
          key: const ValueKey('wd05-report-date-to'),
          controller: _to,
          enabled: !_loading,
          decoration: const InputDecoration(labelText: 'To date (YYYY-MM-DD)'),
        ),
        const SizedBox(height: 12),
        OutlinedButton(
          key: const ValueKey('wd05-apply-report-filters'),
          onPressed: _loading ? null : () => unawaited(_load()),
          child: const Text('Apply report filters'),
        ),
        if (_loading) const Center(child: CircularProgressIndicator()),
        if (_error != null)
          Text(_error!, key: const ValueKey('wd05-reports-error')),
        if (_selectedReport != null) ...[
          Text('Verified report #${_selectedReport!.id}'),
          Text('${_selectedReport!.type} / ${_selectedReport!.format} · ${_selectedReport!.createdUtc.toIso8601String()}'),
          const Text('Metadata only; export/download remains unavailable.'),
        ],
        if (snapshot != null) ...[
          Text(
            'Reports on page ${snapshot.page}: ${snapshot.items.length} of ${snapshot.total}',
          ),
          if (snapshot.items.isEmpty)
            const Text('No generated reports in the selected server result.'),
          for (final report in snapshot.items)
            Card(
              child: ListTile(
                key: ValueKey('wd05-report-${report.id}'),
                title: Text('${report.type} / ${report.format}'),
                subtitle: Text(
                  'Generated UTC: ${report.createdUtc.toIso8601String()}',
                ),
                trailing: Text('ID ${report.id}'),
                onTap: _loading ? null : () => unawaited(_detail(report)),
              ),
            ),
          Row(
            children: [
              if (snapshot.page > 1)
                TextButton(
                  key: const ValueKey('wd05-reports-previous'),
                  onPressed: _loading
                      ? null
                      : () => unawaited(_load(page: snapshot.page - 1)),
                  child: const Text('Previous'),
                ),
              if (snapshot.hasNext)
                TextButton(
                  key: const ValueKey('wd05-reports-next'),
                  onPressed: _loading
                      ? null
                      : () => unawaited(_load(page: snapshot.page + 1)),
                  child: const Text('Next'),
                ),
            ],
          ),
        ],
      ],
    );
  }
}
