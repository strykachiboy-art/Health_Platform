import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import 'device_connectivity_cubit.dart';
import 'api_client.dart';

class DeviceConnectivityBanner extends StatefulWidget {
  const DeviceConnectivityBanner({
    super.key,
    required this.child,
    required this.apiClient,
  });

  final Widget child;
  final ApiClient apiClient;

  @override
  State<DeviceConnectivityBanner> createState() =>
      _DeviceConnectivityBannerState();
}

class _DeviceConnectivityBannerState extends State<DeviceConnectivityBanner>
    with WidgetsBindingObserver {
  StreamSubscription<ApiCommunicationStatus>? _communicationSubscription;
  ApiCommunicationStatus _communicationStatus =
      ApiCommunicationStatus.notChecked;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _listenForCommunicationChanges();
  }

  void _listenForCommunicationChanges() {
    _communicationStatus = widget.apiClient.communicationStatus;
    _communicationSubscription = widget.apiClient.communicationStatusChanges
        .listen((status) {
          if (!mounted) return;
          setState(() => _communicationStatus = status);
        });
  }

  @override
  void didUpdateWidget(covariant DeviceConnectivityBanner oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (identical(oldWidget.apiClient, widget.apiClient)) return;

    unawaited(_communicationSubscription?.cancel());
    _listenForCommunicationChanges();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      unawaited(context.read<DeviceConnectivityCubit>().refresh());
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    unawaited(_communicationSubscription?.cancel());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return BlocBuilder<DeviceConnectivityCubit, DeviceConnectivityViewState>(
      builder: (context, deviceStatus) {
        final deviceOffline =
            deviceStatus == DeviceConnectivityViewState.disconnected;
        final requestUnconfirmed =
            _communicationStatus == ApiCommunicationStatus.responseNotReceived;

        if (!deviceOffline && !requestUnconfirmed) {
          return widget.child;
        }

        final colors = Theme.of(context).colorScheme;
        final notices = <Widget>[];

        if (deviceOffline) {
          notices.add(
            _buildNotice(
              message:
                  'No device network connection detected. '
                  'Online requests may fail; changes are not saved offline.',
              background: colors.tertiaryContainer,
              foreground: colors.onTertiaryContainer,
            ),
          );
        }

        if (requestUnconfirmed) {
          notices.add(
            _buildNotice(
              message:
                  'The latest API request did not receive an HTTP response. '
                  'Use the affected screen to verify the result before retrying.',
              background: colors.errorContainer,
              foreground: colors.onErrorContainer,
            ),
          );
        }

        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            ...notices,
            Expanded(child: widget.child),
          ],
        );
      },
    );
  }

  Widget _buildNotice({
    required String message,
    required Color background,
    required Color foreground,
  }) {
    return Material(
      color: background,
      child: SafeArea(
        bottom: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          child: Text(
            message,
            textAlign: TextAlign.center,
            style: TextStyle(color: foreground),
          ),
        ),
      ),
    );
  }
}
