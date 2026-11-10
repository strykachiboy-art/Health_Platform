// P23F.S02: transient, read-only eMAR workspace. All clinical actions are
// deliberately absent. Server status, not the device clock, defines "due".
import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

enum EmarScheduleView {
  due,
  scheduled,
  all,
  administered,
  held,
  refused,
  missed,
  cancelled,
}

extension on EmarScheduleView {
  EmarAdministrationStatus? get status => switch (this) {
    EmarScheduleView.due => EmarAdministrationStatus.due,
    EmarScheduleView.scheduled => EmarAdministrationStatus.scheduled,
    EmarScheduleView.all => null,
    EmarScheduleView.administered => EmarAdministrationStatus.administered,
    EmarScheduleView.held => EmarAdministrationStatus.held,
    EmarScheduleView.refused => EmarAdministrationStatus.refused,
    EmarScheduleView.missed => EmarAdministrationStatus.missed,
    EmarScheduleView.cancelled => EmarAdministrationStatus.cancelled,
  };

  String get caption => switch (this) {
    EmarScheduleView.due => 'Due (server status)',
    EmarScheduleView.scheduled => 'Scheduled (not due)',
    EmarScheduleView.all => 'All server statuses',
    EmarScheduleView.administered => 'Administered',
    EmarScheduleView.held => 'Held',
    EmarScheduleView.refused => 'Refused',
    EmarScheduleView.missed => 'Missed',
    EmarScheduleView.cancelled => 'Cancelled',
  };
}

/// Requires an admission and patient selected by an authenticated parent.
/// No bearer tokens are accepted as route/widget arguments.
class EmarDueScheduleScreen extends StatefulWidget {
  const EmarDueScheduleScreen({
    super.key,
    required this.dataSource,
    required this.patientId,
    required this.admissionId,
    this.onVerifyRequested,
    this.onTerminalRequested,
  }) : assert(patientId > 0),
       assert(admissionId > 0);

  final EmarReadRemoteDataSource dataSource;
  final int patientId;
  final int admissionId;
  final ValueChanged<EmarAdministration>? onVerifyRequested;
  final ValueChanged<EmarAdministration>? onTerminalRequested;

  @override
  State<EmarDueScheduleScreen> createState() => _EmarDueScheduleScreenState();
}

class _EmarDueScheduleScreenState extends State<EmarDueScheduleScreen> {
  static const int _perPage = 20;
  EmarScheduleView _view = EmarScheduleView.due;
  EmarAdministrationPage? _page;
  EmarAdministration? _detail;
  bool _loading = true;
  bool _detailLoading = false;
  bool _needsReopen = false;
  String? _error;
  String? _detailError;
  int _generation = 0;

  bool get _guardAvailable =>
      widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;

  bool get _available => _guardAvailable && !_needsReopen;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load(page: 1));
  }

  @override
  void didUpdateWidget(covariant EmarDueScheduleScreen oldWidget) {
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
      _error = null;
      _detailError = null;
      _detailLoading = false;
      _needsReopen = false;
      _view = EmarScheduleView.due;
      unawaited(_load(page: 1));
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_sessionChanged);
    super.dispose();
  }

  void _sessionChanged() {
    // An old patient/admission snapshot must never survive logout or a
    // changed authenticated revision, even if the guard becomes available.
    _generation++;
    _page = null;
    _detail = null;
    _error = null;
    _detailError = null;
    _loading = false;
    _detailLoading = false;
    _needsReopen = true;
    if (mounted) setState(() {});
  }

  bool _current(int generation, int revision) =>
      mounted &&
      _generation == generation &&
      widget.dataSource.sessionNavigationGuard.revision == revision &&
      _available;

  Future<void> _load({required int page}) async {
    if (!_available || page < 1) return;
    final revision = widget.dataSource.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _loading = true;
      _page = null;
      _detail = null;
      _error = null;
      _detailError = null;
      _detailLoading = false;
    });
    try {
      final result = await widget.dataSource.listForAdmission(
        admissionId: widget.admissionId,
        expectedPatientId: widget.patientId,
        status: _view.status,
        page: page,
        perPage: _perPage,
      );
      if (!_current(generation, revision)) return;
      setState(() {
        _page = result;
        _loading = false;
      });
    } on Object {
      if (!_current(generation, revision)) return;
      setState(() {
        _page = null;
        _loading = false;
        _error =
            'Medication schedule unavailable or access not confirmed. '
            'Refresh only when connected.';
      });
    }
  }

  Future<void> _readDetail(EmarAdministration selected) async {
    if (!_available || _loading || _detailLoading) return;
    final revision = widget.dataSource.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _detail = null;
      _detailLoading = true;
      _detailError = null;
    });
    try {
      final detail = await widget.dataSource.getAdministration(
        administrationId: selected.id,
        expectedPatientId: widget.patientId,
        expectedAdmissionId: widget.admissionId,
      );
      if (!_current(generation, revision)) return;
      // A detail refresh is a server snapshot, not permission to act.
      setState(() {
        _detailLoading = false;
        _detail = detail;
      });
    } on Object {
      if (!_current(generation, revision)) return;
      setState(() {
        _detailLoading = false;
        _detail = null;
        _detailError =
            'Medication detail not found, changed or not authorized.';
      });
    }
  }

  void _changeView(EmarScheduleView value) {
    if (!_available || _loading || value == _view) return;
    setState(() => _view = value);
    unawaited(_load(page: 1));
  }

  static String _date(DateTime? value) =>
      value == null ? 'Not recorded' : '${value.toIso8601String()} UTC';

  static String _dose(EmarAdministration row) {
    if (row.doseValue == null || row.doseUnit == null) {
      return 'Dose: unavailable; check clinical order';
    }
    return 'Dose: ${row.doseValue} ${row.doseUnit}';
  }

  @override
  Widget build(BuildContext context) {
    final currentPage = _page;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Medication administration (eMAR)'),
        actions: [
          IconButton(
            key: const ValueKey('p23f-s02-refresh'),
            tooltip: 'Refresh medication schedule',
            onPressed: _available && !_loading
                ? () => unawaited(_load(page: currentPage?.page ?? 1))
                : null,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: !_available
          ? const Center(
              key: ValueKey('p23f-s02-session-unavailable'),
              child: Padding(
                padding: EdgeInsets.all(24),
                child: Text(
                  'eMAR session changed or unavailable. Reopen from the '
                  'current authenticated patient and admission.',
                ),
              ),
            )
          : RefreshIndicator(
              onRefresh: () => _load(page: currentPage?.page ?? 1),
              child: ListView(
                key: const ValueKey('p23f-s02-workspace'),
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.all(16),
                children: [
                  Text(
                    'Patient #${widget.patientId} · Admission #${widget.admissionId}',
                    key: const ValueKey('p23f-s02-context'),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Online read only. A due label reflects the server status; '
                    'the device does not decide whether a dose is due. '
                    'No scanning or medication administration is enabled.',
                  ),
                  const SizedBox(height: 12),
                  DropdownButton<EmarScheduleView>(
                    key: const ValueKey('p23f-s02-filter'),
                    value: _view,
                    isExpanded: true,
                    onChanged: _loading
                        ? null
                        : (value) {
                            if (value != null) _changeView(value);
                          },
                    items: [
                      for (final value in EmarScheduleView.values)
                        DropdownMenuItem<EmarScheduleView>(
                          value: value,
                          child: Text(value.caption),
                        ),
                    ],
                  ),
                  if (_loading)
                    const LinearProgressIndicator(
                      key: ValueKey('p23f-s02-loading'),
                    ),
                  if (_error != null) ...[
                    Text(_error!, key: const ValueKey('p23f-s02-error')),
                    TextButton(
                      key: const ValueKey('p23f-s02-retry'),
                      onPressed: () => unawaited(_load(page: 1)),
                      child: const Text('Retry server read'),
                    ),
                  ],
                  if (!_loading &&
                      currentPage != null &&
                      currentPage.items.isEmpty)
                    const Text(
                      'No administrations returned for this server filter.',
                      key: ValueKey('p23f-s02-empty'),
                    ),
                  if (!_loading && currentPage != null) ...[
                    for (final record in currentPage.items)
                      ListTile(
                        key: ValueKey('p23f-s02-row-${record.id}'),
                        title: Text(
                          'Prescription item #${record.prescriptionItemId}',
                        ),
                        subtitle: Text(
                          'Server status: ${record.status.name}\n'
                          '${_dose(record)} · Route: ${record.routeCode ?? 'Unavailable'}\n'
                          'Scheduled: ${_date(record.scheduledAt)}',
                        ),
                        isThreeLine: true,
                        onTap: _detailLoading
                            ? null
                            : () => unawaited(_readDetail(record)),
                      ),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Flexible(
                          child: Text(
                            'Page ${currentPage.page} · '
                            '${currentPage.total} server records',
                            key: const ValueKey('p23f-s02-page-label'),
                          ),
                        ),
                        IconButton(
                          key: const ValueKey('p23f-s02-prev'),
                          tooltip: 'Previous page',
                          onPressed: currentPage.page > 1
                              ? () =>
                                    unawaited(_load(page: currentPage.page - 1))
                              : null,
                          icon: const Icon(Icons.chevron_left),
                        ),
                        IconButton(
                          key: const ValueKey('p23f-s02-next'),
                          tooltip: 'Next page',
                          onPressed: currentPage.hasNext
                              ? () =>
                                    unawaited(_load(page: currentPage.page + 1))
                              : null,
                          icon: const Icon(Icons.chevron_right),
                        ),
                      ],
                    ),
                  ],
                  if (_detailLoading)
                    const LinearProgressIndicator(
                      key: ValueKey('p23f-s02-detail-loading'),
                    ),
                  if (_detailError != null)
                    Text(
                      _detailError!,
                      key: const ValueKey('p23f-s02-detail-error'),
                    ),
                  if (_detail != null) ...[
                    const Divider(),
                    Text(
                      'Server detail: administration #${_detail!.id}',
                      key: const ValueKey('p23f-s02-detail'),
                    ),
                    Text(
                      'Status: ${_detail!.status.name} · '
                      'Record: ${_detail!.recordStatus.name} '
                      'v${_detail!.recordVersion}',
                    ),
                    Text(_dose(_detail!)),
                    Text('Scheduled: ${_date(_detail!.scheduledAt)}'),
                    Text('Administered: ${_date(_detail!.administeredAt)}'),
                    if (_detail!.terminalAt != null) ...[
                      Text('Outcome recorded: ${_date(_detail!.terminalAt)}',
                          key: const ValueKey('p23f-s05-history-time')),
                      Text('Clinical reason: ${_detail!.terminalReason ?? 'Unavailable'}',
                          key: const ValueKey('p23f-s05-history-reason')),
                    ],
                    if (widget.onVerifyRequested == null)
                      const Text(
                        'Barcode verification and administration actions are '
                        'not available in this read-only workspace.',
                      )
                    else ...[
                      const Text(
                        'This is a read-only schedule. Barcode verification '
                        'is separate and does NOT administer medication.',
                      ),
                      if (_detail!.isPending && widget.onTerminalRequested != null)
                        OutlinedButton.icon(
                          key: const ValueKey('p23f-s05-terminal-entry'),
                          onPressed: _available && !_loading && !_detailLoading
                              ? () => widget.onTerminalRequested?.call(_detail!)
                              : null,
                          icon: const Icon(Icons.assignment_outlined),
                          label: const Text('Document hold/refusal/missed dose'),
                        ),
                      if (_detail!.isPending)
                        OutlinedButton.icon(
                          key: const ValueKey('p23f-s03c-verify-entry'),
                          onPressed: _available && !_loading && !_detailLoading
                              ? () => widget.onVerifyRequested?.call(_detail!)
                              : null,
                          icon: const Icon(Icons.qr_code_scanner),
                          label: const Text('Review barcode verification'),
                        ),
                    ],
                  ],
                ],
              ),
            ),
    );
  }
}
