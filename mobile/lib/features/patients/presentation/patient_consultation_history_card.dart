import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_consultation.dart';
import 'package:clinic_system_pro/features/patients/data/patient_consultation_remote_data_source.dart';

/// Read-only consultation history for a previously verified patient.
///
/// Workflow status and signed clinical-record status are separate.
/// Backend records are authoritative; this widget cannot finalize,
/// amend, or otherwise mutate a clinical record.
class PatientConsultationHistoryCard extends StatefulWidget {
  const PatientConsultationHistoryCard({
    super.key,
    required this.patientId,
    required this.dataSource,
    this.pageSize = 20,
  }) : assert(patientId > 0),
       assert(pageSize > 0 && pageSize <= 500);

  final int patientId;
  final PatientConsultationRemoteDataSource dataSource;
  final int pageSize;

  @override
  State<PatientConsultationHistoryCard> createState() =>
      _PatientConsultationHistoryCardState();
}

class _PatientConsultationHistoryCardState
    extends State<PatientConsultationHistoryCard> {
  List<PatientConsultation> _items = const [];
  int? _total;
  int? _nextPage;
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
  void didUpdateWidget(covariant PatientConsultationHistoryCard oldWidget) {
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
    _items = const [];
    _total = null;
    _nextPage = null;
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
          : 'Session unavailable. Consultation records have been cleared.';
    });

    if (authorized) unawaited(_load());
  }

  Future<void> _load({bool nextPage = false}) async {
    if (_loading || (nextPage && _nextPage == null)) return;

    final source = widget.dataSource;
    final guard = source.sessionNavigationGuard;

    if (!guard.canAccessProtectedRoutes) {
      setState(() {
        _generation++;
        _clearRecords();
        _loading = false;
        _failure =
            'Session unavailable. Consultation records have been cleared.';
      });
      return;
    }

    final patientId = widget.patientId;
    final pageSize = widget.pageSize;
    final revision = guard.revision;
    final generation = ++_generation;
    final requestedPage = nextPage ? _nextPage! : 1;

    setState(() {
      if (!nextPage) _clearRecords();
      _loading = true;
      _failure = null;
    });

    bool stillCurrent() =>
        mounted &&
        generation == _generation &&
        identical(source, widget.dataSource) &&
        patientId == widget.patientId &&
        pageSize == widget.pageSize &&
        guard.canAccessProtectedRoutes &&
        guard.revision == revision;

    try {
      final result = await source.fetchConsultationHistory(
        patientId: patientId,
        page: requestedPage,
        perPage: pageSize,
      );

      if (!stillCurrent()) return;

      final combined = nextPage
          ? <PatientConsultation>[..._items, ...result.items]
          : <PatientConsultation>[...result.items];

      if (combined.length > result.total ||
          combined.map((item) => item.id).toSet().length != combined.length ||
          (nextPage && _total != result.total)) {
        throw const FormatException('Inconsistent consultation history pages.');
      }

      setState(() {
        _items = List<PatientConsultation>.unmodifiable(combined);
        _total = result.total;
        _nextPage = result.nextPage;
        _loading = false;
      });
    } catch (error) {
      if (!stillCurrent()) return;

      setState(() {
        _clearRecords();
        _loading = false;
        _failure = _messageFor(error);
      });
    }
  }

  String _messageFor(Object error) {
    if (error is PatientConsultationSessionMismatchException) {
      return 'Session unavailable. Consultation records have been cleared.';
    }

    if (error is ApiException) {
      if (const <int>{401, 403, 404, 422}.contains(error.statusCode)) {
        return 'Consultation access denied or patient records unavailable.';
      }
      return 'Consultation history could not be loaded from the backend.';
    }

    return 'Consultation records could not be verified.';
  }

  String _type(PatientConsultationType value) => switch (value) {
    PatientConsultationType.general => 'General',
    PatientConsultationType.followUp => 'Follow-up',
    PatientConsultationType.specialist => 'Specialist',
    PatientConsultationType.emergency => 'Emergency',
  };

  String _workflow(PatientConsultationStatus value) => switch (value) {
    PatientConsultationStatus.inProgress => 'In progress',
    PatientConsultationStatus.completed => 'Completed',
    PatientConsultationStatus.cancelled => 'Cancelled',
  };

  String _clinicalRecord(PatientClinicalRecordStatus value) => switch (value) {
    PatientClinicalRecordStatus.draft => 'Draft (not finalized)',
    PatientClinicalRecordStatus.finalized => 'Finalized',
    PatientClinicalRecordStatus.amended => 'Amended',
  };

  Widget _record(PatientConsultation item) {
    final lines = <String>[
      'Consultation ID: ${item.id}',
      'Type: ${_type(item.consultationType)}',
      'Workflow: ${_workflow(item.status)}',
      'Clinical record: ${_clinicalRecord(item.recordStatus)}',
      'Record version: ${item.recordVersion}',
      if (item.startedAt != null) 'Started (backend time): ${item.startedAt}',
      if (item.endedAt != null) 'Ended (backend time): ${item.endedAt}',
      if (item.chiefComplaint != null && item.chiefComplaint!.trim().isNotEmpty)
        'Chief complaint: ${item.chiefComplaint}',
      if (item.diagnosis != null && item.diagnosis!.trim().isNotEmpty)
        'Diagnosis: ${item.diagnosis}',
      if (item.icd10Code != null && item.icd10Code!.trim().isNotEmpty)
        'ICD-10: ${item.icd10Code}',
      if (item.treatmentPlan != null && item.treatmentPlan!.trim().isNotEmpty)
        'Treatment plan: ${item.treatmentPlan}',
      if (item.finalizedAt != null)
        'Finalized (backend time): ${item.finalizedAt}',
      if (item.latestAmendmentId != null)
        'Latest amendment ID: ${item.latestAmendmentId}',
    ];

    return Padding(
      key: ValueKey('patient-consultation-history-${item.id}'),
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final line in lines)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 2),
              child: Text(line),
            ),
          const Divider(),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final guard = widget.dataSource.sessionNavigationGuard;

    return Card(
      key: const ValueKey('patient-consultations-card'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Consultation history',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                if (!_loading &&
                    _failure == null &&
                    _total != null &&
                    guard.canAccessProtectedRoutes)
                  IconButton(
                    key: const ValueKey('patient-consultations-refresh'),
                    tooltip: 'Refresh consultation history',
                    icon: const Icon(Icons.refresh),
                    onPressed: () => unawaited(_load()),
                  ),
              ],
            ),
            const SizedBox(height: 6),
            const Text(
              'Server-owned consultation history. Clinical record status '
              'is separate from consultation workflow status.',
            ),
            const SizedBox(height: 12),
            if (_failure != null) ...[
              Text(
                _failure!,
                key: const ValueKey('patient-consultations-error'),
              ),
              if (guard.canAccessProtectedRoutes) ...[
                const SizedBox(height: 8),
                OutlinedButton(
                  key: const ValueKey('patient-consultations-retry'),
                  onPressed: () => unawaited(_load()),
                  child: const Text('Retry consultations'),
                ),
              ],
            ] else if (_loading && _total == null) ...[
              const Center(
                child: CircularProgressIndicator(
                  key: ValueKey('patient-consultations-loading'),
                ),
              ),
            ] else if (_total == null) ...[
              const Text('Preparing consultation history...'),
            ] else ...[
              Text(
                'Consultations: ${_items.length} of $_total',
                key: const ValueKey('patient-consultations-count'),
              ),
              if (_items.isEmpty)
                const Text(
                  'No consultation history records returned. '
                  'This does not confirm that the patient has never '
                  'had a consultation.',
                  key: ValueKey('patient-consultations-empty'),
                ),
              for (final item in _items) _record(item),
              if (_loading)
                const Center(child: CircularProgressIndicator())
              else if (_nextPage != null)
                OutlinedButton(
                  key: const ValueKey('patient-consultations-load-more'),
                  onPressed: () => unawaited(_load(nextPage: true)),
                  child: const Text('Load more consultations'),
                ),
            ],
          ],
        ),
      ),
    );
  }
}
