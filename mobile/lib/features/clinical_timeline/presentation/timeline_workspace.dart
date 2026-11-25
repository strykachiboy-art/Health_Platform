import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/clinical_timeline/data/timeline_source.dart';
import 'package:clinic_system_pro/features/problems/data/problem_source.dart';
import 'package:clinic_system_pro/features/problems/presentation/problem_workspace.dart';

String _label(String value) {
  final words = value.replaceAll('_', ' ');
  return '${words[0].toUpperCase()}${words.substring(1)}';
}

String _eventLabel(String value) => switch (value) {
  'appointment_current_slot' => 'Appointment scheduled slot',
  'medication_current_slot' => 'Medication scheduled slot',
  'imaging_current_slot' => 'Imaging scheduled slot',
  'diagnosis_documented_at_finalization' =>
    'Diagnosis documented at consultation finalization',
  'operative_document_signature_recorded' =>
    'Operative document signature (not procedure completion)',
  _ => _label(value),
};
String _failure(Object e) {
  if (e is TimelineUnavailable) {
    return 'Authorized session unavailable. Reopen the patient chart.';
  }
  if (e is ApiException) {
    if (e.statusCode == 404) {
      return 'Clinical timeline or patient unavailable. The staged feature may be disabled.';
    }
    if (e.statusCode == 401 || e.statusCode == 403) {
      return 'Clinical timeline access denied.';
    }
    if (e.statusCode == 400) {
      return 'Filters or continuation expired or rejected. Refresh from the first page.';
    }
  }
  return 'Unable to verify clinical timeline. Refresh from the server.';
}

/// Created only inside the verified patient chart, never for emergency access.
/// Opening the feature requires a fresh bound session; no background PHI request.
class TimelineEntry extends StatefulWidget {
  const TimelineEntry({
    super.key,
    required this.source,
    required this.patientId,
    required this.clinicId,
    this.problemSource,
  });
  final TimelineSource source;
  final ProblemSource? problemSource;
  final int patientId, clinicId;
  @override
  State<TimelineEntry> createState() => _TimelineEntryState();
}

class _TimelineEntryState extends State<TimelineEntry> {
  TimelineSession? _session;
  int _generation = 0;
  bool _opening = false;
  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_clear);
    unawaited(_prepare());
  }

  @override
  void didUpdateWidget(covariant TimelineEntry oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source) ||
        !identical(oldWidget.problemSource, widget.problemSource) ||
        oldWidget.patientId != widget.patientId ||
        oldWidget.clinicId != widget.clinicId) {
      oldWidget.source.guard.removeListener(_clear);
      widget.source.guard.addListener(_clear);
      _clear();
      unawaited(_prepare());
    }
  }

  void _clear() {
    _generation++;
    if (mounted) {
      setState(() {
        _session = null;
        _opening = false;
      });
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.guard.removeListener(_clear);
    super.dispose();
  }

  Future<void> _prepare() async {
    final g = ++_generation;
    try {
      final s = await widget.source.open(widget.patientId, widget.clinicId);
      if (mounted && g == _generation && s.current) {
        setState(() => _session = s);
      }
    } on Object {
      if (mounted && g == _generation) {
        setState(() => _session = null);
      }
    }
  }

  Future<void> _open() async {
    final s = _session;
    if (s == null || !s.current || _opening) {
      return;
    }
    final g = _generation;
    setState(() => _opening = true);
    try {
      await s.check();
      if (!mounted || g != _generation || !s.current) {
        return;
      }
      final router = GoRouter.maybeOf(context);
      final problems = widget.problemSource;
      await Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => TimelineWorkspace(
            session: s,
            problemSource: problems,
            onOpenSource: router == null
                ? null
                : (path) => router.push<void>(path),
          ),
        ),
      );
    } on Object {
      if (mounted && g == _generation) {
        _clear();
      }
    } finally {
      if (mounted && g == _generation) {
        setState(() => _opening = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_session?.current != true) {
      return const SizedBox.shrink();
    }
    return Card(
      child: ListTile(
        key: const ValueKey('clinical-timeline-open'),
        leading: const Icon(Icons.timeline),
        title: const Text('Clinical timeline'),
        subtitle: const Text(
          'Read-only patient journey across authorized clinical records',
        ),
        trailing: const Icon(Icons.chevron_right),
        onTap: _opening ? null : () => unawaited(_open()),
      ),
    );
  }
}

class TimelineWorkspace extends StatefulWidget {
  const TimelineWorkspace({
    super.key,
    required this.session,
    this.onOpenSource,
    this.problemSource,
  });
  final TimelineSession session;
  final ProblemSource? problemSource;
  final Future<void> Function(String path)? onOpenSource;
  @override
  State<TimelineWorkspace> createState() => _TimelineWorkspaceState();
}

class _TimelineWorkspaceState extends State<TimelineWorkspace> {
  TimelinePage? _page;
  Set<String> _available = {};
  String? _category, _message;
  DateTimeRange? _dates;
  int _generation = 0, _pageNumber = 1;
  bool _busy = false, _navigating = false;
  bool get _active => widget.session.current;
  @override
  void initState() {
    super.initState();
    widget.session.source.guard.addListener(_invalidate);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant TimelineWorkspace oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.session, widget.session)) {
      oldWidget.session.source.guard.removeListener(_invalidate);
      widget.session.source.guard.addListener(_invalidate);
      _invalidate();
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.session.source.guard.removeListener(_invalidate);
    super.dispose();
  }

  void _invalidate() {
    _generation++;
    if (mounted) {
      setState(() {
        _page = null;
        _available = {};
        _category = null;
        _dates = null;
        _message = null;
        _busy = false;
        _navigating = false;
      });
    }
  }

  bool _current(int generation) =>
      mounted && _active && generation == _generation;
  TimelineFilters _filters() {
    final range = _dates;
    return TimelineFilters(
      category: _category,
      start: range?.start,
      end: range == null
          ? null
          : DateTime(range.end.year, range.end.month, range.end.day + 1),
    );
  }

  Future<void> _load({bool next = false}) async {
    if (!_active || _busy || _navigating) {
      return;
    }
    final prior = next ? _page : null;
    if (next && prior?.cursor == null) {
      return;
    }
    final g = ++_generation;
    final filters = _filters();
    setState(() {
      _busy = true;
      _page = null;
      _message = null;
      if (!next) {
        _pageNumber = 1;
      }
    });
    try {
      final result = await widget.session.read(filters, previous: prior);
      if (!_current(g)) {
        return;
      }
      if (_category != null && !result.available.contains(_category)) {
        setState(() => _category = null);
        throw const TimelineUnavailable();
      }
      setState(() {
        _page = result;
        _available = result.available;
        _busy = false;
        if (next) {
          _pageNumber++;
        }
      });
    } on Object catch (e) {
      if (_current(g)) {
        setState(() {
          _busy = false;
          _page = null;
          _message = _failure(e);
        });
      }
    }
  }

  Future<void> _chooseDates() async {
    final g = _generation;
    final now = DateTime.now();
    final range = await showDateRangePicker(
      context: context,
      firstDate: DateTime(1900),
      lastDate: DateTime(now.year + 10, 12, 31),
      initialDateRange: _dates,
      helpText: 'Filter by local calendar dates',
    );
    if (!_current(g) || range == null) {
      return;
    }
    setState(() => _dates = range);
    await _load();
  }

  bool _canOpenProblem(TimelineEvent event) =>
      event.sourceKind == 'problem' &&
      widget.problemSource != null &&
      identical(widget.problemSource!.guard, widget.session.source.guard) &&
      identical(
        widget.problemSource!.apiClient,
        widget.session.source.apiClient,
      ) &&
      identical(
        widget.problemSource!.loadSession,
        widget.session.source.loadSession,
      );
  Future<void> _openProblem(TimelineEvent event) async {
    final source = widget.problemSource;
    if (source == null ||
        !_canOpenProblem(event) ||
        !_active ||
        _busy ||
        _navigating) {
      return;
    }
    final g = _generation;
    setState(() => _navigating = true);
    try {
      await widget.session.check();
      final session = await source.open();
      await widget.session.check();
      if (!mounted || !_current(g) || !session.current) {
        return;
      }
      final patientId = widget.session.patientId;
      await Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => ProblemWorkspaceScreen(
            source: source,
            session: session,
            patientId: patientId,
            initialProblemId: event.sourceId,
            allowWrite: false,
          ),
        ),
      );
      await widget.session.check();
    } on Object catch (e) {
      if (_current(g)) {
        setState(() {
          _page = null;
          _message = _failure(e);
        });
      }
    } finally {
      if (_current(g)) {
        setState(() => _navigating = false);
      }
    }
  }

  Future<void> _openSource(TimelineEvent event) async {
    final callback = widget.onOpenSource;
    final path = event.route(widget.session.patientId);
    if (callback == null || path == null || !_active || _busy || _navigating) {
      return;
    }
    final g = _generation;
    setState(() => _navigating = true);
    try {
      await widget.session.check();
      if (!_current(g)) {
        return;
      }
      await callback(path);
      await widget.session.check();
    } on Object catch (e) {
      if (_current(g)) {
        setState(() {
          _page = null;
          _message = _failure(e);
        });
      }
    } finally {
      if (_current(g)) {
        setState(() => _navigating = false);
      }
    }
  }

  String _local(BuildContext context, DateTime utc) {
    final d = utc.toLocal();
    final loc = MaterialLocalizations.of(context);
    return '${loc.formatFullDate(d)} · ${loc.formatTimeOfDay(TimeOfDay.fromDateTime(d), alwaysUse24HourFormat: MediaQuery.alwaysUse24HourFormatOf(context))} (local)';
  }

  Widget _event(BuildContext context, TimelineEvent event) {
    final staff = event.staff;
    final version = event.version;
    return Card(
      key: ValueKey('timeline-event-${event.eventId}'),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 4,
              children: [
                Chip(label: Text(_label(event.category))),
                Chip(label: Text(_label(event.phase))),
              ],
            ),
            Text(
              _eventLabel(event.type),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            Text(_local(context, event.occurredAt)),
            Text(
              'Recorded timestamp (UTC): ${event.occurredAt.toIso8601String()}',
            ),
            Text('Source: ${_label(event.sourceKind)} #${event.sourceId}'),
            if (version != null)
              Text(
                'Recorded version $version · original and amended records are distinct.',
              ),
            if (staff != null)
              Text(
                'Related staff: ${staff.name} (#${staff.id})${staff.department == null ? '' : ' · Current department: ${staff.department}'}',
              ),
            if (event.phase == 'scheduled')
              const Text('Scheduled slot — not proof of completed care.'),
            if (event.phase == 'amended')
              const Text(
                'Historical amendment event — current source may differ.',
              ),
            if (_canOpenProblem(event))
              TextButton(
                key: ValueKey('timeline-source-${event.eventId}'),
                onPressed: _busy || _navigating
                    ? null
                    : () => unawaited(_openProblem(event)),
                child: const Text('Open authorized problem record'),
              )
            else if (widget.onOpenSource != null &&
                event.route(widget.session.patientId) != null)
              TextButton(
                key: ValueKey('timeline-source-${event.eventId}'),
                onPressed: _busy || _navigating
                    ? null
                    : () => unawaited(_openSource(event)),
                child: Text(
                  event.exactRoute
                      ? 'Open authorized source record'
                      : 'Open related patient records',
                ),
              )
            else
              const Text(
                'Record reference only; use the authorized clinical workspace.',
              ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final page = _page;
    final dates = _dates;
    final blocked = _busy || _navigating;
    final categories = _available.toList()..sort();
    return Scaffold(
      appBar: AppBar(title: const Text('Clinical timeline')),
      body: !_active
          ? const Center(
              child: Text(
                'Session changed. Clinical timeline information cleared.',
              ),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text('Patient #${widget.session.patientId}'),
                const Text(
                  'Read-only clinical journey · newest first. Original records remain authoritative.',
                ),
                const Text(
                  'Scheduled events and documented actions are not inferred clinical outcomes.',
                ),
                if (_busy) const LinearProgressIndicator(),
                if (_message != null)
                  Text(_message!, key: const ValueKey('timeline-message')),
                DropdownButton<String>(
                  key: const ValueKey('timeline-category'),
                  isExpanded: true,
                  value: _category ?? 'all',
                  items: [
                    const DropdownMenuItem(
                      value: 'all',
                      child: Text('All authorized categories'),
                    ),
                    for (final category in categories)
                      DropdownMenuItem(
                        value: category,
                        child: Text(_label(category)),
                      ),
                  ],
                  onChanged: blocked || categories.isEmpty
                      ? null
                      : (value) {
                          if (value != null) {
                            setState(
                              () => _category = value == 'all' ? null : value,
                            );
                            unawaited(_load());
                          }
                        },
                ),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    OutlinedButton(
                      key: const ValueKey('timeline-dates'),
                      onPressed: blocked
                          ? null
                          : () => unawaited(_chooseDates()),
                      child: Text(
                        dates == null
                            ? 'Filter dates'
                            : '${MaterialLocalizations.of(context).formatShortDate(dates.start)} – ${MaterialLocalizations.of(context).formatShortDate(dates.end)}',
                      ),
                    ),
                    if (dates != null)
                      TextButton(
                        onPressed: blocked
                            ? null
                            : () {
                                setState(() => _dates = null);
                                unawaited(_load());
                              },
                        child: const Text('Clear dates'),
                      ),
                    OutlinedButton(
                      key: const ValueKey('timeline-refresh'),
                      onPressed: blocked ? null : () => unawaited(_load()),
                      child: const Text('Refresh from first page'),
                    ),
                  ],
                ),
                if (page != null) ...[
                  Text('Page $_pageNumber · ${page.items.length} events'),
                  if (page.items.isEmpty)
                    const Text(
                      'No authorized events returned for this filter.',
                    ),
                  if (page.limitations.contains(
                    'patient_check_in_timestamp_unavailable',
                  ))
                    const Text(
                      'Patient check-in timestamps are not available.',
                    ),
                  if (page.limitations.contains(
                    'clinical_radiology_final_report_unavailable',
                  ))
                    const Text(
                      'Finalized diagnostic radiology reports are not included in this timeline.',
                    ),
                  if (page.limitations.contains(
                    'surgery_documentation_is_not_operation_completion',
                  ))
                    const Text(
                      'Surgery documentation alone does not establish operation completion.',
                    ),
                  if (!page.labReleaseEnabled)
                    const Text(
                      'Patient laboratory release events are not available in this session.',
                    ),
                  for (final event in page.items) _event(context, event),
                  OutlinedButton(
                    key: const ValueKey('timeline-next'),
                    onPressed: blocked || page.cursor == null
                        ? null
                        : () => unawaited(_load(next: true)),
                    child: const Text('Next page'),
                  ),
                ],
              ],
            ),
    );
  }
}
