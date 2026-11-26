import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart' show kDebugMode, kIsWeb, defaultTargetPlatform, TargetPlatform;
import 'package:http/http.dart' as http;
import 'package:flutter/material.dart';
import 'package:printing/printing.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import '../data/clinic_print_service.dart';

/// Staged device verification ONLY. The backend feature gate remains final.
/// Release builds cannot opt in through a Dart define.
const clinicPrintStaged = kDebugMode && !kIsWeb &&
    bool.fromEnvironment('CSP_STAGED_PRINTING', defaultValue: false);

class NativeClinicPrintDevice implements ClinicPrintDevice {
  const NativeClinicPrintDevice();
  @override
  Future<bool> available() async {
    if (kIsWeb || !const {TargetPlatform.windows, TargetPlatform.android, TargetPlatform.iOS}.contains(defaultTargetPlatform)) {
      return false;
    }
    return (await Printing.info()).canPrint;
  }
  @override
  Future<bool> present(Future<Uint8List> Function() layout) async =>
      Printing.layoutPdf(name: 'Clinic document copy', dynamicLayout: false,
        onLayout: (_) => layout());
}

/// Single explicit action, reusable from the staff workspace and patient copies.
class ClinicPrintAction extends StatefulWidget {
  const ClinicPrintAction({super.key, required this.api, required this.loadSession,
    required this.guard, required this.source, required this.recordId,
    this.enabled = true, this.device = const NativeClinicPrintDevice(), this.clientFactory});
  final ApiClient api;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard guard;
  final ClinicPrintSource source;
  final int recordId;
  final bool enabled;
  final ClinicPrintDevice device;
  final http.Client Function()? clientFactory;
  @override
  State<ClinicPrintAction> createState() => _ClinicPrintActionState();
}

class _ClinicPrintActionState extends State<ClinicPrintAction> {
  bool _busy = false;
  String? _notice;
  int _generation = 0;
  @override
  void initState() {
    super.initState();
    widget.guard.addListener(_invalidate);
  }
  @override
  void didUpdateWidget(covariant ClinicPrintAction oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.guard, widget.guard)) {
      oldWidget.guard.removeListener(_invalidate);
      widget.guard.addListener(_invalidate);
    }
    if (oldWidget.recordId != widget.recordId || oldWidget.source != widget.source ||
        !identical(oldWidget.guard, widget.guard) || !identical(oldWidget.api, widget.api) ||
        oldWidget.loadSession != widget.loadSession || oldWidget.enabled != widget.enabled ||
        oldWidget.device != widget.device || oldWidget.clientFactory != widget.clientFactory) {
      _generation++;
      _notice = null;
    }
  }
  void _invalidate() {
    _generation++;
    if (mounted) setState(() => _notice = null);
  }
  @override
  void dispose() {
    _generation++;
    widget.guard.removeListener(_invalidate);
    super.dispose();
  }
  Future<void> _run() async {
    if (_busy || !widget.enabled || !widget.guard.canAccessProtectedRoutes) return;
    final generation = ++_generation;
    final revision = widget.guard.revision;
    final guard = widget.guard;
    final source = widget.source;
    final id = widget.recordId;
    bool active() => mounted && generation == _generation && identical(guard, widget.guard) &&
        guard.revision == revision && guard.canAccessProtectedRoutes && widget.enabled &&
        widget.source == source && widget.recordId == id;
    setState(() { _busy = true; _notice = null; });
    try {
      final ok = await showDialog<bool>(context: context, builder: (dialogContext) => AlertDialog(
        title: const Text('Print current authorized copy?'),
        content: Text('${source.label}\nThe server will retrieve and audit the current document. Use only an authorized printer. The operating system may spool or retain a print job.'),
        actions: [TextButton(onPressed: () => Navigator.pop(dialogContext, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(dialogContext, true), child: const Text('Continue'))],
      ));
      if (ok != true || !active()) return;
      final service = ClinicPrintService(api: widget.api, loadSession: widget.loadSession,
        guard: guard, device: widget.device, clientFactory: widget.clientFactory);
      final result = await service.printCurrent(source: source, id: id, active: active);
      if (active()) setState(() => _notice = result ? 'Print dialog reported completion; physical output is not verified.' : 'Print dialog cancelled or not completed.');
    } on Object {
      if (active()) setState(() => _notice = 'Print unavailable or outcome unconfirmed. Review the device and server before trying again.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
    FilledButton.icon(key: const ValueKey('clinic-print-current'),
      onPressed: _busy || !widget.enabled || !widget.guard.canAccessProtectedRoutes || widget.recordId <= 0 ? null : () => unawaited(_run()),
      icon: const Icon(Icons.print_outlined), label: Text(_busy ? 'Print request in progress' : 'Print current server copy')),
    if (_notice != null) Text(_notice!),
  ]);
}
