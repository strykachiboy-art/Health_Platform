import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/appointments/data/appointment_create_queue_state_read_model.dart';
import 'package:clinic_system_pro/features/pending_work/data/pending_work_source.dart';

/// P25: metadata-only view of the existing appointment-create operation queue.
/// No operation payloads, patient identifiers, tokens, or idempotency keys.
class PendingWorkCenterScreen extends StatefulWidget {
  const PendingWorkCenterScreen({super.key, required this.source});

  final PendingWorkAccess source;

  @override
  State<PendingWorkCenterScreen> createState() =>
      _PendingWorkCenterScreenState();
}

class _PendingWorkCenterScreenState extends State<PendingWorkCenterScreen> {
  int _generation = 0;
  int _page = 1;
  bool _loading = false;
  bool _acting = false;
  AppointmentCreateQueuePage? _result;
  String? _notice;

  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_onGuardChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        unawaited(_load());
      }
    });
  }

  @override
  void didUpdateWidget(covariant PendingWorkCenterScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source)) {
      oldWidget.source.guard.removeListener(_onGuardChanged);
      widget.source.guard.addListener(_onGuardChanged);
      _reset();
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          unawaited(_load());
        }
      });
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.guard.removeListener(_onGuardChanged);
    super.dispose();
  }

  void _reset() {
    _generation++;
    _page = 1;
    _loading = false;
    _acting = false;
    _result = null;
    _notice = null;
  }

  void _onGuardChanged() {
    if (!mounted) {
      return;
    }
    setState(_reset);
    if (widget.source.guard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  bool _valid(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.source.guard.canAccessProtectedRoutes &&
      widget.source.guard.revision == revision;

  Future<void> _load({int? page}) async {
    if (!widget.source.guard.canAccessProtectedRoutes || _acting) {
      return;
    }
    final revision = widget.source.guard.revision;
    final generation = ++_generation;
    final requested = page ?? _page;
    setState(() {
      _loading = true;
      _result = null;
      _notice = null;
    });
    final AppointmentCreateQueuePage value;
    try {
      value = await widget.source.loadPage(page: requested, pageSize: 20);
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _loading = false;
        _notice = 'Local work status is unavailable. Recheck your session.';
      });
      return;
    }
    if (!_valid(generation, revision)) {
      return;
    }
    setState(() {
      _page = requested;
      _loading = false;
      _result = value;
    });
  }

  Future<void> _act(
    AppointmentCreateQueueSummary item, {
    required bool discard,
  }) async {
    if (_acting || _loading || !widget.source.guard.canAccessProtectedRoutes) {
      return;
    }
    final approved = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(
          discard ? 'Request safe discard?' : 'Review server outcome?',
        ),
        content: Text(
          discard
              ? 'The backend must prove this request caused no effect before any local record is removed. An unknown outcome cannot be discarded.'
              : 'Check the existing server operation record. This does not submit another appointment.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('p25-confirm-action'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Continue'),
          ),
        ],
      ),
    );
    if (!mounted ||
        approved != true ||
        !widget.source.guard.canAccessProtectedRoutes) {
      return;
    }
    final revision = widget.source.guard.revision;
    final generation = ++_generation;
    setState(() {
      _acting = true;
      _notice = null;
    });
    PendingWorkActionResult outcome;
    try {
      outcome = discard
          ? await widget.source.discard(item.operationId)
          : await widget.source.reconcile(item.operationId);
    } on Object {
      outcome = const PendingWorkActionResult(
        PendingWorkActionOutcome.unconfirmed,
      );
    }
    if (!_valid(generation, revision)) {
      return;
    }
    setState(() {
      _acting = false;
      _result = null;
      _notice = switch (outcome.outcome) {
        PendingWorkActionOutcome.confirmed =>
          'Authoritative disposition recorded. Refresh local status.',
        PendingWorkActionOutcome.reviewRequired =>
          'Outcome still needs manual review. Do not create a duplicate.',
        PendingWorkActionOutcome.rejected => 'Discard or reconciliation not authorized. No local deletion is claimed.',
        PendingWorkActionOutcome.unconfirmed =>
          'Action outcome unconfirmed. No repeat is allowed in this session.',
        PendingWorkActionOutcome.unavailable =>
          'Action unavailable. Check your session and local status.',
      };
    });
  }

  static String _label(AppointmentCreateQueueDisplayState state) =>
      switch (state) {
        AppointmentCreateQueueDisplayState.pending => 'Queued locally',
        AppointmentCreateQueueDisplayState.sending =>
          'Sending / outcome pending',
        AppointmentCreateQueueDisplayState.retryScheduled => 'Retry scheduled',
        AppointmentCreateQueueDisplayState.reconciliationRequired =>
          'Reconciliation required',
        AppointmentCreateQueueDisplayState.conflictReview =>
          'Conflict needs review',
        AppointmentCreateQueueDisplayState.acknowledged =>
          'Server acknowledgement recorded',
        AppointmentCreateQueueDisplayState.rejected => 'Rejected',
        AppointmentCreateQueueDisplayState.userInterventionRequired =>
          'User review required',
        AppointmentCreateQueueDisplayState.cancelled => 'Cancelled locally',
      };

  static bool _canReconcile(AppointmentCreateQueueDisplayState state) =>
      state == AppointmentCreateQueueDisplayState.reconciliationRequired ||
      state == AppointmentCreateQueueDisplayState.conflictReview;

  static bool _canRequestDiscard(AppointmentCreateQueueDisplayState state) =>
      state == AppointmentCreateQueueDisplayState.pending ||
      state == AppointmentCreateQueueDisplayState.retryScheduled ||
      state == AppointmentCreateQueueDisplayState.reconciliationRequired ||
      state == AppointmentCreateQueueDisplayState.userInterventionRequired ||
      state == AppointmentCreateQueueDisplayState.conflictReview;

  @override
  Widget build(BuildContext context) {
    final allowed = widget.source.guard.canAccessProtectedRoutes;
    final result = allowed ? _result : null;
    return Scaffold(
      key: const ValueKey('p25-pending-work-center'),
      appBar: AppBar(title: const Text('Pending Work')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text(
              'Local appointment-create operations only. A queued or acknowledged request is not independent confirmation of a clinical appointment.',
            ),
            const SizedBox(height: 8),
            const Text(
              'Medication, emergency, billing, chat and feedback actions remain outside automatic offline replay.',
              key: ValueKey('p25-no-broad-replay'),
            ),
            if (!allowed)
              const Text(
                'Pending work is hidden until a valid clinic session is restored.',
                key: ValueKey('p25-locked'),
              )
            else ...[
              OutlinedButton.icon(
                key: const ValueKey('p25-refresh'),
                onPressed: _loading || _acting ? null : () => _load(),
                icon: const Icon(Icons.refresh),
                label: const Text('Refresh local work status'),
              ),
              if (_loading || _acting)
                const LinearProgressIndicator(key: ValueKey('p25-loading')),
              if (_notice != null)
                Text(_notice!, key: const ValueKey('p25-notice')),
              if (!_loading && result != null && !result.available)
                const Text(
                  'Work status is unavailable for this session.',
                  key: ValueKey('p25-unavailable'),
                ),
              if (result != null && result.available) ...[
                if (result.items.isEmpty)
                  const Text(
                    'No local appointment-create work on this page.',
                    key: ValueKey('p25-empty'),
                  ),
                for (var index = 0; index < result.items.length; index++)
                  Card(
                    key: ValueKey('p25-work-$index'),
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(_label(result.items[index].state)),
                          Text(
                            'Attempts: ${result.items[index].attemptsCompleted}',
                          ),
                          Text(
                            'Created: ${result.items[index].createdAt.toLocal()}',
                          ),
                          if (result.items[index].nextAttemptAt != null)
                            Text(
                              'Scheduled: ${result.items[index].nextAttemptAt!.toLocal()}',
                            ),
                          if (DateTime.now().toUtc().difference(
                                result.items[index].createdAt.toUtc(),
                              ) >
                              const Duration(days: 7))
                            const Text('Aged request — review before acting.'),
                          Wrap(
                            spacing: 8,
                            children: [
                              if (_canReconcile(result.items[index].state))
                                TextButton(
                                  key: ValueKey('p25-reconcile-$index'),
                                  onPressed: _acting
                                      ? null
                                      : () => _act(
                                          result.items[index],
                                          discard: false,
                                        ),
                                  child: const Text('Reconcile with server'),
                                ),
                              if (_canRequestDiscard(result.items[index].state))
                                TextButton(
                                  key: ValueKey('p25-discard-$index'),
                                  onPressed: _acting
                                      ? null
                                      : () => _act(
                                          result.items[index],
                                          discard: true,
                                        ),
                                  child: const Text('Request safe discard'),
                                ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
                Row(
                  children: [
                    TextButton(
                      key: const ValueKey('p25-previous'),
                      onPressed: _acting || _loading || _page <= 1
                          ? null
                          : () => _load(page: _page - 1),
                      child: const Text('Previous'),
                    ),
                    Text('Page $_page'),
                    TextButton(
                      key: const ValueKey('p25-next'),
                      onPressed: _acting || _loading || !result.hasNext
                          ? null
                          : () => _load(page: _page + 1),
                      child: const Text('Next'),
                    ),
                  ],
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }
}
