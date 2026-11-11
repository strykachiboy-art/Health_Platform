import 'dart:async';

import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_models.dart';
import 'package:clinic_system_pro/features/devices/data/device_token_provider.dart';

/// Android-only FID provider. The FID is an opaque server targeting identifier.
/// It is never written to local storage or logged. Backend registration remains
/// user-initiated through the existing guarded P24B route.
class FirebaseInstallationTokenProvider implements DeviceTokenProvider {
  FirebaseInstallationTokenProvider(this._messaging) {
    _registered = _messaging.onRegistered.listen((fid) {
      if (_validFid(fid)) {
        _latestFid = fid;
        final pending = _pending;
        if (pending != null && !pending.isCompleted) pending.complete(fid);
      }
    });
    _unregistered = _messaging.onUnregistered.listen((fid) {
      if (fid == _latestFid) _latestFid = null;
    });
  }

  final FirebaseMessaging _messaging;
  late final StreamSubscription<String> _registered;
  late final StreamSubscription<String> _unregistered;
  String? _latestFid;
  Completer<String>? _pending;
  Future<ProviderDeviceToken?>? _acquiring;

  static bool _validFid(String fid) =>
      fid.length >= 10 &&
      fid.length <= 500 &&
      fid == fid.trim() &&
      RegExp(r'^[A-Za-z0-9_:-]+$').hasMatch(fid);

  @override
  Future<ProviderDeviceToken?> currentToken() =>
      _acquiring ??= _acquire().whenComplete(() => _acquiring = null);

  Future<ProviderDeviceToken?> _acquire() async {
    final permission = await _messaging.requestPermission(
      alert: true,
      badge: true,
      sound: true,
      announcement: false,
      criticalAlert: false,
      provisional: false,
    );
    if (permission.authorizationStatus != AuthorizationStatus.authorized) {
      return null;
    }
    final completer = Completer<String>();
    _pending = completer;
    try {
      // Explicit FID registration uses the API supplied by FlutterFire 17.x.
      // onRegistered is subscribed before this method is called.
      await _messaging.register();
      final fid =
          _latestFid ??
          await completer.future.timeout(
            const Duration(seconds: 20),
            onTimeout: () => '',
          );
      if (!_validFid(fid)) return null;
      return ProviderDeviceToken(
        deviceToken: fid,
        platform: RegisteredDevicePlatform.android,
        deviceName: 'Android device',
      );
    } finally {
      if (identical(_pending, completer)) _pending = null;
    }
  }

  Future<void> dispose() async {
    await _registered.cancel();
    await _unregistered.cancel();
  }
}
