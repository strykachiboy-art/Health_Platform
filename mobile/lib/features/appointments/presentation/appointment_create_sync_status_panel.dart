import 'dart:async';

import 'package:flutter/material.dart';

import '../../../core/session/session_navigation_guard.dart';
import '../data/appointment_create_queue_state_read_model.dart';

/// Inject the S05-B read-model's loadPage tear-off; never an operation sender.
/// The caller must bind its session loader to the same navigation guard revision.
typedef AppointmentCreateSyncPageLoader =
    Future<AppointmentCreateQueuePage> Function({
      required int page,
      required int pageSize,
    });

/// Metadata-only local create-request status. No replay or mutation controls.
/// This panel intentionally does not expose operation IDs, idempotency keys,
/// patient identifiers, encryption material, queued payloads or raw failures.
class AppointmentCreateSyncStatusPanel extends StatefulWidget {
  const AppointmentCreateSyncStatusPanel({
    super.key,
    required this.sessionNavigationGuard,
    required this.loadPage,
  });

  final SessionNavigationGuard sessionNavigationGuard;
  final AppointmentCreateSyncPageLoader loadPage;

  @override
  State<AppointmentCreateSyncStatusPanel> createState() =>
      _AppointmentCreateSyncStatusPanelState();
}

class _AppointmentCreateSyncStatusPanelState
    extends State<AppointmentCreateSyncStatusPanel> {
  static const int _pageSize = 20;

  int _generation = 0;
  int _pageNumber = 1;
  bool _loading = false;
  AppointmentCreateQueuePage? _result;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard.addListener(_onGuardChanged);
    _startLoad();
  }

  @override
  void didUpdateWidget(covariant AppointmentCreateSyncStatusPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
          oldWidget.sessionNavigationGuard,
          widget.sessionNavigationGuard,
        ) ||
        oldWidget.loadPage != widget.loadPage) {
      oldWidget.sessionNavigationGuard.removeListener(_onGuardChanged);
      widget.sessionNavigationGuard.addListener(_onGuardChanged);
      _pageNumber = 1;
      _startLoad();
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.sessionNavigationGuard.removeListener(_onGuardChanged);
    super.dispose();
  }

  void _onGuardChanged() {
    _pageNumber = 1;
    setState(_startLoad);
  }

  void _startLoad() {
    final generation = ++_generation;
    final guard = widget.sessionNavigationGuard;
    final revision = guard.revision;
    final pageNumber = _pageNumber;
    _result = null;
    _loading = guard.canAccessProtectedRoutes;
    if (!_loading) return;
    unawaited(_load(generation, revision, pageNumber));
  }

  Future<void> _load(int generation, int revision, int pageNumber) async {
    AppointmentCreateQueuePage next;
    try {
      next = await widget.loadPage(page: pageNumber, pageSize: _pageSize);
    } catch (_) {
      next = AppointmentCreateQueuePage.unavailable(page: pageNumber);
    }

    if (!mounted ||
        generation != _generation ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes ||
        widget.sessionNavigationGuard.revision != revision) {
      return;
    }
    setState(() {
      _loading = false;
      _result = next;
    });
  }

  void _refresh({int? page}) {
    if (!widget.sessionNavigationGuard.canAccessProtectedRoutes) return;
    setState(() {
      if (page != null) _pageNumber = page;
      _startLoad();
    });
  }

  @override
  Widget build(BuildContext context) {
    final guard = widget.sessionNavigationGuard;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const Text(
              'Appointment create sync',
              style: TextStyle(fontWeight: FontWeight.bold),
            ),
            const Text('Local request status only. Not booking confirmation.'),
            if (!guard.canAccessProtectedRoutes)
              const Text(
                'Sign in and select a clinic to view local sync status.',
                key: ValueKey('s05c-session-denied'),
              )
            else if (_loading)
              const LinearProgressIndicator(key: ValueKey('s05c-loading'))
            else if (_result == null || !_result!.available)
              const Text(
                'Sync status is unavailable. Recheck your session and retry.',
                key: ValueKey('s05c-unavailable'),
              )
            else ...<Widget>[
              Text('Page $_pageNumber', key: const ValueKey('s05c-page')),
              if (_result!.items.isEmpty)
                const Text(
                  'No local appointment create requests on this page.',
                  key: ValueKey('s05c-empty'),
                ),
              for (var index = 0; index < _result!.items.length; index++)
                ListTile(
                  key: ValueKey('s05c-entry-$index'),
                  contentPadding: EdgeInsets.zero,
                  title: Text(_label(_result!.items[index].state)),
                  subtitle: Text(
                    'Attempt count: ${_result!.items[index].attemptsCompleted}',
                  ),
                ),
              Row(
                children: <Widget>[
                  TextButton(
                    key: const ValueKey('s05c-previous'),
                    onPressed: _pageNumber > 1
                        ? () => _refresh(page: _pageNumber - 1)
                        : null,
                    child: const Text('Previous'),
                  ),
                  TextButton(
                    key: const ValueKey('s05c-next'),
                    onPressed: _result!.hasNext
                        ? () => _refresh(page: _pageNumber + 1)
                        : null,
                    child: const Text('Next'),
                  ),
                ],
              ),
            ],
            if (guard.canAccessProtectedRoutes)
              TextButton(
                key: const ValueKey('s05c-refresh'),
                onPressed: _loading ? null : _refresh,
                child: const Text('Refresh local status'),
              ),
          ],
        ),
      ),
    );
  }

  static String _label(AppointmentCreateQueueDisplayState state) {
    return switch (state) {
      AppointmentCreateQueueDisplayState.pending =>
        'Queued locally — not yet confirmed',
      AppointmentCreateQueueDisplayState.sending =>
        'Sending — outcome not yet confirmed',
      AppointmentCreateQueueDisplayState.retryScheduled =>
        'Retry scheduled for verified create request',
      AppointmentCreateQueueDisplayState.reconciliationRequired =>
        'Outcome uncertain — reconciliation required',
      AppointmentCreateQueueDisplayState.conflictReview =>
        'Conflict requires review — do not submit a duplicate',
      AppointmentCreateQueueDisplayState.acknowledged =>
        'Server acknowledgement recorded — check appointment list',
      AppointmentCreateQueueDisplayState.rejected =>
        'Request rejected — no booking confirmed',
      AppointmentCreateQueueDisplayState.userInterventionRequired =>
        'User intervention required — review before proceeding',
      AppointmentCreateQueueDisplayState.cancelled =>
        'Local create request cancelled',
    };
  }
}
