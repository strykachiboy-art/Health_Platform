import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/dashboard/data/models/dashboard_snapshot.dart';

/// GET-only, in-memory dashboard view. No KPI is shown until a fresh,
/// identity-checked server snapshot is available for the current session.
class DashboardOverviewPanel extends StatefulWidget {
  const DashboardOverviewPanel({
    super.key,
    required this.dataSource,
    required this.expectedRole,
    required this.expectedClinicId,
    this.sessionNavigationGuard,
  });

  final DashboardRemoteDataSource dataSource;
  final String expectedRole;
  final int? expectedClinicId;
  final SessionNavigationGuard? sessionNavigationGuard;

  @override
  State<DashboardOverviewPanel> createState() => _DashboardOverviewPanelState();
}

class _DashboardOverviewPanelState extends State<DashboardOverviewPanel> {
  DashboardSnapshot? _snapshot;
  String? _failure;
  bool _loading = true;
  int _requestGeneration = 0;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard?.addListener(_onSessionChanged);
    unawaited(_refresh());
  }

  @override
  void didUpdateWidget(covariant DashboardOverviewPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
      oldWidget.sessionNavigationGuard,
      widget.sessionNavigationGuard,
    )) {
      oldWidget.sessionNavigationGuard?.removeListener(_onSessionChanged);
      widget.sessionNavigationGuard?.addListener(_onSessionChanged);
    }
    if (!identical(oldWidget.dataSource, widget.dataSource) ||
        oldWidget.expectedRole != widget.expectedRole ||
        oldWidget.expectedClinicId != widget.expectedClinicId ||
        !identical(
          oldWidget.sessionNavigationGuard,
          widget.sessionNavigationGuard,
        )) {
      unawaited(_refresh());
    }
  }

  @override
  void dispose() {
    widget.sessionNavigationGuard?.removeListener(_onSessionChanged);
    _requestGeneration++;
    _snapshot = null;
    super.dispose();
  }

  void _onSessionChanged() {
    // Clear visible values synchronously. A pending old GET must never win.
    _requestGeneration++;
    if (!mounted) return;
    if (widget.sessionNavigationGuard?.canAccessProtectedRoutes == true) {
      unawaited(_refresh());
    } else {
      setState(() {
        _snapshot = null;
        _loading = false;
        _failure = 'Session changed. Dashboard data was cleared.';
      });
    }
  }

  bool _isCurrent(int request, int? guardRevision) {
    final guard = widget.sessionNavigationGuard;
    return mounted &&
        request == _requestGeneration &&
        (guard == null ||
            (guard.canAccessProtectedRoutes &&
                guard.revision == guardRevision));
  }

  Future<void> _refresh() async {
    final request = ++_requestGeneration;
    final guard = widget.sessionNavigationGuard;
    final guardRevision = guard?.revision;
    if (!mounted) return;
    setState(() {
      _loading = true;
      _failure = null;
      _snapshot = null;
    });
    if (guard != null && !guard.canAccessProtectedRoutes) {
      if (mounted && request == _requestGeneration) {
        setState(() {
          _loading = false;
          _failure = 'Session unavailable. Dashboard data cannot be shown.';
        });
      }
      return;
    }

    try {
      final result = await widget.dataSource.fetchDashboard();
      if (!_isCurrent(request, guardRevision)) return;
      // The HTTP adapter enforces session identity. Keep a second UI-boundary
      // check so an obsolete widget configuration never paints another role.
      if (result.context.role.toLowerCase() !=
              widget.expectedRole.trim().toLowerCase() ||
          (result.context.family != DashboardFamily.system &&
              widget.expectedClinicId != null &&
              result.context.clinicId != widget.expectedClinicId)) {
        throw const DashboardSessionMismatchException();
      }
      setState(() {
        _loading = false;
        _snapshot = result;
      });
    } on DashboardSessionMismatchException {
      _reject(
        request,
        guardRevision,
        'Session or clinic changed. Dashboard data was discarded.',
      );
    } on ApiException catch (error) {
      final message = switch (error.kind) {
        ApiFailureKind.timeout =>
          'Dashboard timed out. No current metrics are available.',
        ApiFailureKind.connection =>
          'Dashboard is offline. No current metrics are available.',
        ApiFailureKind.invalidResponse =>
          'Dashboard response was invalid. No metrics are shown.',
        ApiFailureKind.httpStatus when error.statusCode == 401 =>
          'Session expired. Dashboard data was cleared.',
        ApiFailureKind.httpStatus when error.statusCode == 403 =>
          'You are not authorized to view this dashboard.',
        _ => 'Dashboard is unavailable. No current metrics are shown.',
      };
      _reject(request, guardRevision, message);
    } on FormatException {
      _reject(
        request,
        guardRevision,
        'Dashboard response was invalid. No metrics are shown.',
      );
    } on Object {
      _reject(
        request,
        guardRevision,
        'Dashboard is unavailable. No current metrics are shown.',
      );
    }
  }

  void _reject(int request, int? guardRevision, String message) {
    if (!_isCurrent(request, guardRevision)) return;
    setState(() {
      _loading = false;
      _snapshot = null;
      _failure = message;
    });
  }

  @override
  Widget build(BuildContext context) {
    final snapshot = _snapshot;
    return Card(
      key: const ValueKey('p22-dashboard-panel'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Live dashboard',
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                ),
                IconButton(
                  key: const ValueKey('p22-dashboard-refresh'),
                  tooltip: 'Refresh dashboard',
                  onPressed: _loading ? null : () => unawaited(_refresh()),
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            if (_loading)
              const Padding(
                padding: EdgeInsets.all(12),
                child: Center(
                  child: CircularProgressIndicator(
                    key: ValueKey('p22-dashboard-loading'),
                  ),
                ),
              )
            else if (_failure != null)
              Text(_failure!, key: const ValueKey('p22-dashboard-unavailable'))
            else if (snapshot != null) ...[
              Text(
                'Reporting period: '
                '${snapshot.period.from.toIso8601String().substring(0, 10)} – '
                '${snapshot.period.to.toIso8601String().substring(0, 10)}',
                key: const ValueKey('p22-dashboard-period'),
              ),
              const SizedBox(height: 12),
              if (snapshot.metrics.isEmpty)
                const Text(
                  'The backend returned no dashboard metrics.',
                  key: ValueKey('p22-dashboard-empty'),
                )
              else
                LayoutBuilder(
                  builder: (context, constraints) {
                    final tileWidth = constraints.maxWidth < 480
                        ? constraints.maxWidth
                        : 220.0;
                    return Wrap(
                      spacing: 12,
                      runSpacing: 12,
                      children: [
                        for (final metric in snapshot.metrics)
                          SizedBox(
                            width: tileWidth,
                            child: Card(
                              child: Padding(
                                padding: const EdgeInsets.all(12),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      metric.label,
                                      style: Theme.of(context)
                                          .textTheme
                                          .bodyMedium,
                                    ),
                                    const SizedBox(height: 4),
                                    Text(
                                      metric.unit == null
                                          ? metric.value.text
                                          : '${metric.value.text} ${metric.unit}',
                                      key: ValueKey(
                                        'p22-dashboard-metric-${metric.key}',
                                      ),
                                      style: Theme.of(context)
                                          .textTheme
                                          .titleMedium,
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ),
                      ],
                    );
                  },
                ),
              const SizedBox(height: 8),
              const Text(
                'Snapshot only. Access and clinical decisions remain server-controlled.',
                key: ValueKey('p22-dashboard-authority'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
