import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import 'package:clinic_system_pro/core/networking/device_connectivity_cubit.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/status/data/scoped_sync_status_source.dart';

/// Authenticated status: network transport is NOT backend reachability, and
/// local sync counts are NOT evidence that the backend saved an operation.
/// Nothing sensitive remains displayed after a session guard transition.
class AuthenticatedStatusStrip extends StatefulWidget {
  const AuthenticatedStatusStrip({
    super.key,
    required this.loadSession,
    required this.loadSyncCounts,
    this.sessionNavigationGuard,
  });

  final Future<AuthSessionState> Function() loadSession;
  final ScopedSyncCountsLoader loadSyncCounts;
  final SessionNavigationGuard? sessionNavigationGuard;

  @override
  State<AuthenticatedStatusStrip> createState() =>
      _AuthenticatedStatusStripState();
}

class _AuthenticatedStatusStripState extends State<AuthenticatedStatusStrip>
    with WidgetsBindingObserver {
  int _generation = 0;
  bool _checking = true;
  ScopedSyncCounts? _counts;
  String _syncStatus = 'Checking';
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    widget.sessionNavigationGuard?.addListener(_onSessionTransition);
    _refreshTimer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => unawaited(_refresh()),
    );
    unawaited(_refresh());
  }

  @override
  void didUpdateWidget(covariant AuthenticatedStatusStrip oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
      oldWidget.sessionNavigationGuard,
      widget.sessionNavigationGuard,
    )) {
      oldWidget.sessionNavigationGuard?.removeListener(_onSessionTransition);
      widget.sessionNavigationGuard?.addListener(_onSessionTransition);
    }
    if (!identical(
          oldWidget.sessionNavigationGuard,
          widget.sessionNavigationGuard,
        ) ||
        !identical(oldWidget.loadSession, widget.loadSession) ||
        !identical(oldWidget.loadSyncCounts, widget.loadSyncCounts)) {
      unawaited(_refresh());
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _refreshTimer?.cancel();
    widget.sessionNavigationGuard?.removeListener(_onSessionTransition);
    _generation++;
    _counts = null;
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      unawaited(_refresh());
    }
  }

  void _onSessionTransition() {
    _generation++;
    if (!mounted) return;
    // Clear the old user's count synchronously, before any storage reads.
    setState(() {
      _counts = null;
      _checking = false;
      _syncStatus = 'Unavailable';
    });
    if (widget.sessionNavigationGuard?.canAccessProtectedRoutes == true) {
      unawaited(_refresh());
    }
  }

  bool _stillCurrent(int generation, int? revision) {
    final guard = widget.sessionNavigationGuard;
    return mounted &&
        generation == _generation &&
        (guard == null ||
            (guard.canAccessProtectedRoutes && guard.revision == revision));
  }

  Future<void> _refresh() async {
    final generation = ++_generation;
    final guard = widget.sessionNavigationGuard;
    final revision = guard?.revision;
    if (!mounted) return;
    setState(() {
      _counts = null;
      _checking = true;
      _syncStatus = 'Checking';
    });
    if (guard != null && !guard.canAccessProtectedRoutes) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _checking = false;
        _syncStatus = 'Unavailable';
      });
      return;
    }

    try {
      final before = await widget.loadSession();
      if (!_stillCurrent(generation, revision)) return;
      final session = before.session;
      if (before.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0) {
        _finish(generation, revision, 'Unavailable');
        return;
      }
      final clinicId = session.clinicContextId;
      if (clinicId == null || clinicId <= 0) {
        _finish(generation, revision, 'Clinic context required');
        return;
      }
      final counts = await widget.loadSyncCounts(
        userId: session.userId,
        clinicContextId: clinicId,
      );
      if (!_stillCurrent(generation, revision)) return;
      // The guard revision alone does not detect all secure-store changes.
      // Revalidate the complete session stamp before exposing the count.
      final after = await widget.loadSession();
      if (!_stillCurrent(generation, revision)) return;
      final current = after.session;
      if (after.disposition != AuthSessionDisposition.ready ||
          current == null ||
          current.userId != session.userId ||
          current.clinicContextId != clinicId ||
          current.accessToken != session.accessToken ||
          current.refreshToken != session.refreshToken ||
          current.role != session.role) {
        _finish(generation, revision, 'Unavailable');
        return;
      }
      setState(() {
        _counts = counts;
        _checking = false;
        _syncStatus = 'Current';
      });
    } on Object {
      _finish(generation, revision, 'Unknown');
    }
  }

  void _finish(int generation, int? revision, String status) {
    if (!_stillCurrent(generation, revision)) return;
    setState(() {
      _counts = null;
      _checking = false;
      _syncStatus = status;
    });
  }

  @override
  Widget build(BuildContext context) {
    final counts = _counts;
    final syncText = counts == null
        ? 'Sync: $_syncStatus'
        : 'Sync: ${counts.pending} pending, ${counts.needsReview} review';
    return Material(
      key: const ValueKey('p22-status-strip'),
      color: Theme.of(context).colorScheme.surfaceContainerLow,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
        child: Wrap(
          spacing: 12,
          runSpacing: 4,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            BlocBuilder<DeviceConnectivityCubit, DeviceConnectivityViewState>(
              builder: (context, network) {
                final label = switch (network) {
                  DeviceConnectivityViewState.checking => 'Checking',
                  DeviceConnectivityViewState.connected => 'Connected',
                  DeviceConnectivityViewState.disconnected => 'Offline',
                  DeviceConnectivityViewState.unknown => 'Unknown',
                };
                return Text(
                  'Device network: $label',
                  key: const ValueKey('p22-status-connectivity'),
                );
              },
            ),
            Text(syncText, key: const ValueKey('p22-status-sync')),
            IconButton(
              key: const ValueKey('p22-status-sync-refresh'),
              tooltip: 'Refresh local sync status',
              onPressed: _checking ? null : () => unawaited(_refresh()),
              icon: const Icon(Icons.refresh, size: 20),
              visualDensity: VisualDensity.compact,
            ),
            const Text(
              'Network signal is not server health',
              key: ValueKey('p22-status-transport-caveat'),
              style: TextStyle(fontSize: 11),
            ),
          ],
        ),
      ),
    );
  }
}
