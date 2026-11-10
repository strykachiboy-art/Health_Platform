import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_prescription.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';

/// Read-only server-owned prescription history.
/// No prescribing, cancellation, fulfillment or local PHI persistence.
class PatientPrescriptionHistoryCard extends StatefulWidget {
  const PatientPrescriptionHistoryCard({
    super.key,
    required this.patientId,
    required this.dataSource,
    this.pageSize = 20,
    this.onPrescriptionDetailRequested,
  }) : assert(patientId > 0),
       assert(pageSize > 0 && pageSize <= 500);

  final int patientId;
  final PatientPrescriptionRemoteDataSource dataSource;
  final int pageSize;
  final void Function(int prescriptionId)? onPrescriptionDetailRequested;

  @override
  State<PatientPrescriptionHistoryCard> createState() =>
      _PatientPrescriptionHistoryCardState();
}

class _PatientPrescriptionHistoryCardState
    extends State<PatientPrescriptionHistoryCard> {
  List<PatientPrescription> _items = const [];
  int? _total;
  int _page = 0;
  int _generation = 0;
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_sessionChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_load());
    });
  }

  @override
  void didUpdateWidget(covariant PatientPrescriptionHistoryCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.patientId == widget.patientId &&
        identical(oldWidget.dataSource, widget.dataSource) &&
        oldWidget.pageSize == widget.pageSize) {
      return;
    }
    oldWidget.dataSource.sessionNavigationGuard.removeListener(_sessionChanged);
    widget.dataSource.sessionNavigationGuard.addListener(_sessionChanged);
    _generation++;
    _clear();
    _loading = false;
    _error = null;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_load());
    });
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_sessionChanged);
    super.dispose();
  }

  void _clear() {
    _items = const [];
    _total = null;
    _page = 0;
  }

  void _sessionChanged() {
    if (!mounted) return;
    _generation++;
    final allowed =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;
    setState(() {
      _clear();
      _loading = false;
      _error = allowed ? null : 'Session unavailable. Prescriptions cleared.';
    });
    if (allowed) unawaited(_load());
  }

  Future<void> _load({bool more = false}) async {
    if (_loading || (more && (_total == null || _items.length >= _total!))) {
      return;
    }

    final source = widget.dataSource;
    final guard = source.sessionNavigationGuard;

    if (!guard.canAccessProtectedRoutes) {
      setState(() {
        _generation++;
        _clear();
        _loading = false;
        _error = 'Session unavailable. Prescriptions cleared.';
      });
      return;
    }

    final patientId = widget.patientId;
    final pageSize = widget.pageSize;
    final revision = guard.revision;
    final generation = ++_generation;
    final requestedPage = more ? _page + 1 : 1;

    setState(() {
      if (!more) _clear();
      _loading = true;
      _error = null;
    });

    bool current() =>
        mounted &&
        generation == _generation &&
        identical(source, widget.dataSource) &&
        patientId == widget.patientId &&
        pageSize == widget.pageSize &&
        guard.revision == revision &&
        guard.canAccessProtectedRoutes;

    try {
      final result = await source.fetchPrescriptions(
        patientId: patientId,
        page: requestedPage,
        perPage: pageSize,
      );
      if (!current()) return;

      final combined = more
          ? <PatientPrescription>[..._items, ...result.items]
          : <PatientPrescription>[...result.items];

      if ((more && result.total != _total) ||
          combined.length > result.total ||
          combined.map((item) => item.id).toSet().length != combined.length ||
          (result.items.isEmpty && combined.length < result.total)) {
        throw const FormatException('Inconsistent prescription pages.');
      }

      setState(() {
        _items = List<PatientPrescription>.unmodifiable(combined);
        _total = result.total;
        _page = result.page;
        _loading = false;
      });
    } catch (error) {
      if (!current()) return;
      setState(() {
        _clear();
        _loading = false;
        _error = _errorFor(error);
      });
    }
  }

  String _errorFor(Object error) {
    if (error is PatientPrescriptionSessionMismatchException) {
      return 'Session unavailable. Prescriptions cleared.';
    }
    if (error is ApiException &&
        const <int>{401, 403, 404, 422}.contains(error.statusCode)) {
      return 'Prescription access denied or records unavailable.';
    }
    return 'Prescriptions could not be verified. Retry online.';
  }

  String _status(PatientPrescriptionStatus value) => switch (value) {
    PatientPrescriptionStatus.active => 'Active',
    PatientPrescriptionStatus.completed => 'Completed',
    PatientPrescriptionStatus.cancelled => 'Cancelled',
    PatientPrescriptionStatus.expired => 'Expired',
  };

  Widget _record(PatientPrescription prescription) {
    final details = <String>[
      'Prescription ID: ${prescription.id}',
      'Status: ${_status(prescription.status)}',
      'Prescribed by staff ID: ${prescription.prescribedById}',
      if (prescription.issuedAt != null)
        'Issued (backend time): ${prescription.issuedAt}',
      if (prescription.expiresAt != null)
        'Expires (backend time): ${prescription.expiresAt}',
      if (prescription.consultationId != null)
        'Consultation ID: ${prescription.consultationId}',
    ];

    return Padding(
      key: ValueKey('patient-prescription-${prescription.id}'),
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final detail in details)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 2),
              child: Text(detail),
            ),
          for (final item in prescription.items)
            Padding(
              key: ValueKey('patient-prescription-item-${item.id}'),
              padding: const EdgeInsets.only(top: 8, left: 8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Drug ID: ${item.drugId}'),
                  if (item.dosage != null) Text('Dosage: ${item.dosage}'),
                  if (item.frequency != null)
                    Text('Frequency: ${item.frequency}'),
                  if (item.duration != null) Text('Duration: ${item.duration}'),
                  if (item.quantity != null) Text('Quantity: ${item.quantity}'),
                  if (item.instructions != null)
                    Text('Instructions: ${item.instructions}'),
                ],
              ),
            ),
          if (widget.onPrescriptionDetailRequested != null &&
              widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes)
            TextButton(
              key: ValueKey('p23h-prescription-detail-link-${prescription.id}'),
              onPressed: () =>
                  widget.onPrescriptionDetailRequested!(prescription.id),
              child: const Text('Open prescription detail'),
            ),
          const Divider(),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final guard = widget.dataSource.sessionNavigationGuard;
    final canLoadMore = !_loading && _total != null && _items.length < _total!;

    return Card(
      key: const ValueKey('patient-prescriptions-card'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Patient prescriptions',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                if (!_loading &&
                    _error == null &&
                    _total != null &&
                    guard.canAccessProtectedRoutes)
                  IconButton(
                    key: const ValueKey('patient-prescriptions-refresh'),
                    tooltip: 'Refresh prescriptions',
                    icon: const Icon(Icons.refresh),
                    onPressed: () => unawaited(_load()),
                  ),
              ],
            ),
            const Text(
              'Server-recorded orders only. No medication changes '
              'can be made from this panel.',
            ),
            const SizedBox(height: 12),
            if (_error != null) ...[
              Text(_error!, key: const ValueKey('patient-prescriptions-error')),
              if (guard.canAccessProtectedRoutes)
                OutlinedButton(
                  key: const ValueKey('patient-prescriptions-retry'),
                  onPressed: () => unawaited(_load()),
                  child: const Text('Retry prescriptions'),
                ),
            ] else if (_loading && _total == null)
              const Center(
                child: CircularProgressIndicator(
                  key: ValueKey('patient-prescriptions-loading'),
                ),
              )
            else if (_total == null)
              const Text('Preparing prescription history...')
            else ...[
              Text('Prescriptions: ${_items.length} of $_total'),
              if (_items.isEmpty)
                const Text(
                  'No prescriptions returned on this request. '
                  'This is not proof of an empty medication history.',
                  key: ValueKey('patient-prescriptions-empty'),
                ),
              for (final item in _items) _record(item),
              if (_loading)
                const Center(child: CircularProgressIndicator())
              else if (canLoadMore)
                OutlinedButton(
                  key: const ValueKey('patient-prescriptions-load-more'),
                  onPressed: () => unawaited(_load(more: true)),
                  child: const Text('Load more prescriptions'),
                ),
            ],
          ],
        ),
      ),
    );
  }
}
