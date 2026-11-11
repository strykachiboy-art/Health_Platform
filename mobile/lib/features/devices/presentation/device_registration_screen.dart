import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_coordinator.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_models.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_remote_data_source.dart';

/// Push registration is deliberately provider-injected. Without P24D's real
/// provider, the router shows unavailable instead of issuing a fake token.
class DeviceRegistrationScreen extends StatefulWidget {
  const DeviceRegistrationScreen({super.key, required this.coordinator});

  final DeviceRegistrationCoordinator coordinator;

  @override
  State<DeviceRegistrationScreen> createState() =>
      _DeviceRegistrationScreenState();
}

class _DeviceRegistrationScreenState extends State<DeviceRegistrationScreen> {
  bool _busy = false;
  String? _status;
  DeviceRegistrationResult? _result;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.coordinator.source.sessionNavigationGuard.addListener(
      _clearOnSessionChange,
    );
  }

  @override
  void dispose() {
    widget.coordinator.source.sessionNavigationGuard.removeListener(
      _clearOnSessionChange,
    );
    _generation++;
    super.dispose();
  }

  void _clearOnSessionChange() {
    _generation++;
    if (!mounted) {
      return;
    }
    setState(() {
      _busy = false;
      _status = null;
      _result = null;
    });
  }

  Future<void> _register() async {
    if (_busy) {
      return;
    }
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _status = null;
      _result = null;
    });
    try {
      final result = await widget.coordinator.registerLatestToken();
      if (!mounted || _generation != generation) {
        return;
      }
      setState(() {
        _result = result;
        _busy = false;
      });
    } on Object catch (error) {
      if (!mounted || _generation != generation) {
        return;
      }
      setState(() {
        _busy = false;
        _result = null;
        _status = switch (error) {
          DeviceTokenProviderUnavailable() => 'Push registration is unavailable until a verified provider supplies a token.',
          DeviceRegistrationSessionUnavailable() => 'Your authenticated session changed. Sign in before registering again.',
          DeviceRegistrationOffline() =>
            'Registration was not sent. Connect to the network and try again.',
          DeviceRegistrationBusy() =>
            'A device registration request is already in progress.',
          DeviceRegistrationOutcomeUnknown() => 'Registration status is unconfirmed. Do not retry this token; review your devices online.',
          ApiException() => 'The backend rejected device registration. Check your account permissions.',
          _ => 'Device registration could not be confirmed. Do not retry automatically.',
        };
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final result = _result;
    return Scaffold(
      appBar: AppBar(title: const Text('Register this device')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            const Text(
              'Registration uses a private token supplied by an authorized push provider. '
              'It cannot be entered manually and is never displayed.',
            ),
            const SizedBox(height: 16),
            ElevatedButton(
              key: const ValueKey('p24b-register-device'),
              onPressed:
                  _busy ||
                      !widget
                          .coordinator
                          .source
                          .sessionNavigationGuard
                          .canAccessProtectedRoutes
                  ? null
                  : _register,
              child: Text(_busy ? 'Registering…' : 'Register device'),
            ),
            if (_busy) ...[
              const SizedBox(height: 16),
              const CircularProgressIndicator(
                key: ValueKey('p24b-device-registering'),
              ),
            ],
            if (_status != null) ...[
              const SizedBox(height: 16),
              Text(_status!, key: const ValueKey('p24b-registration-status')),
            ],
            if (result != null) ...[
              const SizedBox(height: 16),
              Text(
                'Device ${result.receipt.id} registered for this account '
                '(${result.receipt.platform.wireValue}).',
                key: const ValueKey('p24b-registration-confirmed'),
              ),
              if (!result.previousDeviceCleanupConfirmed)
                const Text(
                  'The previous device token may still be active. '
                  'Review it in device management before assuming rotation completed.',
                  key: ValueKey('p24b-rotation-reconcile'),
                ),
            ],
          ],
        ),
      ),
    );
  }
}
