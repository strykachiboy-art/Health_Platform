import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/clinical_timeline/data/timeline_source.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/models/lab_order_read.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/clinical_order_summary.dart';

/// Use the existing authorized timeline, not a second chart aggregator.
/// A bounded search may be incomplete: never imply absence in that case.
class ClinicalHighlight {
  const ClinicalHighlight(this.event, {required this.exhausted});
  final TimelineEvent? event;
  final bool exhausted;
}

Future<ClinicalHighlight> findClinicalHighlight(
  TimelineSession session,
  String category,
  Set<String> acceptedTypes, {
  int maxPages = 5,
}) async {
  if (maxPages < 1 || maxPages > 5 || !timelineCategories.contains(category)) {
    throw const FormatException('Invalid clinical highlight request.');
  }
  final filters = TimelineFilters(category: category, limit: 25);
  TimelinePage? prior;
  for (var index = 0; index < maxPages; index++) {
    final page = await session.read(filters, previous: prior);
    if (!page.available.contains(category)) {
      throw const TimelineUnavailable();
    }
    for (final item in page.items) {
      if (acceptedTypes.contains(item.type)) {
        return ClinicalHighlight(item, exhausted: true);
      }
    }
    if (page.cursor == null) {
      return const ClinicalHighlight(null, exhausted: true);
    }
    prior = page;
  }
  return const ClinicalHighlight(null, exhausted: false);
}

class PatientClinicalHighlights extends StatefulWidget {
  const PatientClinicalHighlights({
    super.key,
    required this.source,
    required this.patientId,
    required this.clinicId,
    this.labSource,
    this.ordersSource,
  });
  final TimelineSource source;
  final int patientId, clinicId;
  final LabOrderReadRemoteDataSource? labSource;
  final ClinicalOrdersRemoteDataSource? ordersSource;

  @override
  State<PatientClinicalHighlights> createState() =>
      _PatientClinicalHighlightsState();
}

class _PatientClinicalHighlightsState extends State<PatientClinicalHighlights> {
  static const _labels = <String, String>{
    'medication_administration': 'Last recorded medication administration',
    'laboratory': 'Latest recorded laboratory event',
    'imaging': 'Latest recorded imaging event',
  };
  static const _events = <String, Set<String>>{
    'medication_administration': {'medication_administered'},
    'laboratory': {
      'lab_order_created',
      'lab_sample_collected',
      'lab_sample_processed',
      'lab_result_entry_recorded',
      'lab_results_verified',
      'lab_order_completed',
      'lab_patient_release_recorded',
      'lab_patient_release_withdrawn',
      'lab_corrected_release_recorded',
      'lab_corrected_release_withdrawn',
    },
    'imaging': {'imaging_order_created', 'imaging_order_completed'},
  };
  final _results = <String, ClinicalHighlight>{};
  final _unavailable = <String>{};
  LabReadPage<LabPatientOrder>? _labs;
  ClinicalOrderTimeline? _orders;
  bool _allowed = false, _busy = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_invalidate);
    unawaited(_prepare());
  }

  @override
  void didUpdateWidget(covariant PatientClinicalHighlights oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source) ||
        oldWidget.patientId != widget.patientId ||
        oldWidget.clinicId != widget.clinicId ||
        !identical(oldWidget.labSource, widget.labSource) ||
        !identical(oldWidget.ordersSource, widget.ordersSource)) {
      oldWidget.source.guard.removeListener(_invalidate);
      widget.source.guard.addListener(_invalidate);
      _invalidate();
      unawaited(_prepare());
    }
  }

  bool _current(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.source.guard.revision == revision &&
      widget.source.guard.canAccessProtectedRoutes;

  void _invalidate() {
    _generation++;
    if (mounted) {
      setState(() {
        _allowed = false;
        _busy = false;
        _results.clear();
        _unavailable.clear();
        _labs = null;
        _orders = null;
      });
    }
  }

  Future<void> _prepare() async {
    final g = _generation;
    final rev = widget.source.guard.revision;
    try {
      final session = await widget.source.open(
        widget.patientId,
        widget.clinicId,
      );
      if (_current(g, rev) && session.current) {
        setState(() => _allowed = true);
      }
    } on Object {
      // Role, scope or feature unavailable. Do not fetch clinical data.
    }
  }

  Future<void> _load() async {
    if (!_allowed || _busy) {
      return;
    }
    final g = ++_generation;
    final rev = widget.source.guard.revision;
    setState(() {
      _busy = true;
      _results.clear();
      _unavailable.clear();
      _labs = null;
      _orders = null;
    });
    try {
      final session = await widget.source.open(
        widget.patientId,
        widget.clinicId,
      );
      final results = <String, ClinicalHighlight>{};
      final unavailable = <String>{};
      for (final category in _labels.keys) {
        if (!_current(g, rev)) {
          return;
        }
        try {
          results[category] = await findClinicalHighlight(
            session,
            category,
            _events[category]!,
            maxPages: category == 'medication_administration' ? 5 : 1,
          );
        } on Object {
          unavailable.add(category);
        }
      }
      LabReadPage<LabPatientOrder>? labs;
      ClinicalOrderTimeline? orders;
      final labSource = widget.labSource;
      final ordersSource = widget.ordersSource;
      if (labSource != null &&
          identical(labSource.sessionNavigationGuard, widget.source.guard) &&
          identical(labSource.apiClient, widget.source.apiClient) &&
          labSource.loadSession == widget.source.loadSession) {
        await session.check();
        if (!_current(g, rev)) {
          return;
        }
        try {
          labs = await labSource.fetchPatientOrders(
            patientId: widget.patientId,
            perPage: 5,
          );
        } on Object {
          unavailable.add('lab_current');
        }
      }
      if (ordersSource != null &&
          identical(ordersSource.sessionNavigationGuard, widget.source.guard) &&
          identical(ordersSource.apiClient, widget.source.apiClient) &&
          ordersSource.loadSession == widget.source.loadSession) {
        await session.check();
        if (!_current(g, rev)) {
          return;
        }
        try {
          orders = await ordersSource.fetchPatientOrders(
            patientId: widget.patientId,
            type: 'imaging',
          );
        } on Object {
          unavailable.add('imaging_current');
        }
      }
      await session.check();
      if (_current(g, rev)) {
        setState(() {
          _results.addAll(results);
          _unavailable.addAll(unavailable);
          _labs = labs;
          _orders = orders;
        });
      }
    } on Object {
      if (_current(g, rev)) {
        setState(() => _unavailable.addAll(_labels.keys));
      }
    } finally {
      if (_current(g, rev)) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.guard.removeListener(_invalidate);
    super.dispose();
  }

  String _describe(String key) {
    if (_unavailable.contains(key)) {
      return 'Unavailable; check authorized source.';
    }
    final result = _results[key];
    if (result == null) {
      return 'Not loaded.';
    }
    final event = result.event;
    if (event == null) {
      return result.exhausted
          ? 'No matching recorded event.'
          : 'Not established within the bounded history.';
    }
    return '${event.type.replaceAll('_', ' ')} · ${event.occurredAt.toIso8601String()} (UTC) · ${event.sourceKind.replaceAll('_', ' ')} #${event.sourceId}';
  }

  @override
  Widget build(BuildContext context) {
    if (!_allowed || !widget.source.guard.canAccessProtectedRoutes) {
      return const SizedBox.shrink();
    }
    return Card(
      key: const ValueKey('patient-clinical-highlights'),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Recorded clinical highlights',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const Text(
              'Read-only historical events, not current order status or a signed radiology report. Scheduled, held and refused medications do not count as administration.',
            ),
            OutlinedButton(
              key: const ValueKey('patient-highlights-refresh'),
              onPressed: _busy ? null : () => unawaited(_load()),
              child: const Text('Load clinical highlights'),
            ),
            if (_busy) const LinearProgressIndicator(),
            for (final entry in _labels.entries)
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text(entry.value),
                subtitle: Text(_describe(entry.key)),
              ),
            if (widget.labSource != null) ...[
              const Divider(),
              const Text('Current laboratory orders (first server page)'),
              if (_labs == null)
                Text(
                  _unavailable.contains('lab_current')
                      ? 'Unavailable.'
                      : 'Not loaded or source scope unavailable.',
                )
              else ...[
                Text(
                  '${_labs!.items.length} of ${_labs!.total} server records.',
                ),
                for (final order in _labs!.items)
                  ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    title: Text(
                      'Laboratory order #${order.id} · ${order.status.name}',
                    ),
                    subtitle: Text(
                      '${order.createdAt} · ${order.items.length} test item(s)${order.status == LabOrderStatus.completed ? '\n${order.items.where((item) => item.resultValue != null).length} recorded result(s); review original laboratory record.' : ''}',
                    ),
                  ),
                const Text(
                  'Completed order status does not confirm patient release.',
                ),
              ],
            ],
            if (widget.ordersSource != null) ...[
              const Divider(),
              const Text('Current imaging order statuses'),
              if (_orders == null)
                Text(
                  _unavailable.contains('imaging_current')
                      ? 'Unavailable.'
                      : 'Not loaded or source scope unavailable.',
                )
              else ...[
                Text('${_orders!.total} imaging order(s) returned.'),
                for (final order in _orders!.items.take(5))
                  ListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    title: Text(order.displayName),
                    subtitle: Text(
                      'Order #${order.sourceEntityId} · ${order.sourceStatus} · ${order.orderedAt}',
                    ),
                  ),
                if (_orders!.total > 5)
                  const Text(
                    'Showing first five server records. Review imaging workspace for the rest.',
                  ),
                const Text(
                  'Order status is not a finalized diagnostic report.',
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }
}
