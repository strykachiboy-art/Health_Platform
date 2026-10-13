import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_bloc/flutter_bloc.dart';

import 'device_connectivity_cubit.dart';

class DeviceConnectivityBanner extends StatefulWidget {
  const DeviceConnectivityBanner({super.key, required this.child});

  final Widget child;

  @override
  State<DeviceConnectivityBanner> createState() =>
      _DeviceConnectivityBannerState();
}

class _DeviceConnectivityBannerState extends State<DeviceConnectivityBanner>
    with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
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
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return BlocBuilder<DeviceConnectivityCubit, DeviceConnectivityViewState>(
      builder: (context, status) {
        if (status != DeviceConnectivityViewState.disconnected) {
          return widget.child;
        }

        final colors = Theme.of(context).colorScheme;

        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Material(
              color: colors.tertiaryContainer,
              child: SafeArea(
                bottom: false,
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 10,
                  ),
                  child: Text(
                    'No device network connection detected. '
                    'Online requests may fail; changes are not saved offline.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: colors.onTertiaryContainer),
                  ),
                ),
              ),
            ),
            Expanded(child: widget.child),
          ],
        );
      },
    );
  }
}
