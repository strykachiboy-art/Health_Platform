import '../networking/api_exception.dart';
import '../session/session_navigation_guard.dart';
import '../storage/app_database.dart';
import '../../features/auth/data/models/auth_token_response.dart';
import 'operation_lifecycle.dart';
import 'operation_payload_crypto.dart';
import 'operation_queue_store.dart';
import 'operation_replay_worker.dart';
import 'run2_policy.dart';
import 'run2_transport.dart';
import 'run2_module_catalog.dart';
import 'run2_module_transport.dart';

part 'run2_module_workspace.dart';

class Run2WorkspaceUnavailable implements Exception {
  const Run2WorkspaceUnavailable();
}

class Run2WorkItem {
  const Run2WorkItem({
    required this.id,
    required this.type,
    required this.state,
    required this.capturedAt,
    required this.description,
  });
  final String id;
  final String type;
  final String state;
  final DateTime capturedAt;
  final String description;
  bool get localOnly => Run2Policy.localTypes.contains(type);
}

class Run2View {
  const Run2View({required this.role, required this.items});
  final String role;
  final List<Run2WorkItem> items;
}

class _Stamp {
  const _Stamp(this.session, this.context, this.revision);
  final AuthTokenResponse session;
  final OperationPayloadContext context;
  final int revision;
  String get role => Run2Policy.role(session);
}

class _Lease {
  const _Lease(this.stamp, this.start, this.end, this.elapsedStart);
  final _Stamp stamp;
  final DateTime start;
  final DateTime end;
  final Duration elapsedStart;
}

/// A typed adapter over the EXISTING Drift queue, crypto, retry lifecycle and
/// scheduler. No arbitrary payload writer, parallel queue, background timer or
/// network retry policy is exposed. Local-only rows can never reach delivery.
class Run2Workspace {
  Run2Workspace({
    required this.store,
    required this.guard,
    required this.loadSession,
    required this.transport,
    required this.notifyQueued,
    this.moduleTransport,
    this.managedRecords = Run2ModuleCatalog.managedPolicyEnabled,
    DateTime Function()? clock,
    Duration Function()? elapsed,
  }) : _clock = clock ?? DateTime.now,
       _elapsed = elapsed ?? _startElapsed();

  static Duration Function() _startElapsed() {
    final watch = Stopwatch()..start();
    return () => watch.elapsed;
  }

  final OperationQueueStore store;
  final SessionNavigationGuard guard;
  final Future<AuthTokenResponse?> Function() loadSession;
  final Run2Transport transport;
  final Run2ModuleTransport? moduleTransport;
  final bool managedRecords;
  final Map<String, _ModuleTarget> _moduleTargets = {};
  final Set<String> _visibleModuleItems = {};
  final void Function() notifyQueued;
  final DateTime Function() _clock;
  final Duration Function() _elapsed;
  _Lease? _lease;
  final Set<int> _choices = <int>{};
  bool _editing = false;
  bool _delivering = false;
  DateTime get _now => _clock().toUtc();

  bool get leaseValid {
    final lease = _lease;
    if (lease == null ||
        !guard.canAccessProtectedRoutes ||
        guard.revision != lease.stamp.revision) {
      return false;
    }
    final now = _now;
    final age = _elapsed() - lease.elapsedStart;
    return !now.isBefore(lease.start) &&
        now.isBefore(lease.end) &&
        !age.isNegative &&
        age < Run2Policy.leaseAge;
  }

  void clearLease() {
    _lease = null;
    _choices.clear();
    _moduleTargets.clear();
    _visibleModuleItems.clear();
  }

  Future<_Stamp> _stamp() async {
    final revision = guard.revision;
    if (!guard.canAccessProtectedRoutes) {
      throw const Run2WorkspaceUnavailable();
    }
    final session = await loadSession();
    final context = Run2Policy.context(session);
    if (session == null ||
        context == null ||
        !guard.canAccessProtectedRoutes ||
        guard.revision != revision) {
      throw const Run2WorkspaceUnavailable();
    }
    final exp = Run2Policy.tokenExpiry(session);
    if (exp == null || !_now.isBefore(exp)) {
      throw const Run2WorkspaceUnavailable();
    }
    return _Stamp(session, context, revision);
  }

  Future<void> _check(_Stamp stamp) async {
    final current = await _stamp();
    if (current.revision != stamp.revision ||
        !Run2Policy.sameSession(stamp.session, current.session)) {
      clearLease();
      throw const Run2WorkspaceUnavailable();
    }
  }

  Future<_Stamp> _authorized() async {
    final lease = _lease;
    if (!leaseValid || lease == null) {
      clearLease();
      throw const Run2WorkspaceUnavailable();
    }
    try {
      await _check(lease.stamp);
      if (!leaseValid || !identical(lease, _lease)) {
        throw const Run2WorkspaceUnavailable();
      }
      return lease.stamp;
    } on Object {
      clearLease();
      rethrow;
    }
  }

  Future<bool> verifyLease() async {
    try {
      await _authorized();
      return true;
    } on Object {
      return false;
    }
  }

  Future<void> _checkLease(_Stamp stamp) async {
    final current = await _authorized();
    if (current.revision != stamp.revision ||
        !Run2Policy.sameSession(stamp.session, current.session)) {
      throw const Run2WorkspaceUnavailable();
    }
  }

  Future<T> _edit<T>(Future<T> Function() action) async {
    if (_editing) {
      throw const Run2WorkspaceUnavailable();
    }
    _editing = true;
    try {
      return await action();
    } finally {
      _editing = false;
    }
  }

  /// Only the real server tenant-context response establishes a short lease.
  /// The lease is never persisted: restart requires new online verification.
  Future<void> refreshOnline() => _edit(() async {
    clearLease();
    final stamp = await _stamp();
    final start = _now;
    final elapsedStart = _elapsed();
    await transport.verifyScope(stamp.session);
    await _check(stamp);
    final exp = Run2Policy.tokenExpiry(stamp.session)!;
    final limit = start.add(Run2Policy.leaseAge);
    _lease = _Lease(
      stamp,
      start,
      exp.isBefore(limit) ? exp : limit,
      elapsedStart,
    );
    try {
      await _checkLease(stamp);
      // This is an observed liveness sample, not a readiness or safety grant.
      final healthy = await transport.checkHealth();
      await _checkLease(stamp);
      if (healthy) {
        final old = await _rows(stamp, Run2Policy.snapshot);
        for (final row in old) {
          await _checkLease(stamp);
          await store.removeRun2LocalOnly(
            operationId: row.operationId,
            context: stamp.context,
            type: Run2Policy.snapshot,
          );
        }
        await _persist(stamp, Run2Policy.snapshot, {'healthy': true});
      }
    } on Object {
      clearLease();
      rethrow;
    }
  });

  Future<List<OperationQueueEntryData>> _rows(_Stamp stamp, String type) =>
      store.listScopedOperationPage(
        context: stamp.context,
        operationType: type,
        limit: 100,
      );

  static bool _terminal(String state) =>
      const {'acknowledged', 'rejected', 'cancelled'}.contains(state);

  Future<void> _persist(
    _Stamp stamp,
    String type,
    Map<String, dynamic> values,
  ) async {
    final rows = await _rows(stamp, type);
    if (rows.length >= 50) {
      throw StateError('Local work capacity reached.');
    }
    if (Run2Policy.replayTypes.contains(type) &&
        rows.any((r) => !_terminal(r.lifecycleState))) {
      // Do not allow a fresh key to bypass unresolved work, even after restart.
      throw StateError('Existing delivery needs review.');
    }
    final now = _now;
    final payload = <String, dynamic>{
      'role': stamp.role,
      'captured_at': now.toIso8601String(),
      ...values,
    };
    Run2Policy.validate(type, payload, stamp.role);
    await _checkLease(stamp);
    final outcome = await store.enqueueEncrypted(
      Run2Policy.identity(type),
      context: stamp.context,
      payload: payload,
      now: now,
    );
    if (outcome != OperationQueueEnqueueResult.inserted) {
      throw const Run2WorkspaceUnavailable();
    }
    await _checkLease(stamp);
    if (Run2Policy.replayTypes.contains(type)) {
      notifyQueued();
    }
  }

  Future<void> saveReminder(String domain, String intent) => _edit(() async {
    final stamp = await _authorized();
    await _persist(stamp, Run2Policy.draft, {
      'domain': domain,
      'intent': intent,
    });
  });

  Future<void> queueFeedback(String preset) => _edit(() async {
    final stamp = await _authorized();
    await _persist(stamp, Run2Policy.feedback, {'preset': preset});
  });

  Future<List<Run2ConversationChoice>> loadOrdinaryChats() => _edit(() async {
    final stamp = await _authorized();
    _choices.clear();
    final values = await transport.listOrdinaryChats(stamp.session);
    await _checkLease(stamp);
    _choices.addAll(values.map((item) => item.id));
    return values;
  });

  Future<void> queueChat(int conversationId, String preset) => _edit(() async {
    final stamp = await _authorized();
    if (!_choices.contains(conversationId)) {
      throw const Run2WorkspaceUnavailable();
    }
    await _persist(stamp, Run2Policy.chat, {
      'conversation_id': conversationId,
      'preset': preset,
    });
  });

  Future<void> deleteReminder(String id) => _edit(() async {
    final stamp = await _authorized();
    await store.removeRun2LocalOnly(
      operationId: id,
      context: stamp.context,
      type: Run2Policy.draft,
    );
    await _checkLease(stamp);
  });

  Future<Run2View> view() async {
    final stamp = await _authorized();
    final items = <Run2WorkItem>[];
    for (final type in <String>{
      ...Run2Policy.localTypes,
      ...Run2Policy.replayTypes,
    }) {
      final rows = await _rows(stamp, type);
      for (final row in rows) {
        await _checkLease(stamp);
        final payload = await store.loadPayload(
          operationId: row.operationId,
          context: stamp.context,
        );
        Run2Policy.validate(type, payload, stamp.role);
        final captured = Run2Policy.captured(payload);
        final now = _now;
        final fresh = Run2Policy.fresh(type, payload, now);
        if (Run2Policy.localTypes.contains(type) && !fresh) {
          // Clock rollback does not delete future-dated local data.
          if (!now.isBefore(captured)) {
            await store.removeRun2LocalOnly(
              operationId: row.operationId,
              context: stamp.context,
              type: type,
            );
          }
          continue;
        }
        final label = switch (type) {
          Run2Policy.snapshot =>
            'Last successful backend liveness check (historical only)',
          Run2Policy.draft => '${payload['domain']}: ${payload['intent']}',
          Run2Policy.feedback => 'Product feedback: ${payload['preset']}',
          Run2Policy.chat => 'Routine chat: ${payload['preset']}',
          _ => 'Unrecognized',
        };
        items.add(
          Run2WorkItem(
            id: row.operationId,
            type: type,
            state: !fresh && !_terminal(row.lifecycleState)
                ? 'reviewRequired'
                : row.lifecycleState,
            capturedAt: captured,
            description: label,
          ),
        );
      }
    }
    await _checkLease(stamp);
    items.sort((a, b) => b.capturedAt.compareTo(a.capturedAt));
    return Run2View(role: stamp.role, items: List.unmodifiable(items));
  }

  static OperationReplayDisposition _disposition(String state) =>
      switch (state) {
        'acknowledged' => OperationReplayDisposition.acknowledged,
        'retryScheduled' => OperationReplayDisposition.retryScheduled,
        'reconciliationRequired' =>
          OperationReplayDisposition.reconciliationRequired,
        'userInterventionRequired' =>
          OperationReplayDisposition.userInterventionRequired,
        'rejected' => OperationReplayDisposition.rejected,
        _ => OperationReplayDisposition.staleOperation,
      };

  static const _stable =
      OperationReplayContract.endpointSupportsStableIdempotencyKey;

  /// Same scheduler invokes this finite adapter after the existing appointment
  /// worker. A lease is not replay authorization: each network delivery uses
  /// current server scope, current ordinary-chat eligibility and exact key.
  Future<OperationReplayRunResult> runDelivery() async {
    final results = <OperationReplayItemResult>[];
    if (_delivering) {
      return OperationReplayRunResult(
        items: results,
        blockedReason: 'run2DeliveryBusy',
      );
    }
    _delivering = true;
    try {
      final stamp = await _stamp();
      final now = _now;
      final interrupted = await store.listInFlight(
        context: stamp.context,
        limit: 100,
      );
      for (final row in interrupted) {
        if (!Run2Policy.replayTypes.contains(row.operationType)) {
          continue;
        }
        final updated = await store.recordFailure(
          operationId: row.operationId,
          failure: OperationAttemptFailure.outcomeUnknown,
          now: now,
          replayContract: _stable,
        );
        results.add(
          OperationReplayItemResult(
            operationId: row.operationId,
            disposition: _disposition(updated.lifecycleState),
            category: 'interruptedPresetDelivery',
          ),
        );
      }
      final ready = await store.listReady(
        context: stamp.context,
        operationTypes: Run2Policy.replayTypes,
        now: now,
        limit: 100,
      );
      for (final row in ready) {
        final type = row.operationType;
        Map<String, dynamic>? loaded;
        var valid = false;
        try {
          loaded = await store.loadPayload(
            operationId: row.operationId,
            context: stamp.context,
          );
          Run2Policy.validate(type, loaded, stamp.role);
          valid = Run2Policy.fresh(type, loaded, _now);
        } on Object {
          // Invalid or expired local data must never become a network request.
        }
        if (!valid || loaded == null) {
          await store.holdRun2Preset(
            operationId: row.operationId,
            context: stamp.context,
            type: type,
            now: _now,
          );
          results.add(
            OperationReplayItemResult(
              operationId: row.operationId,
              disposition: OperationReplayDisposition.reconciliationRequired,
              category: 'presetExpiredOrInvalid',
            ),
          );
          continue;
        }
        final payload = loaded;
        await _check(stamp);
        try {
          await transport.verifyScope(stamp.session);
          if (type == Run2Policy.chat) {
            await transport.verifyOrdinaryChat(
              stamp.session,
              payload['conversation_id'] as int,
            );
          }
          await _check(stamp);
        } on ApiException catch (error) {
          // Connectivity hints are not proof of backend reachability. A
          // transient preflight failure has not sent the operation; retain it
          // and let the existing scheduler try again after a bounded delay.
          final status = error.statusCode;
          if (status == null || status >= 500 || status == 429) {
            await _check(stamp);
            return OperationReplayRunResult(
              items: results,
              blockedReason: 'presetPreflightTransient',
              nextRetryAt: _now.add(const Duration(seconds: 30)),
            );
          }
          await store.holdRun2Preset(
            operationId: row.operationId,
            context: stamp.context,
            type: type,
            now: _now,
          );
          results.add(
            OperationReplayItemResult(
              operationId: row.operationId,
              disposition: OperationReplayDisposition.reconciliationRequired,
              category: 'presetPreflightDenied',
            ),
          );
          clearLease();
          break;
        } on Object {
          // No send occurred. Hold for a human rather than creating a tight
          // retry loop or treating a missing authorization as a transient grant.
          await store.holdRun2Preset(
            operationId: row.operationId,
            context: stamp.context,
            type: type,
            now: _now,
          );
          results.add(
            OperationReplayItemResult(
              operationId: row.operationId,
              disposition: OperationReplayDisposition.reconciliationRequired,
              category: 'presetPreflightUnavailable',
            ),
          );
          clearLease();
          break;
        }
        if (!Run2Policy.fresh(type, payload, _now)) {
          await store.holdRun2Preset(
            operationId: row.operationId,
            context: stamp.context,
            type: type,
            now: _now,
          );
          continue;
        }
        await store.transition(
          operationId: row.operationId,
          expectedState: OperationLifecycleState.values.byName(
            row.lifecycleState,
          ),
          nextState: OperationLifecycleState.inFlight,
          now: _now,
          context: stamp.context,
        );
        var stableRetry = false;
        var acknowledged = false;
        try {
          await _check(stamp);
          if (!Run2Policy.fresh(type, payload, _now)) {
            throw const Run2WorkspaceUnavailable();
          }
          stableRetry = true;
          await transport.deliver(
            stamp.session,
            type,
            payload,
            row.idempotencyKey,
          );
          stableRetry = false;
          await _check(stamp);
          acknowledged = true;
        } on ApiException catch (error) {
          // Explicit 4xx denial/conflict is not automatically replayed. A
          // transport timeout or 5xx may be post-commit; keep the same key.
          final status = error.statusCode;
          if (status != null && status >= 400 && status < 500) {
            stableRetry = false;
          }
        } on Object {
          // Keep the durable identity. Never infer no effect from an exception.
        }
        if (acknowledged) {
          final updated = await store.transition(
            operationId: row.operationId,
            expectedState: OperationLifecycleState.inFlight,
            nextState: OperationLifecycleState.acknowledged,
            now: _now,
            context: stamp.context,
          );
          results.add(
            OperationReplayItemResult(
              operationId: row.operationId,
              disposition: _disposition(updated.lifecycleState),
            ),
          );
        } else {
          try {
            await _check(stamp);
          } on Object {
            stableRetry = false;
          }
          final updated = await store.recordFailure(
            operationId: row.operationId,
            failure: OperationAttemptFailure.outcomeUnknown,
            now: _now,
            replayContract: stableRetry && Run2Policy.fresh(type, payload, _now)
                ? _stable
                : OperationReplayContract.notVerified,
          );
          results.add(
            OperationReplayItemResult(
              operationId: row.operationId,
              disposition: _disposition(updated.lifecycleState),
              category: 'presetOutcomeUnconfirmed',
            ),
          );
        }
      }
      await _check(stamp);
      final next = await store.nextScheduledAttempt(
        context: stamp.context,
        now: _now,
      );
      return OperationReplayRunResult(items: results, nextRetryAt: next);
    } on Object {
      return OperationReplayRunResult(
        items: results,
        blockedReason: 'run2ScopeOrStorageUnavailable',
      );
    } finally {
      _delivering = false;
    }
  }
}

Future<OperationReplayRunResult> runCombinedOfflineDelivery(
  OperationReplayWorker appointments,
  Run2Workspace workspace,
) async {
  final a = await appointments.runOnce();
  final b = await workspace.runDelivery();
  final aNext = a.nextRetryAt;
  final bNext = b.nextRetryAt;
  final next = aNext == null
      ? bNext
      : (bNext == null || aNext.isBefore(bNext) ? aNext : bNext);
  return OperationReplayRunResult(
    items: [...a.items, ...b.items],
    blockedReason: a.blockedReason ?? b.blockedReason,
    nextRetryAt: next,
  );
}
