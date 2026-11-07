import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_vitals.dart';
import 'package:clinic_system_pro/features/patients/data/patient_vitals_remote_data_source.dart';

/// Read-only vitals display, using only backend-owned observations.
///
/// The widget does not compute clinical classifications, invent
/// normal ranges, persist PHI, or retry failed GETs automatically.
class PatientVitalsHistoryCard extends StatefulWidget {
  const PatientVitalsHistoryCard({
    super.key,
    required this.patientId,
    required this.dataSource,
    this.pageSize = 20,
  }) : assert(patientId > 0),
       assert(pageSize > 0 && pageSize <= 500);

  final int patientId;
  final PatientVitalsRemoteDataSource dataSource;
  final int pageSize;

  @override
  State<PatientVitalsHistoryCard> createState() =>
      _PatientVitalsHistoryCardState();
}

class _PatientVitalsHistoryCardState extends State<PatientVitalsHistoryCard> {
  PatientVitals? _latest;
  List<PatientVitals> _history = const [];
  int? _total;
  int _page = 0;
  int _generation = 0;
  bool _loading = false;
  String? _failure;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_load());
    });
  }

  @override
  void didUpdateWidget(covariant PatientVitalsHistoryCard oldWidget) {
    super.didUpdateWidget(oldWidget);

    if (oldWidget.patientId == widget.patientId &&
        identical(oldWidget.dataSource, widget.dataSource) &&
        oldWidget.pageSize == widget.pageSize) {
      return;
    }

    oldWidget.dataSource.sessionNavigationGuard.removeListener(
      _onSessionChanged,
    );
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);

    _generation++;
    _clearData();
    _failure = null;

    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_load());
    });
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onSessionChanged);
    super.dispose();
  }

  void _clearData() {
    _latest = null;
    _history = const [];
    _total = null;
    _page = 0;
    _loading = false;
  }

  void _onSessionChanged() {
    if (!mounted) return;

    _generation++;

    final authorized =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;

    setState(() {
      _clearData();
      _failure = authorized
          ? null
          : 'Session unavailable. Vitals have been cleared.';
    });

    if (authorized) unawaited(_load());
  }

  Future<void> _load({bool nextPage = false}) async {
    if (_loading) return;

    if (nextPage && (_total == null || _history.length >= _total!)) {
      return;
    }

    final source = widget.dataSource;
    final guard = source.sessionNavigationGuard;

    if (!guard.canAccessProtectedRoutes) {
      setState(() {
        _clearData();
        _failure = 'Session unavailable. Vitals have been cleared.';
      });
      return;
    }

    final patientId = widget.patientId;
    final revision = guard.revision;
    final generation = ++_generation;
    final requestedPage = nextPage ? _page + 1 : 1;
    final pageSize = widget.pageSize;

    setState(() {
      _loading = true;
      _failure = null;

      if (!nextPage) {
        _latest = null;
        _history = const [];
        _total = null;
        _page = 0;
      }
    });

    bool stillCurrent() =>
        mounted &&
        generation == _generation &&
        identical(source, widget.dataSource) &&
        widget.patientId == patientId &&
        widget.pageSize == pageSize &&
        guard.canAccessProtectedRoutes &&
        guard.revision == revision;

    try {
      // Refresh/latest is read independently of history.
      // Do not assume the first history item is the latest.
      final latest = nextPage
          ? _latest
          : await source.fetchLatestVitals(patientId: patientId);

      if (!stillCurrent()) return;

      final page = await source.fetchVitalsHistory(
        patientId: patientId,
        page: requestedPage,
        perPage: pageSize,
      );

      if (!stillCurrent()) return;

      final combined = nextPage
          ? <PatientVitals>[..._history, ...page.items]
          : <PatientVitals>[...page.items];

      if (combined.length > page.total ||
          combined.map((item) => item.id).toSet().length != combined.length) {
        throw const FormatException('Invalid combined vitals history.');
      }

      setState(() {
        _latest = latest;
        _history = List<PatientVitals>.unmodifiable(combined);
        _total = page.total;
        _page = page.page;
        _loading = false;
      });
    } catch (error) {
      if (!stillCurrent()) return;

      setState(() {
        _clearData();
        _failure = _failureMessage(error);
      });
    }
  }

  String _failureMessage(Object error) {
    if (error is PatientVitalsSessionMismatchException) {
      return 'Session unavailable. Vitals have been cleared.';
    }

    if (error is ApiException) {
      if (const <int>{401, 403, 404, 422}.contains(error.statusCode)) {
        return 'Vitals access denied or patient observations unavailable.';
      }
      return 'Vitals could not be loaded from the backend.';
    }

    return 'Vitals response could not be verified.';
  }

  List<Widget> _measurements(PatientVitals item) {
    final lines = <String>[
      if (item.temperature != null) 'Temperature: ${item.temperature} °C',
      if (item.bloodPressureSystolic != null &&
          item.bloodPressureDiastolic != null)
        'Blood pressure: ${item.bloodPressureSystolic}/${item.bloodPressureDiastolic} mmHg',
      if (item.bloodPressureSystolic != null &&
          item.bloodPressureDiastolic == null)
        'Systolic blood pressure: ${item.bloodPressureSystolic} mmHg',
      if (item.bloodPressureDiastolic != null &&
          item.bloodPressureSystolic == null)
        'Diastolic blood pressure: ${item.bloodPressureDiastolic} mmHg',
      if (item.heartRate != null) 'Heart rate: ${item.heartRate} bpm',
      if (item.respiratoryRate != null)
        'Respiratory rate: ${item.respiratoryRate} breaths/min',
      if (item.oxygenSaturation != null)
        'Oxygen saturation: ${item.oxygenSaturation}%',
      if (item.weight != null) 'Weight: ${item.weight} kg',
      if (item.height != null) 'Height: ${item.height} cm',
    ];

    if (lines.isEmpty) {
      return const [
        Text('No measurement values recorded in this observation.'),
      ];
    }

    return lines
        .map(
          (value) => Padding(
            padding: const EdgeInsets.symmetric(vertical: 2),
            child: Text(value),
          ),
        )
        .toList(growable: false);
  }

  Widget _observation(PatientVitals item, {required bool isLatest}) {
    return Padding(
      key: ValueKey(
        isLatest
            ? 'patient-vitals-latest-observation'
            : 'patient-vitals-history-${item.id}',
      ),
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Recorded (UTC): ${item.recordedAt.toUtc().toIso8601String()}',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 6),
          ..._measurements(item),
          if (item.consultationId != null)
            Text('Consultation ID: ${item.consultationId}'),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final guard = widget.dataSource.sessionNavigationGuard;
    final canLoadMore =
        !_loading && _total != null && _history.length < _total!;

    return Card(
      key: const ValueKey('patient-vitals-card'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Patient vitals',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                if (!_loading &&
                    _failure == null &&
                    _total != null &&
                    guard.canAccessProtectedRoutes)
                  IconButton(
                    key: const ValueKey('patient-vitals-refresh'),
                    tooltip: 'Refresh patient vitals',
                    onPressed: () => unawaited(_load()),
                    icon: const Icon(Icons.refresh),
                  ),
              ],
            ),
            const SizedBox(height: 6),
            const Text(
              'Server-recorded measurements. The app does not '
              'interpret values as normal or abnormal.',
            ),
            const SizedBox(height: 12),
            if (_failure != null) ...[
              Text(_failure!, key: const ValueKey('patient-vitals-error')),
              if (guard.canAccessProtectedRoutes) ...[
                const SizedBox(height: 8),
                OutlinedButton(
                  key: const ValueKey('patient-vitals-retry'),
                  onPressed: () => unawaited(_load()),
                  child: const Text('Retry vitals'),
                ),
              ],
            ] else if (_loading && _total == null) ...[
              const Center(
                child: CircularProgressIndicator(
                  key: ValueKey('patient-vitals-loading'),
                ),
              ),
            ] else if (_total == null) ...[
              const Text('Preparing vitals history...'),
            ] else ...[
              Text(
                'Latest observation',
                style: Theme.of(context).textTheme.titleSmall,
              ),
              if (_latest != null)
                _observation(_latest!, isLatest: true)
              else
                const Text(
                  'No latest vitals observation was returned.',
                  key: ValueKey('patient-vitals-latest-empty'),
                ),
              const Divider(),
              Text(
                'Vitals history',
                style: Theme.of(context).textTheme.titleSmall,
              ),
              if (_history.isEmpty)
                const Text(
                  'No vitals history records returned. '
                  'This is not proof that measurements were never recorded.',
                  key: ValueKey('patient-vitals-history-empty'),
                ),
              for (final item in _history) _observation(item, isLatest: false),
              if (_loading)
                const Center(child: CircularProgressIndicator())
              else if (canLoadMore)
                OutlinedButton(
                  key: const ValueKey('patient-vitals-load-more'),
                  onPressed: () => unawaited(_load(nextPage: true)),
                  child: const Text('Load more vitals'),
                ),
            ],
          ],
        ),
      ),
    );
  }
}
