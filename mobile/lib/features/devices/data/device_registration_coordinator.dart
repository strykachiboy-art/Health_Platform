import 'dart:convert';

import 'package:cryptography/cryptography.dart';

import 'device_registration_models.dart';
import 'device_registration_remote_data_source.dart';
import 'device_token_provider.dart';

class DeviceTokenProviderUnavailable implements Exception {
  const DeviceTokenProviderUnavailable();
}

class DeviceRegistrationBusy implements Exception {
  const DeviceRegistrationBusy();
}

class DeviceRegistrationResult {
  const DeviceRegistrationResult({
    required this.receipt,
    required this.previousDeviceCleanupConfirmed,
  });

  final DeviceRegistrationReceipt receipt;

  /// False means the replacement was registered but deactivation of the old
  /// token was not confirmed. Never silently retry a possibly committed POST.
  final bool previousDeviceCleanupConfirmed;
}

/// Explicit registration, no automatic retries or speculative token creation.
/// P24D supplies the real provider; UI/user intent triggers this coordinator.
/// This instance must live for the authenticated router/session lifetime.
class DeviceRegistrationCoordinator {
  DeviceRegistrationCoordinator({required this.source, required this.provider});

  final DeviceRegistrationRemoteDataSource source;
  final DeviceTokenProvider provider;

  final Set<String> _attempted = <String>{};
  DeviceRegistrationReceipt? _lastReceipt;
  String? _lastSessionKey;
  String? _lastTokenDigest;
  bool _lastCleanupConfirmed = true;
  bool _inFlight = false;

  /// Do not infer the current handset from platform or device name.
  /// A P24B receipt is evidence only for the exact authenticated session.
  int? confirmedDeviceIdForSession(DeviceRegistrationSession session) {
    final receipt = _lastReceipt;
    if (receipt == null ||
        _lastSessionKey != session.key ||
        receipt.userId != session.tokenSession.userId) {
      return null;
    }
    return receipt.id;
  }

  Future<DeviceRegistrationResult> registerLatestToken() async {
    if (_inFlight) {
      throw const DeviceRegistrationBusy();
    }
    _inFlight = true;
    try {
      final before = await source.requireSession();
      final token = await provider.currentToken();
      if (token == null) {
        throw const DeviceTokenProviderUnavailable();
      }
      await source.assertSameSession(before);

      // Retain only a one-way digest, never a provider token in the controller.
      final digest = await Sha256().hash(
        utf8.encode('${token.platform.wireValue}:${token.deviceToken}'),
      );
      final fingerprint = digest.bytes
          .map((byte) => byte.toRadixString(16).padLeft(2, '0'))
          .join();
      await source.assertSameSession(before);
      final key = '${before.key}/$fingerprint';

      final previousReceipt = _lastSessionKey == before.key
          ? _lastReceipt
          : null;
      if (_lastSessionKey == before.key &&
          _lastTokenDigest == fingerprint &&
          previousReceipt != null) {
        return DeviceRegistrationResult(
          receipt: previousReceipt,
          previousDeviceCleanupConfirmed: _lastCleanupConfirmed,
        );
      }
      if (!_attempted.add(key)) {
        throw const DeviceRegistrationOutcomeUnknown();
      }

      final DeviceRegistrationReceipt registered;
      try {
        registered = await source.register(token, expectedSession: before);
      } on DeviceRegistrationOffline {
        // Offline is confirmed before HTTP dispatch: safe to try later.
        _attempted.remove(key);
        rethrow;
      } on DeviceRegistrationSessionUnavailable {
        // Session preflight failed before HTTP dispatch.
        _attempted.remove(key);
        rethrow;
      }
      _lastReceipt = registered;
      _lastSessionKey = before.key;
      _lastTokenDigest = fingerprint;
      _lastCleanupConfirmed = true;

      if (previousReceipt != null && previousReceipt.id != registered.id) {
        try {
          await source.deactivatePrevious(
            previousReceipt.id,
            expectedSession: before,
          );
        } on Object {
          // The new registration is confirmed, but the old token could still
          // be active. P24E owns read-back reconciliation, never a blind retry.
          await source.assertSameSession(before);
          _lastCleanupConfirmed = false;
        }
      }
      await source.assertSameSession(before);
      return DeviceRegistrationResult(
        receipt: registered,
        previousDeviceCleanupConfirmed: _lastCleanupConfirmed,
      );
    } finally {
      _inFlight = false;
    }
  }
}
