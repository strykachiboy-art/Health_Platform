import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_scheduler.dart';
import 'package:clinic_system_pro/core/sync/operation_replay_worker.dart';

class _FakeConnectivity implements DeviceConnectivitySource {
  _FakeConnectivity(this.current);

  DeviceNetworkStatus current;
  final StreamController<DeviceNetworkStatus> _controller =
      StreamController<DeviceNetworkStatus>.broadcast(sync: true);

  @override
  Future<DeviceNetworkStatus> checkStatus() async => current;

  @override
  Stream<DeviceNetworkStatus> get statusChanges => _controller.stream;

  void emit(DeviceNetworkStatus status) {
    current = status;
    _controller.add(status);
  }

  Future<void> close() => _controller.close();
}

OperationReplayRunResult _result({DateTime? nextRetryAt}) {
  return OperationReplayRunResult(
    items: const <OperationReplayItemResult>[],
    nextRetryAt: nextRetryAt,
  );
}

Future<void> _settle() =>
    Future<void>.delayed(const Duration(milliseconds: 20));

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('runs replay when connectivity becomes available', () async {
    final connectivity = _FakeConnectivity(DeviceNetworkStatus.disconnected);

    var calls = 0;
    final scheduler = OperationReplayScheduler(
      connectivitySource: connectivity,
      runReplay: () async {
        calls++;
        return _result();
      },
    );

    try {
      await scheduler.start();
      expect(calls, 0);

      connectivity.emit(DeviceNetworkStatus.connected);
      await _settle();

      expect(calls, 1);
    } finally {
      await scheduler.dispose();
      await connectivity.close();
    }
  });

  test('pause blocks replay until the session-change notification', () async {
    final connectivity = _FakeConnectivity(DeviceNetworkStatus.connected);

    var calls = 0;
    final scheduler = OperationReplayScheduler(
      connectivitySource: connectivity,
      runReplay: () async {
        calls++;
        return _result();
      },
    );

    try {
      await scheduler.start();
      await _settle();
      expect(calls, 1);

      scheduler.pause();
      connectivity.emit(DeviceNetworkStatus.connected);
      await _settle();

      expect(calls, 1);

      scheduler.notifySessionChanged();
      await _settle();

      expect(calls, 2);
    } finally {
      await scheduler.dispose();
      await connectivity.close();
    }
  });

  test('runs replay promptly when a new operation is queued online', () async {
    final connectivity = _FakeConnectivity(DeviceNetworkStatus.connected);

    var calls = 0;
    final scheduler = OperationReplayScheduler(
      connectivitySource: connectivity,
      runReplay: () async {
        calls++;
        return _result();
      },
    );

    try {
      await scheduler.start();
      await _settle();
      expect(calls, 1);

      scheduler.notifyOperationQueued();
      await _settle();

      expect(calls, 2);
    } finally {
      await scheduler.dispose();
      await connectivity.close();
    }
  });
  test('rechecks connectivity and replays when the app resumes', () async {
    final connectivity = _FakeConnectivity(DeviceNetworkStatus.disconnected);

    var calls = 0;
    final scheduler = OperationReplayScheduler(
      connectivitySource: connectivity,
      runReplay: () async {
        calls++;
        return _result();
      },
    );

    try {
      await scheduler.start();
      expect(calls, 0);

      // The network changes without a stream event; resume must recheck it.
      connectivity.current = DeviceNetworkStatus.connected;
      scheduler.didChangeAppLifecycleState(AppLifecycleState.resumed);
      await _settle();

      expect(calls, 1);
    } finally {
      await scheduler.dispose();
      await connectivity.close();
    }
  });

  test('schedules a retry at its persisted due time', () async {
    final connectivity = _FakeConnectivity(DeviceNetworkStatus.connected);

    var calls = 0;
    final scheduler = OperationReplayScheduler(
      connectivitySource: connectivity,
      runReplay: () async {
        calls++;

        if (calls == 1) {
          return _result(
            nextRetryAt: DateTime.now().toUtc().add(
              const Duration(milliseconds: 100),
            ),
          );
        }

        return _result();
      },
    );

    try {
      await scheduler.start();
      await Future<void>.delayed(const Duration(milliseconds: 450));

      expect(calls, 2);
    } finally {
      await scheduler.dispose();
      await connectivity.close();
    }
  });
}
