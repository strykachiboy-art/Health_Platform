import 'package:connectivity_plus/connectivity_plus.dart';

enum DeviceNetworkStatus { connected, disconnected, unknown }

abstract interface class DeviceConnectivitySource {
  Future<DeviceNetworkStatus> checkStatus();

  Stream<DeviceNetworkStatus> get statusChanges;
}

DeviceNetworkStatus classifyConnectivityResults(
  List<ConnectivityResult> results,
) {
  if (results.isEmpty) {
    return DeviceNetworkStatus.unknown;
  }

  if (results.every((result) => result == ConnectivityResult.none)) {
    return DeviceNetworkStatus.disconnected;
  }

  return DeviceNetworkStatus.connected;
}

class ConnectivityPlusDeviceConnectivitySource
    implements DeviceConnectivitySource {
  ConnectivityPlusDeviceConnectivitySource({Connectivity? connectivity})
    : _connectivity = connectivity ?? Connectivity();

  final Connectivity _connectivity;

  @override
  Future<DeviceNetworkStatus> checkStatus() async {
    return classifyConnectivityResults(await _connectivity.checkConnectivity());
  }

  @override
  Stream<DeviceNetworkStatus> get statusChanges {
    return _connectivity.onConnectivityChanged.map(classifyConnectivityResults);
  }
}
