import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert.dart';
import 'package:clinic_system_pro/features/patients/data/patient_clinical_alert_remote_data_source.dart';

/// Read-only, patient-filtered clinical safety alert panel.
/// A page of alerts never proves the absence of other clinical risks.
class PatientClinicalAlertsCard extends StatefulWidget {
  const PatientClinicalAlertsCard({
    super.key,
    required this.patientId,
    required this.dataSource,
    this.pageSize = 20,
  }) : assert(patientId > 0),
       assert(pageSize > 0 && pageSize <= 500);

  final int patientId;
  final PatientClinicalAlertRemoteDataSource dataSource;
  final int pageSize;

  @override
  State<PatientClinicalAlertsCard> createState() =>
      _PatientClinicalAlertsCardState();
}

class _PatientClinicalAlertsCardState extends State<PatientClinicalAlertsCard> {
  List<ClinicalAlert> _items = const [];
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
  void didUpdateWidget(covariant PatientClinicalAlertsCard oldWidget) {
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
      _error = allowed ? null : 'Session unavailable. Alerts cleared.';
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
        _error = 'Session unavailable. Alerts cleared.';
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
      final result = await source.fetchPatientAlerts(
        patientId: patientId,
        page: requestedPage,
        perPage: pageSize,
      );
      if (!current()) return;

      final combined = more
          ? <ClinicalAlert>[..._items, ...result.items]
          : <ClinicalAlert>[...result.items];

      if ((more && result.total != _total) ||
          combined.length > result.total ||
          combined.map((item) => item.id).toSet().length != combined.length ||
          (result.items.isEmpty && combined.length < result.total)) {
        throw const FormatException('Inconsistent patient alert pages.');
      }

      setState(() {
        _items = List<ClinicalAlert>.unmodifiable(combined);
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
    if (error is PatientClinicalAlertSessionMismatchException) {
      return 'Session unavailable. Alerts cleared.';
    }
    if (error is ApiException &&
        const <int>{401, 403, 404, 422}.contains(error.statusCode)) {
      return 'Clinical alert access denied or records unavailable.';
    }
    return 'Clinical alert status is unconfirmed. Retry online.';
  }

  String _severity(ClinicalAlertSeverity value) => switch (value) {
    ClinicalAlertSeverity.info => 'Info',
    ClinicalAlertSeverity.low => 'Low',
    ClinicalAlertSeverity.moderate => 'Moderate',
    ClinicalAlertSeverity.high => 'High',
    ClinicalAlertSeverity.critical => 'Critical',
  };

  String _status(ClinicalAlertStatus value) => switch (value) {
    ClinicalAlertStatus.open => 'Open',
    ClinicalAlertStatus.acknowledged => 'Acknowledged',
    ClinicalAlertStatus.overridden => 'Overridden',
    ClinicalAlertStatus.resolved => 'Resolved',
    ClinicalAlertStatus.expired => 'Expired',
  };

  String _action(ClinicalAlertAction value) => switch (value) {
    ClinicalAlertAction.inform => 'Inform',
    ClinicalAlertAction.alert => 'Alert',
    ClinicalAlertAction.requireAcknowledgement => 'Require acknowledgement',
    ClinicalAlertAction.requireJustification => 'Require justification',
    ClinicalAlertAction.block => 'Block',
  };

  Widget _record(ClinicalAlert alert) {
    final unresolvedCritical =
        alert.severity == ClinicalAlertSeverity.critical &&
        alert.status != ClinicalAlertStatus.resolved &&
        alert.status != ClinicalAlertStatus.expired;

    return Padding(
      key: ValueKey('patient-clinical-alert-${alert.id}'),
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(alert.title, style: Theme.of(context).textTheme.titleSmall),
          Text('Severity: ${_severity(alert.severity)}'),
          Text('Action: ${_action(alert.action)}'),
          Text('Status: ${_status(alert.status)}'),
          Text(alert.message),
          Text(
            'Generated (UTC): ${alert.generatedAt.toUtc().toIso8601String()}',
          ),
          if (unresolvedCritical)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 8),
              child: Text(
                'CRITICAL: verify the current backend alert state '
                'before clinical action.',
                key: ValueKey('patient-alert-critical-warning'),
              ),
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
      key: const ValueKey('patient-clinical-alerts-card'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Patient clinical alerts',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                if (!_loading &&
                    _error == null &&
                    _total != null &&
                    guard.canAccessProtectedRoutes)
                  IconButton(
                    key: const ValueKey('patient-alerts-refresh'),
                    tooltip: 'Refresh patient alerts',
                    icon: const Icon(Icons.refresh),
                    onPressed: () => unawaited(_load()),
                  ),
              ],
            ),
            const Text(
              'Backend-reported alerts only. This panel does not '
              'authorize care, acknowledge alerts or resolve blockers.',
            ),
            const SizedBox(height: 8),
            const Text(
              'An empty or partial alert list is not clinical clearance.',
              key: ValueKey('patient-alerts-no-clearance-warning'),
            ),
            const SizedBox(height: 12),
            if (_error != null) ...[
              Text(_error!, key: const ValueKey('patient-alerts-error')),
              if (guard.canAccessProtectedRoutes)
                OutlinedButton(
                  key: const ValueKey('patient-alerts-retry'),
                  onPressed: () => unawaited(_load()),
                  child: const Text('Retry alerts'),
                ),
            ] else if (_loading && _total == null)
              const Center(
                child: CircularProgressIndicator(
                  key: ValueKey('patient-alerts-loading'),
                ),
              )
            else if (_total == null)
              const Text('Preparing patient alert records...')
            else ...[
              Text('Alerts loaded: ${_items.length} of $_total'),
              if (_items.isEmpty)
                const Text(
                  'No alerts returned for this patient on this request.',
                  key: ValueKey('patient-alerts-empty'),
                ),
              for (final item in _items) _record(item),
              if (_loading)
                const Center(child: CircularProgressIndicator())
              else if (canLoadMore)
                OutlinedButton(
                  key: const ValueKey('patient-alerts-load-more'),
                  onPressed: () => unawaited(_load(more: true)),
                  child: const Text('Load more alerts'),
                ),
            ],
          ],
        ),
      ),
    );
  }
}
