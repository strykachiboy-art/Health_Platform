import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/nursing/data/models/ward_overview.dart';
import 'package:clinic_system_pro/features/nursing/data/ward_dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/nursing/data/ward_admission_workflow_remote_data_source.dart';
import 'package:clinic_system_pro/features/nursing/presentation/ward_admissions_transfers_screen.dart';

/// P23E.B: online, read-only ward and bed status from the authoritative API.
/// No automatic retries, PHI cache, offline writes, or local bed decisions.
class WardDashboardScreen extends StatefulWidget {
  const WardDashboardScreen({super.key, required this.dataSource});

  final WardDashboardRemoteDataSource dataSource;

  @override
  State<WardDashboardScreen> createState() => _WardDashboardScreenState();
}

class _WardDashboardScreenState extends State<WardDashboardScreen> {
  late WardAdmissionWorkflowRemoteDataSource _clinicalSource;
  WardPage<WardRecord>? _wards;
  WardRecord? _selected;
  WardOccupancy? _occupancy;
  WardPage<WardBedRecord>? _beds;
  String? _wardError;
  String? _detailError;
  bool _loadingWards = false;
  bool _loadingDetail = false;
  int _wardsGeneration = 0;
  int _detailsGeneration = 0;

  @override
  void initState() {
    super.initState();
    _clinicalSource = WardAdmissionWorkflowRemoteDataSource(
      apiClient: widget.dataSource.apiClient,
      loadSession: widget.dataSource.loadSession,
      sessionNavigationGuard: widget.dataSource.sessionNavigationGuard,
    );
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
    unawaited(_loadWards());
  }

  @override
  void didUpdateWidget(covariant WardDashboardScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.dataSource, widget.dataSource)) {
      _clinicalSource = WardAdmissionWorkflowRemoteDataSource(
        apiClient: widget.dataSource.apiClient,
        loadSession: widget.dataSource.loadSession,
        sessionNavigationGuard: widget.dataSource.sessionNavigationGuard,
      );
      oldWidget.dataSource.sessionNavigationGuard.removeListener(
        _onSessionChanged,
      );
      widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
      _clear();
      unawaited(_loadWards());
    }
  }

  @override
  void dispose() {
    _wardsGeneration++;
    _detailsGeneration++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onSessionChanged);
    super.dispose();
  }

  void _clear() {
    _wardsGeneration++;
    _detailsGeneration++;
    _wards = null;
    _selected = null;
    _occupancy = null;
    _beds = null;
    _wardError = null;
    _detailError = null;
    _loadingWards = false;
    _loadingDetail = false;
  }

  void _onSessionChanged() {
    if (!mounted) return;
    setState(_clear);
    if (widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_loadWards());
    }
  }

  bool _valid(int generation, int revision, {required bool ward}) =>
      mounted &&
      widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.dataSource.sessionNavigationGuard.revision == revision &&
      (ward
          ? _wardsGeneration == generation
          : _detailsGeneration == generation);

  Future<void> _loadWards({int page = 1}) async {
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_wardsGeneration;
    _detailsGeneration++;
    if (!mounted) return;
    setState(() {
      _wards = null;
      _selected = null;
      _occupancy = null;
      _beds = null;
      _wardError = null;
      _detailError = null;
      _loadingDetail = false;
      _loadingWards = true;
    });
    if (!guard.canAccessProtectedRoutes) {
      if (mounted && _wardsGeneration == generation) {
        setState(() => _loadingWards = false);
      }
      return;
    }
    try {
      final result = await widget.dataSource.fetchWards(page: page);
      if (!_valid(generation, revision, ward: true)) return;
      setState(() {
        _wards = result;
        _loadingWards = false;
      });
      if (result.items.isNotEmpty) {
        await _selectWard(result.items.first);
      }
    } on Object {
      if (!_valid(generation, revision, ward: true)) return;
      setState(() {
        _loadingWards = false;
        _wardError = 'Ward data unavailable. Refresh to try again.';
      });
    }
  }

  Future<void> _selectWard(WardRecord ward, {int bedPage = 1}) async {
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_detailsGeneration;
    if (!mounted || !guard.canAccessProtectedRoutes) return;
    setState(() {
      _selected = ward;
      _occupancy = null;
      _beds = null;
      _detailError = null;
      _loadingDetail = true;
    });
    try {
      final occupancy = await widget.dataSource.fetchOccupancy(wardId: ward.id);
      if (!_valid(generation, revision, ward: false)) return;
      if (occupancy.wardName != ward.name ||
          occupancy.capacity != ward.capacity) {
        throw const FormatException('Ward readback disagrees with selection.');
      }
      final beds = await widget.dataSource.fetchBeds(
        wardId: ward.id,
        page: bedPage,
      );
      if (!_valid(generation, revision, ward: false)) return;
      setState(() {
        _occupancy = occupancy;
        _beds = beds;
        _loadingDetail = false;
      });
    } on Object {
      if (!_valid(generation, revision, ward: false)) return;
      setState(() {
        _loadingDetail = false;
        _detailError = 'Bed status unavailable. Refresh to try again.';
      });
    }
  }

  String _kind(WardKind value) => switch (value) {
    WardKind.general => 'General',
    WardKind.icu => 'ICU',
    WardKind.maternity => 'Maternity',
    WardKind.pediatric => 'Pediatric',
    WardKind.isolation => 'Isolation',
  };

  String _bedLabel(WardBedStatus value) => switch (value) {
    WardBedStatus.available => 'Available',
    WardBedStatus.occupied => 'Occupied',
    WardBedStatus.reserved => 'Reserved',
    WardBedStatus.maintenance => 'Maintenance',
  };

  @override
  Widget build(BuildContext context) {
    final guard = widget.dataSource.sessionNavigationGuard;
    final active = guard.canAccessProtectedRoutes;
    final wards = _wards;
    final occupancy = _occupancy;
    final beds = _beds;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Ward dashboard'),
        actions: [
          IconButton(
            key: const ValueKey('p23e-b-refresh'),
            tooltip: 'Refresh wards',
            icon: const Icon(Icons.refresh_outlined),
            onPressed: active && !_loadingWards
                ? () => _loadWards(page: wards?.page ?? 1)
                : null,
          ),
        ],
      ),
      body: !active
          ? const Center(
              key: ValueKey('p23e-b-unavailable'),
              child: Text('Ward session unavailable.'),
            )
          : ListView(
              key: const ValueKey('p23e-b-ward-list'),
              padding: const EdgeInsets.all(16),
              children: [
                const Text('Online ward and bed status · read only'),
                ListTile(
                  key: const ValueKey('p23e-c-open'),
                  leading: const Icon(Icons.move_up_outlined),
                  title: const Text('Admissions & transfers'),
                  subtitle: const Text(
                    'Online clinical actions · confirmation required',
                  ),
                  onTap: () => Navigator.of(context).push<void>(
                    MaterialPageRoute<void>(
                      builder: (_) => WardAdmissionsTransfersScreen(
                        dataSource: _clinicalSource,
                      ),
                    ),
                  ),
                ),
                if (_loadingWards)
                  const LinearProgressIndicator(
                    key: ValueKey('p23e-b-loading-wards'),
                  ),
                if (_wardError != null)
                  Text(_wardError!, key: const ValueKey('p23e-b-ward-error')),
                if (!_loadingWards && wards != null && wards.items.isEmpty)
                  const Text('No wards returned for this clinic.'),
                if (wards != null) ...[
                  for (final ward in wards.items)
                    ListTile(
                      key: ValueKey('p23e-b-ward-${ward.id}'),
                      title: Text(ward.name),
                      subtitle: Text(
                        '${_kind(ward.kind)} · Capacity ${ward.capacity}',
                      ),
                      selected: _selected?.id == ward.id,
                      onTap: () => _selectWard(ward),
                    ),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text('Wards: page ${wards.page} · total ${wards.total}'),
                      Row(
                        children: [
                          IconButton(
                            key: const ValueKey('p23e-b-wards-prev'),
                            tooltip: 'Previous wards',
                            onPressed: wards.hasPrevious
                                ? () => _loadWards(page: wards.page - 1)
                                : null,
                            icon: const Icon(Icons.chevron_left),
                          ),
                          IconButton(
                            key: const ValueKey('p23e-b-wards-next'),
                            tooltip: 'Next wards',
                            onPressed: wards.hasNext
                                ? () => _loadWards(page: wards.page + 1)
                                : null,
                            icon: const Icon(Icons.chevron_right),
                          ),
                        ],
                      ),
                    ],
                  ),
                ],
                if (_selected != null) ...[
                  const Divider(),
                  Text(
                    'Selected: ${_selected!.name}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  if (_loadingDetail)
                    const LinearProgressIndicator(
                      key: ValueKey('p23e-b-loading-beds'),
                    ),
                  if (_detailError != null) ...[
                    Text(
                      _detailError!,
                      key: const ValueKey('p23e-b-bed-error'),
                    ),
                    TextButton(
                      key: const ValueKey('p23e-b-retry-beds'),
                      onPressed: () => _selectWard(_selected!),
                      child: const Text('Retry bed read'),
                    ),
                  ],
                  if (occupancy != null) ...[
                    Text('Beds configured: ${occupancy.totalBeds}'),
                    Text('Available: ${occupancy.available}'),
                    Text('Occupied: ${occupancy.occupied}'),
                    Text('Reserved: ${occupancy.reserved}'),
                    Text('Maintenance: ${occupancy.maintenance}'),
                  ],
                  if (beds != null) ...[
                    if (beds.items.isEmpty) const Text('No beds on this page.'),
                    for (final bed in beds.items)
                      ListTile(
                        key: ValueKey('p23e-b-bed-${bed.id}'),
                        title: Text('Bed ${bed.number}'),
                        subtitle: Text(_bedLabel(bed.status)),
                      ),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text('Beds: page ${beds.page} · total ${beds.total}'),
                        Row(
                          children: [
                            IconButton(
                              key: const ValueKey('p23e-b-beds-prev'),
                              tooltip: 'Previous beds',
                              onPressed: beds.hasPrevious
                                  ? () => _selectWard(
                                      _selected!,
                                      bedPage: beds.page - 1,
                                    )
                                  : null,
                              icon: const Icon(Icons.chevron_left),
                            ),
                            IconButton(
                              key: const ValueKey('p23e-b-beds-next'),
                              tooltip: 'Next beds',
                              onPressed: beds.hasNext
                                  ? () => _selectWard(
                                      _selected!,
                                      bedPage: beds.page + 1,
                                    )
                                  : null,
                              icon: const Icon(Icons.chevron_right),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ],
                ],
              ],
            ),
    );
  }
}
