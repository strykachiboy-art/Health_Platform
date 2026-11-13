import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';

import '../data/models/surgery_read_models.dart';
import '../data/surgery_read_remote_data_source.dart';

/// Seven existing CE1 dashboard roles, projected without a new API or grant.
/// These are documentation counts, never clinical completion/clearance.
class SurgeryDashboardSummary extends StatelessWidget {
  const SurgeryDashboardSummary({super.key, required this.widgetData});

  final SurgeryDashboardWidget widgetData;

  @override
  Widget build(BuildContext context) {
    final data = widgetData;
    final restricted = data.visibility == 'restricted';
    final patient = data.view == 'patient';
    final value = switch (data.view) {
      'operations' => data.scheduledBookings,
      'finance' => data.linkedBillingInvoices,
      _ => data.totalCases,
    };
    final label = switch (data.view) {
      'super_admin' => 'System-wide surgical documentation cases',
      'management' => 'Clinic surgical documentation cases',
      'doctor' => 'My assigned surgical cases',
      'nurse' => 'My assigned surgical nursing cases',
      'operations' => 'Scheduled theatre bookings',
      'finance' => 'Linked surgery invoice records',
      'patient' => 'My records awaiting release',
      _ => 'Surgery unavailable',
    };
    return Card(
      key: const ValueKey('surgery-dashboard-summary'),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Surgery', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            if (restricted)
              const Text(
                'No Surgery information is available for this role.',
                key: ValueKey('surgery-restricted'),
              )
            else ...[
              Text(
                '$label: $value',
                key: const ValueKey('surgery-summary-count'),
              ),
              if (!patient &&
                  const {
                    'super_admin',
                    'management',
                    'doctor',
                    'nurse',
                  }.contains(data.view)) ...[
                Text('Reports awaiting signatures: ${data.pendingSignatures}'),
                Text('Dual-signed reports: ${data.dualSignedReports}'),
                Text(
                  'Reviewed PACU preparations: '
                  '${data.reviewedPacuPreparations}',
                ),
              ],
              if (patient)
                const Text(
                  'Clinical documents are not available until '
                  'a separate authorized release is confirmed.',
                  key: ValueKey('surgery-patient-release-required'),
                ),
              if (data.view == 'super_admin')
                const Text('System aggregates only; no patient or case IDs.'),
              const SizedBox(height: 8),
              const Text(
                'Read-only documentation status. This does not '
                'authorize surgery, medication, transfer, sign-out, '
                'discharge or billing clearance.',
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// GET-only paginated worklist; clinical IDs are never stored across sessions.
class SurgeryWorklistScreen extends StatefulWidget {
  const SurgeryWorklistScreen({
    super.key,
    required this.source,
    required this.guard,
  });
  final SurgeryReadRemoteDataSource source;
  final SessionNavigationGuard guard;

  @override
  State<SurgeryWorklistScreen> createState() => _SurgeryWorklistScreenState();
}

class _SurgeryWorklistScreenState extends State<SurgeryWorklistScreen> {
  SurgeryWorklist? _data;
  String? _error;
  bool _loading = true;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.guard.addListener(_onSession);
    unawaited(_load(0));
  }

  @override
  void didUpdateWidget(covariant SurgeryWorklistScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.guard, widget.guard)) {
      oldWidget.guard.removeListener(_onSession);
      widget.guard.addListener(_onSession);
    }
    if (!identical(oldWidget.guard, widget.guard) ||
        !identical(oldWidget.source, widget.source)) {
      _onSession();
    }
  }

  void _onSession() {
    ++_generation;
    if (!mounted) return;
    setState(() {
      _data = null;
      _loading = false;
      _error = 'Session changed. Surgical records were cleared.';
    });
    if (widget.guard.canAccessProtectedRoutes) unawaited(_load(0));
  }

  Future<void> _load(int offset) async {
    final request = ++_generation;
    final revision = widget.guard.revision;
    if (!mounted) return;
    setState(() {
      _data = null;
      _error = null;
      _loading = true;
    });
    try {
      final result = await widget.source.fetchWorklist(offset: offset);
      if (!mounted ||
          request != _generation ||
          revision != widget.guard.revision ||
          !widget.guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _loading = false;
        _data = result;
      });
    } on Object {
      if (!mounted ||
          request != _generation ||
          revision != widget.guard.revision) {
        return;
      }
      setState(() {
        _loading = false;
        _data = null;
        _error = 'Surgery worklist unavailable or not authorized.';
      });
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.guard.removeListener(_onSession);
    _data = null;
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final data = _data;
    return Scaffold(
      key: const ValueKey('surgery-worklist'),
      appBar: AppBar(title: const Text('Surgery worklist')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
          ? Center(
              child: Text(
                _error!,
                key: const ValueKey('surgery-worklist-denied'),
              ),
            )
          : data == null
          ? const Center(child: Text('Surgery data unavailable.'))
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Documented cases: ${data.total}',
                  key: const ValueKey('surgery-worklist-total'),
                ),
                const Text(
                  'Read-only. A recorded case or signed report '
                  'does not establish operative completion.',
                ),
                const SizedBox(height: 12),
                if (data.cases.isEmpty)
                  const Text('No cases available in this authorized scope.'),
                for (final caseRecord in data.cases)
                  Card(
                    child: ListTile(
                      key: ValueKey('surgery-case-${caseRecord.caseId}'),
                      title: Text('Case #${caseRecord.caseId}'),
                      subtitle: Text(
                        'Case: ${caseRecord.caseStatus} · '
                        'Report: ${caseRecord.reportStatus}',
                      ),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => context.push<void>(
                        '/surgery/cases/${caseRecord.caseId}',
                      ),
                    ),
                  ),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    TextButton(
                      onPressed: data.offset == 0
                          ? null
                          : () => unawaited(
                              _load(
                                data.offset >= data.limit
                                    ? data.offset - data.limit
                                    : 0,
                              ),
                            ),
                      child: const Text('Previous'),
                    ),
                    TextButton(
                      onPressed:
                          data.offset + data.cases.length >= data.total ||
                              data.cases.isEmpty
                          ? null
                          : () => unawaited(_load(data.offset + data.limit)),
                      child: const Text('Next'),
                    ),
                  ],
                ),
                TextButton(
                  onPressed: () => unawaited(_load(data.offset)),
                  child: const Text('Refresh'),
                ),
              ],
            ),
    );
  }
}

class SurgeryCaseDetailScreen extends StatefulWidget {
  const SurgeryCaseDetailScreen({
    super.key,
    required this.source,
    required this.guard,
    required this.caseId,
  });

  final SurgeryReadRemoteDataSource source;
  final SessionNavigationGuard guard;
  final int caseId;

  @override
  State<SurgeryCaseDetailScreen> createState() =>
      _SurgeryCaseDetailScreenState();
}

class _SurgeryCaseDetailScreenState extends State<SurgeryCaseDetailScreen> {
  SurgeryCaseDetail? _detail;
  String? _error;
  bool _loading = true;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.guard.addListener(_onSession);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant SurgeryCaseDetailScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.guard, widget.guard)) {
      oldWidget.guard.removeListener(_onSession);
      widget.guard.addListener(_onSession);
    }
    if (oldWidget.caseId != widget.caseId ||
        !identical(oldWidget.source, widget.source) ||
        !identical(oldWidget.guard, widget.guard)) {
      _onSession();
    }
  }

  void _onSession() {
    ++_generation;
    if (!mounted) return;
    setState(() {
      _detail = null;
      _loading = false;
      _error = 'Session changed. Surgical case data was cleared.';
    });
    if (widget.guard.canAccessProtectedRoutes) unawaited(_load());
  }

  Future<void> _load() async {
    final request = ++_generation;
    final revision = widget.guard.revision;
    if (!mounted) return;
    setState(() {
      _detail = null;
      _error = null;
      _loading = true;
    });
    try {
      final result = await widget.source.fetchCase(widget.caseId);
      if (!mounted ||
          request != _generation ||
          revision != widget.guard.revision ||
          !widget.guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _detail = result;
        _loading = false;
      });
    } on Object {
      if (!mounted ||
          request != _generation ||
          revision != widget.guard.revision) {
        return;
      }
      setState(() {
        _detail = null;
        _loading = false;
        _error = 'Surgery case unavailable or not authorized.';
      });
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.guard.removeListener(_onSession);
    _detail = null;
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    final caseRecord = detail?.caseRecord;
    return Scaffold(
      key: const ValueKey('surgery-case-detail'),
      appBar: AppBar(title: const Text('Surgery case details')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
          ? Center(
              child: Text(_error!, key: const ValueKey('surgery-case-denied')),
            )
          : caseRecord == null
          ? const Center(child: Text('Surgery case unavailable.'))
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                Text(
                  'Case #${caseRecord.caseId}',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 16),
                Text('Case status: ${caseRecord.caseStatus}'),
                Text('Evidence journal: ${caseRecord.journalStatus}'),
                Text('PACU preparation: ${caseRecord.pacuStatus}'),
                Text('Operative report: ${caseRecord.reportStatus}'),
                Text(
                  'Documented signer roles: '
                  '${caseRecord.signatureRoles.join(', ')}',
                ),
                Text(
                  'Material trace records: '
                  '${caseRecord.materialTraceCount}',
                ),
                Text(
                  'Clinical references: '
                  '${caseRecord.clinicalReferenceCount}',
                ),
                const SizedBox(height: 20),
                const Text(
                  'Documentation only. No operative action, '
                  'patient transfer, medication administration, '
                  'billing clearance, or clinical release is available.',
                ),
                TextButton(
                  key: const ValueKey('surgery-c-open-actions'),
                  onPressed: () => context.push<void>(
                    '/surgery/cases/${widget.caseId}/actions',
                  ),
                  child: const Text('Documentation actions'),
                ),
                TextButton(
                  onPressed: () => unawaited(_load()),
                  child: const Text('Refresh from server'),
                ),
              ],
            ),
    );
  }
}
