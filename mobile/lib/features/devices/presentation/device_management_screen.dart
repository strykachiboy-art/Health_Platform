import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/devices/data/device_management_models.dart';
import 'package:clinic_system_pro/features/devices/data/device_management_remote_data_source.dart';
import 'package:clinic_system_pro/features/devices/data/device_registration_remote_data_source.dart';

/// User-owned device management only. Device identifiers are not session IDs.
/// Deactivation stops push targeting; it does NOT revoke authentication.
class DeviceManagementScreen extends StatefulWidget {
  const DeviceManagementScreen({super.key, required this.source});

  final DeviceManagementRemoteDataSource source;

  @override
  State<DeviceManagementScreen> createState() => _DeviceManagementScreenState();
}

class _DeviceManagementScreenState extends State<DeviceManagementScreen> {
  ManagedDevicePage? _page;
  String? _status;
  bool _busy = false;
  bool _uncertain = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.registrationScope.sessionNavigationGuard.addListener(
      _sessionChanged,
    );
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _load();
    });
  }

  @override
  void dispose() {
    widget.source.registrationScope.sessionNavigationGuard.removeListener(
      _sessionChanged,
    );
    ++_generation;
    super.dispose();
  }

  void _sessionChanged() {
    ++_generation;
    if (!mounted) return;
    setState(() {
      _page = null;
      _busy = false;
      _uncertain = false;
      _status = 'Your authenticated session changed. Sign in again.';
    });
  }

  String _safeError(Object error) => switch (error) {
    DeviceRegistrationSessionUnavailable() =>
      'Your session is unavailable. Sign in to manage devices.',
    DeviceRegistrationOffline() =>
      'Device management requires an online connection.',
    DeviceManagementOutcomeUnknown() => 'The action may have completed. Refresh devices before taking another action.',
    ApiException() => 'The server refused the device request.',
    _ => 'Could not confirm device information. Try refreshing.',
  };

  Future<void> _load({int page = 1}) async {
    if (_busy) return;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _status = null;
    });
    try {
      final result = await widget.source.list(page: page);
      if (!mounted || generation != _generation) return;
      setState(() {
        _page = result;
        _busy = false;
        _uncertain = false;
      });
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _busy = false;
        _page = null;
        _status = _safeError(error);
      });
    }
  }

  Future<void> _confirmAction(
    ManagedDevice device, {
    required bool delete,
  }) async {
    if (_busy || _uncertain) return;
    final current = _page?.confirmedCurrentDeviceId == device.id;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(delete ? 'Delete device?' : 'Deactivate device?'),
        content: Text(
          '${delete ? 'Permanently delete' : 'Deactivate'} device #${device.id}? '
          '${current ? 'This device was registered during your current session. ' : ''}'
          'This may stop push notifications but does not sign out or revoke sessions.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          TextButton(
            key: ValueKey(
              delete ? 'p24e-confirm-delete' : 'p24e-confirm-deactivate',
            ),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: Text(delete ? 'Delete' : 'Deactivate'),
          ),
        ],
      ),
    );
    if (!mounted || confirmed != true || _busy || _uncertain) return;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _status = null;
    });
    try {
      if (delete) {
        await widget.source.delete(device);
      } else {
        await widget.source.deactivate(device);
      }
      if (!mounted || generation != _generation) return;
      setState(() {
        _busy = false;
        _status = 'Action acknowledged. Refreshing the server device list.';
      });
      await _load(page: _page?.page ?? 1);
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _busy = false;
        _uncertain = error is DeviceManagementOutcomeUnknown;
        _status = _safeError(error);
      });
    }
  }

  String _lastSeen(ManagedDevice device) {
    final instant = device.lastSeenAt;
    if (instant == null) return 'Last seen: not recorded';
    return 'Last seen: ${instant.toLocal().toString().split('.').first}';
  }

  @override
  Widget build(BuildContext context) {
    final page = _page;
    final activeGuard = widget
        .source
        .registrationScope
        .sessionNavigationGuard
        .canAccessProtectedRoutes;
    return Scaffold(
      key: const ValueKey('p24e-device-management-screen'),
      appBar: AppBar(title: const Text('My devices')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text(
              'Only devices registered to your account appear here. '
              'Turning off push delivery does not revoke a login session.',
            ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              key: const ValueKey('p24e-refresh'),
              onPressed: !_busy && activeGuard
                  ? () => _load(page: page?.page ?? 1)
                  : null,
              icon: const Icon(Icons.refresh),
              label: const Text('Refresh devices'),
            ),
            if (_busy)
              const LinearProgressIndicator(key: ValueKey('p24e-loading')),
            if (_status != null)
              Text(_status!, key: const ValueKey('p24e-status')),
            if (page != null) ...[
              Text('Registered devices: ${page.total}'),
              if (page.confirmedCurrentDeviceId == null)
                const Text('Current device unverified in this session.'),
              if (page.items.isEmpty)
                const Text('No devices found on this page.'),
              for (final device in page.items)
                Card(
                  key: ValueKey('p24e-device-${device.id}'),
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          device.name?.isNotEmpty == true
                              ? device.name!
                              : 'Device #${device.id}',
                        ),
                        Text(
                          '${device.platform.toUpperCase()} · Device #${device.id}',
                        ),
                        Text(device.active ? 'Active' : 'Inactive'),
                        Text(_lastSeen(device)),
                        if (page.confirmedCurrentDeviceId == device.id)
                          const Text(
                            'Confirmed on this device in this session',
                          ),
                        Wrap(
                          spacing: 8,
                          children: [
                            OutlinedButton(
                              key: ValueKey('p24e-deactivate-${device.id}'),
                              onPressed:
                                  _busy ||
                                      _uncertain ||
                                      !activeGuard ||
                                      !device.active
                                  ? null
                                  : () => _confirmAction(device, delete: false),
                              child: const Text('Deactivate'),
                            ),
                            OutlinedButton(
                              key: ValueKey('p24e-delete-${device.id}'),
                              onPressed: _busy || _uncertain || !activeGuard
                                  ? null
                                  : () => _confirmAction(device, delete: true),
                              child: const Text('Delete'),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  TextButton(
                    key: const ValueKey('p24e-previous'),
                    onPressed: _busy || !activeGuard || !page.hasPrev
                        ? null
                        : () => _load(page: page.page - 1),
                    child: const Text('Previous'),
                  ),
                  Text('Page ${page.page}'),
                  TextButton(
                    key: const ValueKey('p24e-next'),
                    onPressed: _busy || !activeGuard || !page.hasNext
                        ? null
                        : () => _load(page: page.page + 1),
                    child: const Text('Next'),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}
