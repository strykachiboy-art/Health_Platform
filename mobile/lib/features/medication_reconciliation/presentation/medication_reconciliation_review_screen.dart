// P23G.S02 — admission-scoped medication reconciliation review (GET only).
// Displays backend decisions and states. It does not make clinical decisions,
// persist patient data, or assert that reported home medicines were verified.
import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/models/medication_reconciliation.dart';

enum MedicationReconciliationReviewFilter {
  all,
  admission,
  transfer,
  discharge,
}

extension on MedicationReconciliationReviewFilter {
  MedicationReconciliationEvent? get serverEvent => switch (this) {
    MedicationReconciliationReviewFilter.all => null,
    MedicationReconciliationReviewFilter.admission =>
      MedicationReconciliationEvent.admission,
    MedicationReconciliationReviewFilter.transfer =>
      MedicationReconciliationEvent.transfer,
    MedicationReconciliationReviewFilter.discharge =>
      MedicationReconciliationEvent.discharge,
  };

  String get label => switch (this) {
    MedicationReconciliationReviewFilter.all => 'All reconciliation events',
    MedicationReconciliationReviewFilter.admission => 'Admission review',
    MedicationReconciliationReviewFilter.transfer => 'Transfer review',
    MedicationReconciliationReviewFilter.discharge => 'Discharge review',
  };
}

/// Must be opened with patient and admission IDs selected in an authenticated
/// parent. Do not put bearer tokens or clinical documents in navigation URLs.
class MedicationReconciliationReviewScreen extends StatefulWidget {
  const MedicationReconciliationReviewScreen({
    super.key,
    required this.dataSource,
    required this.patientId,
    required this.admissionId,
  }) : assert(patientId > 0),
       assert(admissionId > 0);

  final MedicationReconciliationReadRemoteDataSource dataSource;
  final int patientId;
  final int admissionId;

  @override
  State<MedicationReconciliationReviewScreen> createState() =>
      _MedicationReconciliationReviewScreenState();
}

class _MedicationReconciliationReviewScreenState
    extends State<MedicationReconciliationReviewScreen> {
  static const int _perPage = 20;

  MedicationReconciliationReviewFilter _filter =
      MedicationReconciliationReviewFilter.all;
  MedicationReconciliationPage? _page;
  MedicationReconciliationRecord? _detail;
  String? _listError;
  String? _detailError;
  bool _loading = true;
  bool _detailLoading = false;
  bool _requiresReopen = false;
  int _generation = 0;

  bool get _available =>
      widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes &&
      !_requiresReopen;

  bool _current(int generation, int sessionRevision) =>
      mounted &&
      _generation == generation &&
      widget.dataSource.sessionNavigationGuard.revision == sessionRevision &&
      _available;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_readPage(1));
  }

  @override
  void didUpdateWidget(
    covariant MedicationReconciliationReviewScreen oldWidget,
  ) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.dataSource, widget.dataSource) ||
        oldWidget.patientId != widget.patientId ||
        oldWidget.admissionId != widget.admissionId) {
      oldWidget.dataSource.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      widget.dataSource.sessionNavigationGuard.addListener(_sessionChanged);
      _generation++;
      _page = null;
      _detail = null;
      _listError = null;
      _detailError = null;
      _loading = false;
      _detailLoading = false;
      _requiresReopen = false;
      _filter = MedicationReconciliationReviewFilter.all;
      unawaited(_readPage(1));
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_sessionChanged);
    super.dispose();
  }

  void _sessionChanged() {
    // Never keep a clinical snapshot after a logout, context change or token
    // lifecycle transition. Reopening must originate in a fresh parent context.
    _generation++;
    _page = null;
    _detail = null;
    _listError = null;
    _detailError = null;
    _loading = false;
    _detailLoading = false;
    _requiresReopen = true;
    if (mounted) {
      setState(() {});
    }
  }

  Future<void> _readPage(int page) async {
    if (!_available || page < 1) {
      return;
    }
    final revision = widget.dataSource.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _loading = true;
      _page = null;
      _detail = null;
      _listError = null;
      _detailError = null;
      _detailLoading = false;
    });
    try {
      final result = await widget.dataSource.listForAdmission(
        admissionId: widget.admissionId,
        expectedPatientId: widget.patientId,
        event: _filter.serverEvent,
        page: page,
        perPage: _perPage,
      );
      if (!_current(generation, revision)) {
        return;
      }
      setState(() {
        _page = result;
        _loading = false;
      });
    } on Object {
      if (!_current(generation, revision)) {
        return;
      }
      setState(() {
        _loading = false;
        _listError =
            'Reconciliation records unavailable or access not confirmed. '
            'Check your connection and refresh the server read.';
      });
    }
  }

  Future<void> _readDetail(MedicationReconciliationRecord selected) async {
    if (!_available || _loading || _detailLoading) {
      return;
    }
    final revision = widget.dataSource.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _detail = null;
      _detailError = null;
      _detailLoading = true;
    });
    try {
      final result = await widget.dataSource.getById(
        reconciliationId: selected.id,
        expectedPatientId: widget.patientId,
        expectedAdmissionId: widget.admissionId,
      );
      if (!_current(generation, revision)) {
        return;
      }
      if (result.event != selected.event ||
          result.wardTransferId != selected.wardTransferId ||
          result.eventIdentity != selected.eventIdentity) {
        throw const FormatException('Reconciliation event identity changed.');
      }
      setState(() {
        _detail = result;
        _detailLoading = false;
      });
    } on Object {
      if (!_current(generation, revision)) {
        return;
      }
      setState(() {
        _detailLoading = false;
        _detailError =
            'Reconciliation detail unavailable, changed or not authorized. '
            'Refresh the admission review.';
      });
    }
  }

  void _changeFilter(MedicationReconciliationReviewFilter filter) {
    if (!_available || _loading || _detailLoading || filter == _filter) {
      return;
    }
    setState(() => _filter = filter);
    unawaited(_readPage(1));
  }

  static String _eventLabel(MedicationReconciliationEvent event) =>
      switch (event) {
        MedicationReconciliationEvent.admission => 'Admission',
        MedicationReconciliationEvent.transfer => 'Transfer',
        MedicationReconciliationEvent.discharge => 'Discharge',
      };

  static String _recordStatus(MedicationReconciliationRecordStatus status) =>
      switch (status) {
        MedicationReconciliationRecordStatus.draft => 'Draft — not finalized',
        MedicationReconciliationRecordStatus.finalised => 'Finalized by server',
        MedicationReconciliationRecordStatus.amended => 'Amended server record',
      };

  static String _decisionLabel(MedicationReconciliationDecision decision) =>
      switch (decision) {
        MedicationReconciliationDecision.continueMedication => 'Continue',
        MedicationReconciliationDecision.hold => 'Hold',
        MedicationReconciliationDecision.stop => 'Stop',
        MedicationReconciliationDecision.replace => 'Replace',
        MedicationReconciliationDecision.newMedication => 'New',
        MedicationReconciliationDecision.verify => 'Verify — unresolved',
        MedicationReconciliationDecision.unknown => 'Unknown — unresolved',
      };

  static String _timestamp(DateTime? value) =>
      value == null ? 'Not recorded' : '${value.toIso8601String()} UTC';

  static Widget _optionalField(String label, String? value) {
    if (value == null || value.isEmpty) {
      return const SizedBox.shrink();
    }
    return Padding(
      padding: const EdgeInsets.only(top: 3),
      child: Text('$label: $value'),
    );
  }

  Widget _detailPanel(MedicationReconciliationRecord record) {
    return Card(
      key: const ValueKey('p23g-s02-detail'),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Server detail — reconciliation #${record.id}',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            Text('Event: ${_eventLabel(record.event)}'),
            if (record.wardTransferId != null)
              Text('Ward transfer #${record.wardTransferId}'),
            Text('Record status: ${_recordStatus(record.status)}'),
            Text('Server version: ${record.version}'),
            Text('Last updated: ${_timestamp(record.updatedAt)}'),
            Text('Finalized at: ${_timestamp(record.finalizedAt)}'),
            if (!record.isClinicallyFinal)
              const Text(
                'DRAFT — review is not clinically completed.',
                key: ValueKey('p23g-s02-draft-warning'),
              ),
            if (record.hasUnresolvedDecisions)
              const Text(
                'Unresolved VERIFY or UNKNOWN medication decisions.',
                key: ValueKey('p23g-s02-unresolved-warning'),
              ),
            if (record.noMedicationsReported)
              const Text(
                'No medications reported in this reconciliation.',
                key: ValueKey('p23g-s02-no-medications'),
              ),
            if (!record.noMedicationsReported && record.items.isEmpty)
              const Text('No medication decisions documented in this draft.'),
            _optionalField('Clinical notes', record.notes),
            for (var i = 0; i < record.items.length; i++)
              Padding(
                key: ValueKey('p23g-s02-medication-$i'),
                padding: const EdgeInsets.only(top: 12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Medication: ${record.items[i].medicationName}',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                    Text(
                      'Server decision: ${_decisionLabel(record.items[i].decision)}',
                    ),
                    _optionalField(
                      'Replacement',
                      record.items[i].replacementMedicationName,
                    ),
                    _optionalField(
                      'Dose (documented)',
                      record.items[i].doseText,
                    ),
                    _optionalField(
                      'Route (documented)',
                      record.items[i].routeText,
                    ),
                    _optionalField(
                      'Frequency (documented)',
                      record.items[i].frequencyText,
                    ),
                    _optionalField(
                      'Instructions',
                      record.items[i].instructions,
                    ),
                    _optionalField('Rationale', record.items[i].rationale),
                    if (record.items[i].sourcePrescriptionItemId != null)
                      Text(
                        'Source prescription item #${record.items[i].sourcePrescriptionItemId}',
                      ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final page = _page;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Medication reconciliation review'),
        actions: [
          IconButton(
            key: const ValueKey('p23g-s02-refresh'),
            tooltip: 'Refresh server reconciliation',
            onPressed: _available && !_loading && !_detailLoading
                ? () => unawaited(_readPage(page?.page ?? 1))
                : null,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: !_available
          ? const Center(
              key: ValueKey('p23g-s02-session-unavailable'),
              child: Padding(
                padding: EdgeInsets.all(24),
                child: Text(
                  'Reconciliation session changed or unavailable. '
                  'Reopen from the current authenticated patient and admission.',
                ),
              ),
            )
          : RefreshIndicator(
              onRefresh: () => _readPage(page?.page ?? 1),
              child: ListView(
                key: const ValueKey('p23g-s02-workspace'),
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.all(16),
                children: [
                  Text(
                    'Patient #${widget.patientId} · Admission #${widget.admissionId}',
                    key: const ValueKey('p23g-s02-context'),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Read-only server records. Medication decisions and '
                    'finalization are made only by authorized clinical staff. '
                    'Home-medication history is not independently verified by this view.',
                  ),
                  const SizedBox(height: 12),
                  DropdownButton<MedicationReconciliationReviewFilter>(
                    key: const ValueKey('p23g-s02-filter'),
                    value: _filter,
                    isExpanded: true,
                    onChanged: _loading || _detailLoading
                        ? null
                        : (value) {
                            if (value != null) {
                              _changeFilter(value);
                            }
                          },
                    items: [
                      for (final item
                          in MedicationReconciliationReviewFilter.values)
                        DropdownMenuItem<MedicationReconciliationReviewFilter>(
                          value: item,
                          child: Text(item.label),
                        ),
                    ],
                  ),
                  if (_loading)
                    const LinearProgressIndicator(
                      key: ValueKey('p23g-s02-loading'),
                    ),
                  if (_listError != null) ...[
                    Text(_listError!, key: const ValueKey('p23g-s02-error')),
                    TextButton(
                      key: const ValueKey('p23g-s02-retry'),
                      onPressed: () => unawaited(_readPage(1)),
                      child: const Text('Retry server read'),
                    ),
                  ],
                  if (!_loading && page != null && page.items.isEmpty)
                    const Text(
                      'No reconciliation records returned for this admission and filter.',
                      key: ValueKey('p23g-s02-empty'),
                    ),
                  if (!_loading && page != null) ...[
                    for (final record in page.items)
                      Card(
                        child: ListTile(
                          key: ValueKey('p23g-s02-record-${record.id}'),
                          title: Text(
                            '${_eventLabel(record.event)} reconciliation #${record.id}',
                          ),
                          subtitle: Text(
                            'Server status: ${_recordStatus(record.status)} · '
                            'Version ${record.version} · '
                            '${record.items.length} medication entries',
                          ),
                          onTap: !_detailLoading
                              ? () => unawaited(_readDetail(record))
                              : null,
                        ),
                      ),
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            'Page ${page.page} · ${page.total} server records',
                            key: const ValueKey('p23g-s02-page-label'),
                          ),
                        ),
                        IconButton(
                          key: const ValueKey('p23g-s02-previous'),
                          tooltip: 'Previous page',
                          onPressed: !_detailLoading && page.page > 1
                              ? () => unawaited(_readPage(page.page - 1))
                              : null,
                          icon: const Icon(Icons.chevron_left),
                        ),
                        IconButton(
                          key: const ValueKey('p23g-s02-next'),
                          tooltip: 'Next page',
                          onPressed: !_detailLoading && page.hasNext
                              ? () => unawaited(_readPage(page.page + 1))
                              : null,
                          icon: const Icon(Icons.chevron_right),
                        ),
                      ],
                    ),
                  ],
                  if (_detailLoading)
                    const LinearProgressIndicator(
                      key: ValueKey('p23g-s02-detail-loading'),
                    ),
                  if (_detailError != null)
                    Text(
                      _detailError!,
                      key: const ValueKey('p23g-s02-detail-error'),
                    ),
                  if (_detail != null) _detailPanel(_detail!),
                ],
              ),
            ),
    );
  }
}
