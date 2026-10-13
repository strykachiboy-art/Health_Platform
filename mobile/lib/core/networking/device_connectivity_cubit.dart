import 'dart:async';

import 'package:flutter_bloc/flutter_bloc.dart';

import 'device_connectivity_source.dart';

enum DeviceConnectivityViewState { checking, connected, disconnected, unknown }

class DeviceConnectivityCubit extends Cubit<DeviceConnectivityViewState> {
  DeviceConnectivityCubit({required this.source})
    : super(DeviceConnectivityViewState.checking);

  final DeviceConnectivitySource source;

  StreamSubscription<DeviceNetworkStatus>? _subscription;
  int _revision = 0;
  bool _started = false;

  Future<void> start() async {
    if (_started || isClosed) return;
    _started = true;

    _subscription = source.statusChanges.listen(
      _handleStatus,
      onError: (Object _, StackTrace _) {
        _revision++;
        if (!isClosed) {
          emit(DeviceConnectivityViewState.unknown);
        }
      },
    );

    await refresh();
  }

  /// Rechecks on app resume and prevents older checks overwriting newer events.
  Future<void> refresh() async {
    if (isClosed) return;

    final requestRevision = ++_revision;

    try {
      final status = await source.checkStatus();

      if (isClosed || requestRevision != _revision) return;

      emit(_toViewState(status));
    } catch (_) {
      if (isClosed || requestRevision != _revision) return;

      emit(DeviceConnectivityViewState.unknown);
    }
  }

  void _handleStatus(DeviceNetworkStatus status) {
    _revision++;
    if (!isClosed) {
      emit(_toViewState(status));
    }
  }

  DeviceConnectivityViewState _toViewState(DeviceNetworkStatus status) {
    switch (status) {
      case DeviceNetworkStatus.connected:
        return DeviceConnectivityViewState.connected;
      case DeviceNetworkStatus.disconnected:
        return DeviceConnectivityViewState.disconnected;
      case DeviceNetworkStatus.unknown:
        return DeviceConnectivityViewState.unknown;
    }
  }

  @override
  Future<void> close() async {
    await _subscription?.cancel();
    await super.close();
  }
}
