import 'dart:async';

import 'package:flutter/widgets.dart';

import '../networking/device_connectivity_source.dart';
import 'operation_replay_worker.dart';

typedef OperationReplayRunner = Future<OperationReplayRunResult> Function();

/// Foreground scheduling for durable operation replay.
///
/// The scheduler reacts to connectivity changes, app resume, authentication
/// transitions, and durable retry times. It owns no session tokens and never
/// removes queue entries during logout.
class OperationReplayScheduler with WidgetsBindingObserver {
  factory OperationReplayScheduler({
    required DeviceConnectivitySource connectivitySource,
    required OperationReplayRunner runReplay,
  }) {
    return OperationReplayScheduler._(connectivitySource, runReplay);
  }

  OperationReplayScheduler._(this._connectivitySource, this._runReplay);

  final DeviceConnectivitySource _connectivitySource;
  final OperationReplayRunner _runReplay;

  StreamSubscription<DeviceNetworkStatus>? _subscription;
  Future<void>? _activeRun;
  Timer? _retryTimer;
  DateTime? _retryAt;

  bool _started = false;
  bool _disposed = false;
  bool _paused = false;
  bool _connected = false;
  bool _rerunRequested = false;

  Future<void> start() async {
    if (_started || _disposed) return;

    _started = true;
    WidgetsBinding.instance.addObserver(this);

    _subscription = _connectivitySource.statusChanges.listen(
      _handleStatus,
      onError: (Object _) {
        _connected = false;
      },
    );

    await _refreshConnectivity();
  }

  /// Pauses new replay triggers before logout begins.
  ///
  /// An already-running worker independently revalidates its session before
  /// each request. The queue is retained, encrypted and scope-bound.
  void pause() {
    if (_disposed) return;

    _paused = true;
    _retryTimer?.cancel();
    _retryTimer = null;
    _retryAt = null;
  }

  /// Called after a login/session transition to resume scheduling safely.
  void notifySessionChanged() {
    if (_disposed) return;

    _paused = false;
    if (_started) {
      unawaited(_refreshConnectivity());
    }
  }

  /// Requests an immediate replay after an operation is durably queued.
  ///
  /// The usual scheduler guards still apply: replay will not begin while
  /// disconnected, paused, stopped, or disposed.
  void notifyOperationQueued() {
    if (_disposed) return;
    _kick();
  }

  Future<void> _refreshConnectivity() async {
    if (_disposed) return;

    try {
      _handleStatus(await _connectivitySource.checkStatus());
    } catch (_) {
      _handleStatus(DeviceNetworkStatus.unknown);
    }
  }

  void _handleStatus(DeviceNetworkStatus status) {
    if (_disposed) return;

    _connected = status == DeviceNetworkStatus.connected;

    if (_connected) {
      _kick();
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      unawaited(_refreshConnectivity());
    }
  }

  void _kick() {
    if (_disposed || !_started || _paused || !_connected) return;

    if (_activeRun != null) {
      _rerunRequested = true;
      return;
    }

    late final Future<void> task;

    task = _drain().whenComplete(() {
      if (identical(_activeRun, task)) {
        _activeRun = null;
      }

      if (_rerunRequested && !_disposed && !_paused && _connected) {
        _rerunRequested = false;
        _kick();
      }
    });

    _activeRun = task;
  }

  Future<void> _drain() async {
    do {
      _rerunRequested = false;

      if (_disposed || _paused || !_connected) return;

      final OperationReplayRunResult result;

      try {
        result = await _runReplay();
      } catch (_) {
        // A later connectivity, resume, or session event can try again.
        return;
      }

      if (_disposed || _paused) return;

      _scheduleRetry(result.nextRetryAt);
    } while (_rerunRequested && !_disposed && !_paused && _connected);
  }

  void _scheduleRetry(DateTime? nextRetryAt) {
    if (_disposed || _paused || nextRetryAt == null) return;

    // Small allowance prevents a timer firing a fraction early relative to
    // SQLite's persisted timestamp and the worker's UTC clock.
    final target = nextRetryAt.toUtc().add(const Duration(milliseconds: 100));

    // Preserve an earlier timer if another operation has a later due time.
    if (_retryAt != null && !target.isBefore(_retryAt!)) return;

    _retryTimer?.cancel();
    _retryAt = target;

    final remaining = target.difference(DateTime.now().toUtc());

    _retryTimer = Timer(remaining.isNegative ? Duration.zero : remaining, () {
      _retryTimer = null;
      _retryAt = null;
      _kick();
    });
  }

  Future<void> dispose() async {
    if (_disposed) return;

    _disposed = true;
    _retryTimer?.cancel();
    _retryTimer = null;
    _retryAt = null;

    WidgetsBinding.instance.removeObserver(this);
    await _subscription?.cancel();

    final active = _activeRun;
    if (active != null) {
      try {
        await active;
      } catch (_) {
        // The scheduler is being disposed; do not leak an async error.
      }
    }
  }
}
