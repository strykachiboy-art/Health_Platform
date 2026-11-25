import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';

enum BankScanKind { wristband, component }

class BankCapturedCode {
  const BankCapturedCode(this.value, this.at);
  final String value;
  final DateTime at;
}

typedef BankCameraCapture = Future<BankCapturedCode?> Function(
  BuildContext,
  BankScanKind,
  SessionNavigationGuard,
);
Future<BankCapturedCode?> captureBankCode(
  BuildContext context,
  BankScanKind kind,
  SessionNavigationGuard guard,
) {
  if (!guard.canAccessProtectedRoutes) return Future.value(null);
  if (!kIsWeb &&
      !{
        TargetPlatform.android,
        TargetPlatform.iOS,
        TargetPlatform.macOS,
      }.contains(defaultTargetPlatform)) {
    return showDialog<BankCapturedCode>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Camera scanning unavailable'),
        content: const Text(
          'Use an authorized device with a supported camera. There is no manual barcode override.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }
  return Navigator.of(context).push<BankCapturedCode>(
    MaterialPageRoute(
      builder: (_) => BankCameraScreen(kind: kind, guard: guard),
    ),
  );
}

class BankCameraScreen extends StatefulWidget {
  const BankCameraScreen({super.key, required this.kind, required this.guard});
  final BankScanKind kind;
  final SessionNavigationGuard guard;
  @override
  State<BankCameraScreen> createState() => _BankCameraScreenState();
}

class _BankCameraScreenState extends State<BankCameraScreen>
    with WidgetsBindingObserver {
  final _scanner = MobileScannerController(
    autoStart: false,
    detectionSpeed: DetectionSpeed.noDuplicates,
  );
  late final int _revision;
  bool _closing = false;
  String? _error;
  bool get _current =>
      mounted &&
      !_closing &&
      widget.guard.canAccessProtectedRoutes &&
      widget.guard.revision == _revision;
  @override
  void initState() {
    super.initState();
    _revision = widget.guard.revision;
    widget.guard.addListener(_invalidate);
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_current) unawaited(_start());
    });
  }

  Future<void> _start() async {
    try {
      await _scanner.start();
    } on Object {
      if (_current) {
        setState(() => _error = 'Camera unavailable. Check device permission.');
      }
    }
  }

  Future<void> _stop() async {
    try {
      await _scanner.stop();
    } on Object {
      /* no clinical inference */
    }
  }

  void _invalidate() {
    if (!_current && !_closing && mounted) {
      _closing = true;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && ModalRoute.of(context)?.isCurrent == true) {
          Navigator.pop(context);
        }
      });
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (!_current || !_scanner.value.hasCameraPermission) return;
    if (state == AppLifecycleState.resumed) {
      unawaited(_start());
    } else {
      unawaited(_stop());
    }
  }

  void _detected(BarcodeCapture capture) {
    if (!_current) return;
    if (capture.barcodes.length != 1) {
      setState(() => _error = 'Present exactly one barcode.');
      return;
    }
    final code = capture.barcodes.single.rawValue;
    if (code == null ||
        code.isEmpty ||
        code.trim() != code ||
        code.length > 128) {
      setState(
        () => _error = 'Barcode unreadable. Scan the printed code again.',
      );
      return;
    }
    _closing = true;
    Navigator.pop(context, BankCapturedCode(code, DateTime.now().toUtc()));
  }

  @override
  void dispose() {
    _closing = true;
    widget.guard.removeListener(_invalidate);
    WidgetsBinding.instance.removeObserver(this);
    unawaited(_scanner.dispose());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final label = widget.kind == BankScanKind.wristband
        ? 'patient wristband'
        : 'physical blood component';
    return Scaffold(
      appBar: AppBar(title: Text('Scan $label')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(
              'Scan one $label barcode. Camera capture alone does not authorize transfusion.',
            ),
          ),
          if (_error != null) Text(_error!),
          Expanded(
            child: MobileScanner(controller: _scanner, onDetect: _detected),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel scan'),
          ),
        ],
      ),
    );
  }
}
