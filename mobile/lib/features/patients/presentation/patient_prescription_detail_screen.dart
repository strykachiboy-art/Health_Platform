// P23H.S01 — server-authoritative prescription detail, read-only.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_prescription_detail.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';

class PatientPrescriptionDetailScreen extends StatefulWidget {
  const PatientPrescriptionDetailScreen({
    super.key,
    required this.dataSource,
    required this.patientId,
    required this.prescriptionId,
  }) : assert(patientId > 0),
       assert(prescriptionId > 0);

  final PatientPrescriptionRemoteDataSource dataSource;
  final int patientId;
  final int prescriptionId;

  @override
  State<PatientPrescriptionDetailScreen> createState() =>
      _PatientPrescriptionDetailScreenState();
}

class _PatientPrescriptionDetailScreenState
    extends State<PatientPrescriptionDetailScreen> {
  PatientPrescriptionDetail? _detail;
  String? _error;
  bool _loading = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_onGuardChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_read());
    });
  }

  @override
  void didUpdateWidget(covariant PatientPrescriptionDetailScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (identical(oldWidget.dataSource, widget.dataSource) &&
        oldWidget.patientId == widget.patientId &&
        oldWidget.prescriptionId == widget.prescriptionId) {
      return;
    }
    oldWidget.dataSource.sessionNavigationGuard.removeListener(_onGuardChanged);
    widget.dataSource.sessionNavigationGuard.addListener(_onGuardChanged);
    _generation++;
    _detail = null;
    _error = null;
    _loading = false;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_read());
    });
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onGuardChanged);
    super.dispose();
  }

  void _onGuardChanged() {
    if (!mounted) return;
    _generation++;
    setState(() {
      _detail = null;
      _loading = false;
      _error = 'Session changed. Prescription detail cleared.';
    });
  }

  Future<void> _read() async {
    if (_loading) return;
    final source = widget.dataSource;
    final guard = source.sessionNavigationGuard;
    final revision = guard.revision;
    final patientId = widget.patientId;
    final prescriptionId = widget.prescriptionId;
    final generation = ++_generation;
    if (!guard.canAccessProtectedRoutes) {
      setState(() {
        _detail = null;
        _error = 'Session unavailable. Prescription detail cleared.';
      });
      return;
    }
    setState(() {
      _detail = null;
      _error = null;
      _loading = true;
    });
    bool current() =>
        mounted &&
        generation == _generation &&
        identical(source, widget.dataSource) &&
        widget.patientId == patientId &&
        widget.prescriptionId == prescriptionId &&
        guard.revision == revision &&
        guard.canAccessProtectedRoutes;
    try {
      final result = await source.fetchPrescriptionDetail(
        prescriptionId: prescriptionId,
        expectedPatientId: patientId,
      );
      if (!current()) return;
      setState(() {
        _detail = result;
        _loading = false;
      });
    } on Object catch (error) {
      if (!current()) return;
      setState(() {
        _detail = null;
        _loading = false;
        _error = error is PatientPrescriptionSessionMismatchException
            ? 'Session unavailable. Prescription detail cleared.'
            : error is ApiException &&
                  const <int>{401, 403, 404, 422}.contains(error.statusCode)
            ? 'Prescription detail access denied or unavailable.'
            : 'Prescription detail could not be verified from server.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    return Scaffold(
      appBar: AppBar(title: const Text('Prescription detail')),
      body: _loading
          ? const Center(
              child: CircularProgressIndicator(
                key: ValueKey('p23h-s01-detail-loading'),
              ),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: <Widget>[
                if (_error != null) ...<Widget>[
                  Text(_error!, key: const ValueKey('p23h-s01-detail-error')),
                  const SizedBox(height: 8),
                ],
                if (detail != null) ...<Widget>[
                  Text(
                    'Prescription #${detail.prescription.id}',
                    key: const ValueKey('p23h-s01-detail-record'),
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  Text('Patient #${detail.prescription.patientId}'),
                  Text('Server status: ${detail.prescription.status.name}'),
                  Text(
                    'Prescriber staff #${detail.prescription.prescribedById}',
                  ),
                  if (detail.prescription.issuedAt != null)
                    Text('Issued: ${detail.prescription.issuedAt}'),
                  if (detail.prescription.expiresAt != null)
                    Text('Expires: ${detail.prescription.expiresAt}'),
                  if (detail.prescription.notes != null)
                    Text('Notes: ${detail.prescription.notes}'),
                  for (var i = 0; i < detail.structuredItems.length; i++)
                    Card(
                      key: ValueKey(
                        'p23h-s01-detail-item-${detail.structuredItems[i].id}',
                      ),
                      child: Padding(
                        padding: const EdgeInsets.all(12),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Text(
                              'Drug #${detail.structuredItems[i].drugId}',
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                            Text(
                              'Priority: ${detail.structuredItems[i].priority}',
                            ),
                            Text(
                              'Care setting: ${detail.structuredItems[i].orderType}',
                            ),
                            if (detail.structuredItems[i].doseValue != null)
                              Text(
                                'Dose: ${detail.structuredItems[i].doseValue} ${detail.structuredItems[i].doseUnit ?? '(unit unspecified)'}',
                              ),
                            if (detail.structuredItems[i].routeDisplay != null)
                              Text(
                                'Route: ${detail.structuredItems[i].routeDisplay}',
                              ),
                            if (detail.structuredItems[i].frequencyCode != null)
                              Text(
                                'Frequency code: ${detail.structuredItems[i].frequencyCode}',
                              ),
                            if (detail.structuredItems[i].frequencyInterval !=
                                null)
                              Text(
                                'Interval: ${detail.structuredItems[i].frequencyInterval} ${detail.structuredItems[i].frequencyIntervalUnit}',
                              ),
                            if (detail
                                .structuredItems[i]
                                .scheduledTimes
                                .isNotEmpty)
                              Text(
                                'Schedule: ${detail.structuredItems[i].scheduledTimes.join(', ')}',
                              ),
                            Text(
                              'PRN: ${detail.structuredItems[i].isPrn ? 'Yes' : 'No'}',
                            ),
                            if (detail.structuredItems[i].prnReason != null)
                              Text(
                                'PRN indication: ${detail.structuredItems[i].prnReason}',
                              ),
                            if (detail.structuredItems[i].maxSingleDose != null)
                              Text(
                                'Max single dose: ${detail.structuredItems[i].maxSingleDose}',
                              ),
                            if (detail.structuredItems[i].maxDailyDose != null)
                              Text(
                                'Max daily dose: ${detail.structuredItems[i].maxDailyDose}',
                              ),
                            if (detail
                                    .structuredItems[i]
                                    .administrationInstructions !=
                                null)
                              Text(
                                'Administration: ${detail.structuredItems[i].administrationInstructions}',
                              ),
                            if (detail.structuredItems[i].startAt != null)
                              Text(
                                'Start UTC: ${detail.structuredItems[i].startAt!.toIso8601String()}',
                              ),
                            if (detail.structuredItems[i].endAt != null)
                              Text(
                                'End UTC: ${detail.structuredItems[i].endAt!.toIso8601String()}',
                              ),
                          ],
                        ),
                      ),
                    ),
                  const Text(
                    'Read-only, server-confirmed prescription information. '
                    'No medication is ordered, changed or dispensed here.',
                    key: ValueKey('p23h-s01-readonly-note'),
                  ),
                ],
                OutlinedButton(
                  key: const ValueKey('p23h-s01-detail-refresh'),
                  onPressed:
                      _loading ||
                          !widget
                              .dataSource
                              .sessionNavigationGuard
                              .canAccessProtectedRoutes
                      ? null
                      : () => unawaited(_read()),
                  child: const Text('Refresh from server'),
                ),
              ],
            ),
    );
  }
}
