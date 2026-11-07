import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

/// Read-only current admission and admission-history surface.
///
/// The backend owns admission status, bed assignment, patient/clinic
/// authorization, and the exact no-active-admission response.
class PatientAdmissionHistoryCard extends StatefulWidget {
  const PatientAdmissionHistoryCard({
    super.key,
    required this.patientId,
    required this.dataSource,
    this.pageSize = 20,
  }) : assert(patientId > 0),
       assert(pageSize > 0 && pageSize <= 500);

  final int patientId;
  final PatientAdmissionRemoteDataSource dataSource;
  final int pageSize;

  @override
  State<PatientAdmissionHistoryCard> createState() =>
      _PatientAdmissionHistoryCardState();
}

class _PatientAdmissionHistoryCardState
    extends State<PatientAdmissionHistoryCard> {
  PatientAdmission? _current;
  List<PatientAdmission> _history = const [];
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
  void didUpdateWidget(covariant PatientAdmissionHistoryCard oldWidget) {
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
    _clearRecords();
    _loading = false;
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

  void _clearRecords() {
    _current = null;
    _history = const [];
    _total = null;
    _page = 0;
  }

  void _onSessionChanged() {
    if (!mounted) return;

    _generation++;

    final authorized =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;

    setState(() {
      _clearRecords();
      _loading = false;
      _failure = authorized
          ? null
          : 'Session unavailable. Admission records have been cleared.';
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
        _generation++;
        _clearRecords();
        _loading = false;
        _failure = 'Session unavailable. Admission records have been cleared.';
      });
      return;
    }

    final patientId = widget.patientId;
    final pageSize = widget.pageSize;
    final revision = guard.revision;
    final generation = ++_generation;
    final requestedPage = nextPage ? _page + 1 : 1;

    setState(() {
      if (!nextPage) _clearRecords();

      _loading = true;
      _failure = null;
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
      // A specific backend 404 means "no active admission".
      // Every other failure remains an error through the S01 adapter.
      final current = nextPage
          ? _current
          : await source.fetchCurrentAdmission(patientId: patientId);

      if (!stillCurrent()) return;

      final page = await source.fetchAdmissionHistory(
        patientId: patientId,
        page: requestedPage,
        perPage: pageSize,
      );

      if (!stillCurrent()) return;

      final combined = nextPage
          ? <PatientAdmission>[..._history, ...page.items]
          : <PatientAdmission>[...page.items];

      if (combined.length > page.total ||
          combined.map((item) => item.id).toSet().length != combined.length) {
        throw const FormatException('Invalid combined admission history.');
      }

      setState(() {
        _current = current;
        _history = List<PatientAdmission>.unmodifiable(combined);
        _total = page.total;
        _page = page.page;
        _loading = false;
      });
    } catch (error) {
      if (!stillCurrent()) return;

      setState(() {
        _clearRecords();
        _loading = false;
        _failure = _failureMessage(error);
      });
    }
  }

  String _failureMessage(Object error) {
    if (error is PatientAdmissionSessionMismatchException) {
      return 'Session unavailable. Admission records have been cleared.';
    }

    if (error is ApiException) {
      if (const <int>{401, 403, 404, 422}.contains(error.statusCode)) {
        return 'Admission access denied or patient records unavailable.';
      }

      return 'Admission records could not be loaded from the backend.';
    }

    return 'Admission records could not be verified.';
  }

  String _status(PatientAdmissionStatus status) => switch (status) {
    PatientAdmissionStatus.admitted => 'Admitted',
    PatientAdmissionStatus.discharged => 'Discharged',
    PatientAdmissionStatus.transferred => 'Transferred',
  };

  String _timestamp(DateTime value) => value.toUtc().toIso8601String();

  Widget _admission(PatientAdmission admission, {required bool isCurrent}) {
    final lines = <String>[
      'Admission ID: ${admission.id}',
      'Status: ${_status(admission.status)}',
      'Bed ID: ${admission.bedId}',
      if (admission.reason != null && admission.reason!.trim().isNotEmpty)
        'Reason: ${admission.reason}',
      if (admission.admittedAt != null)
        'Admitted (UTC): ${_timestamp(admission.admittedAt!)}',
      if (admission.dischargedAt != null)
        'Discharged (UTC): ${_timestamp(admission.dischargedAt!)}',
    ];

    return Padding(
      key: ValueKey(
        isCurrent
            ? 'patient-current-admission-record'
            : 'patient-admission-history-${admission.id}',
      ),
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final line in lines)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 2),
              child: Text(line),
            ),
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
      key: const ValueKey('patient-admissions-card'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Patient admissions',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                if (!_loading &&
                    _failure == null &&
                    _total != null &&
                    guard.canAccessProtectedRoutes)
                  IconButton(
                    key: const ValueKey('patient-admissions-refresh'),
                    tooltip: 'Refresh patient admissions',
                    onPressed: () => unawaited(_load()),
                    icon: const Icon(Icons.refresh),
                  ),
              ],
            ),
            const SizedBox(height: 6),
            const Text(
              'Server-recorded admission and bed information. '
              'This screen does not assign beds or change admission status.',
            ),
            const SizedBox(height: 12),
            if (_failure != null) ...[
              Text(_failure!, key: const ValueKey('patient-admissions-error')),
              if (guard.canAccessProtectedRoutes) ...[
                const SizedBox(height: 8),
                OutlinedButton(
                  key: const ValueKey('patient-admissions-retry'),
                  onPressed: () => unawaited(_load()),
                  child: const Text('Retry admissions'),
                ),
              ],
            ] else if (_loading && _total == null) ...[
              const Center(
                child: CircularProgressIndicator(
                  key: ValueKey('patient-admissions-loading'),
                ),
              ),
            ] else if (_total == null) ...[
              const Text('Preparing admission records...'),
            ] else ...[
              Text(
                'Current admission',
                style: Theme.of(context).textTheme.titleSmall,
              ),
              if (_current != null)
                _admission(_current!, isCurrent: true)
              else
                const Text(
                  'No active admission reported by the backend.',
                  key: ValueKey('patient-admissions-current-empty'),
                ),
              const Divider(),
              Text(
                'Admission history',
                style: Theme.of(context).textTheme.titleSmall,
              ),
              if (_history.isEmpty)
                const Text(
                  'No admission history records returned. '
                  'This does not prove the patient was never admitted.',
                  key: ValueKey('patient-admissions-history-empty'),
                ),
              for (final admission in _history)
                _admission(admission, isCurrent: false),
              if (_loading)
                const Center(child: CircularProgressIndicator())
              else if (canLoadMore)
                OutlinedButton(
                  key: const ValueKey('patient-admissions-load-more'),
                  onPressed: () => unawaited(_load(nextPage: true)),
                  child: const Text('Load more admissions'),
                ),
            ],
          ],
        ),
      ),
    );
  }
}
