// P23F.S03.B — camera-only barcode capture, never a clinical decision.
// The barcode is returned in memory to a separately confirmed server workflow.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';

enum EmarScanKind { patient, medication }

class EmarCapturedBarcode {
  const EmarCapturedBarcode({required this.value, required this.capturedAtUtc});

  final String value;
  final DateTime capturedAtUtc;
}

typedef EmarBarcodeCapture = Future<EmarCapturedBarcode?> Function(
  BuildContext context,
  EmarScanKind kind,
  SessionNavigationGuard guard,
);

/// Production capture: no gallery import, manual entry, camera upload or PHI log.
Future<EmarCapturedBarcode?> captureEmarBarcode(
  BuildContext context,
  EmarScanKind kind,
  SessionNavigationGuard guard,
) {
  if (!guard.canAccessProtectedRoutes) {
    return Future<EmarCapturedBarcode?>.value(null);
  }
  return Navigator.of(context).push<EmarCapturedBarcode>(
    MaterialPageRoute<EmarCapturedBarcode>(
      builder: (_) => EmarCameraBarcodeCaptureScreen(kind: kind, guard: guard),
    ),
  );
}

class EmarCameraBarcodeCaptureScreen extends StatefulWidget {
  const EmarCameraBarcodeCaptureScreen({
    super.key,
    required this.kind,
    required this.guard,
  });

  final EmarScanKind kind;
  final SessionNavigationGuard guard;

  @override
  State<EmarCameraBarcodeCaptureScreen> createState() =>
      _EmarCameraBarcodeCaptureScreenState();
}

class _EmarCameraBarcodeCaptureScreenState
    extends State<EmarCameraBarcodeCaptureScreen>
    with WidgetsBindingObserver {
  final MobileScannerController _scanner = MobileScannerController(
    autoStart: false,
    detectionSpeed: DetectionSpeed.noDuplicates,
  );
  bool _taken = false;
  bool _closing = false;
  String? _error;
  late final int _revision;

  bool get _current =>
      mounted &&
      !_closing &&
      widget.guard.canAccessProtectedRoutes &&
      widget.guard.revision == _revision;

  @override
  void initState() {
    super.initState();
    _revision = widget.guard.revision;
    widget.guard.addListener(_guardChanged);
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_current) unawaited(_start());
    });
  }

  Future<void> _start() async {
    try {
      await _scanner.start();
    } on Object {
      if (_current && !_taken) {
        setState(() => _error = 'Camera could not start. Check permissions.');
      }
    }
  }

  Future<void> _stop() async {
    try {
      await _scanner.stop();
    } on Object {
      // No barcode action can be inferred from a camera-stop error.
    }
  }

  void _guardChanged() {
    if (_closing ||
        !mounted ||
        (widget.guard.canAccessProtectedRoutes &&
            widget.guard.revision == _revision)) {
      return;
    }
    _closing = true;
    _taken = true;
    // Remove the camera route when session context becomes invalid.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) Navigator.of(context).pop<EmarCapturedBarcode>();
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Permission dialogs may background the app before the camera is ready.
    if (!mounted || _closing || !_scanner.value.hasCameraPermission) return;
    switch (state) {
      case AppLifecycleState.resumed:
        if (_current && !_taken) unawaited(_start());
        return;
      case AppLifecycleState.inactive:
      case AppLifecycleState.paused:
      case AppLifecycleState.hidden:
      case AppLifecycleState.detached:
        unawaited(_stop());
        return;
    }
  }

  void _onDetect(BarcodeCapture capture) {
    if (!_current || _taken) return;
    // Never guess between multiple barcodes in the same camera frame.
    if (capture.barcodes.length != 1) {
      setState(() => _error = 'Present exactly one barcode to the camera.');
      return;
    }
    final raw = capture.barcodes.single.rawValue;
    if (raw == null || raw.isEmpty || raw.trim() != raw || raw.length > 255) {
      setState(
        () => _error = 'Unreadable barcode. Scan the printed code again.',
      );
      return;
    }
    _taken = true;
    _closing = true;
    Navigator.of(context).pop(
      EmarCapturedBarcode(value: raw, capturedAtUtc: DateTime.now().toUtc()),
    );
  }

  @override
  void dispose() {
    _closing = true;
    widget.guard.removeListener(_guardChanged);
    WidgetsBinding.instance.removeObserver(this);
    unawaited(_scanner.dispose());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final what = widget.kind == EmarScanKind.patient ? 'patient' : 'medication';
    return Scaffold(
      appBar: AppBar(title: Text('Scan $what barcode')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(
              'Scan exactly one printed $what barcode. '
              'The camera does not authorize or record medication administration.',
            ),
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.all(8),
              child: Text(
                _error!,
                key: const ValueKey('p23f-s03b-camera-error'),
              ),
            ),
          Expanded(
            child: MobileScanner(controller: _scanner, onDetect: _onDetect),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: TextButton(
              key: const ValueKey('p23f-s03b-camera-cancel'),
              onPressed: () => Navigator.of(context).pop<EmarCapturedBarcode>(),
              child: const Text('Cancel scan'),
            ),
          ),
        ],
      ),
    );
  }
}
