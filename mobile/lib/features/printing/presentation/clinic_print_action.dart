import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart'
    show kDebugMode, kIsWeb, defaultTargetPlatform, TargetPlatform;
import 'package:http/http.dart' as http;
import 'package:flutter/material.dart';
import 'package:printing/printing.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';

import '../data/clinic_print_service.dart';

/// Staged device verification ONLY. The backend feature gate remains final.
/// Release builds cannot opt in through a Dart define.
const clinicPrintStaged =
    kDebugMode &&
    !kIsWeb &&
    bool.fromEnvironment('CSP_STAGED_PRINTING', defaultValue: false);

class NativeClinicPrintDevice implements ClinicPrintDevice {
  const NativeClinicPrintDevice();
  @override
  Future<bool> available() async {
    if (kIsWeb ||
        !const {
          TargetPlatform.windows,
          TargetPlatform.android,
          TargetPlatform.iOS,
        }.contains(defaultTargetPlatform)) {
      return false;
    }
    return (await Printing.info()).canPrint;
  }

  @override
  Future<bool> present(Future<Uint8List> Function() layout) async =>
      Printing.layoutPdf(
        name: 'Clinic document copy',
        dynamicLayout: false,
        onLayout: (_) => layout(),
      );
}

/// Preview the very same guarded server bytes; no document synthesis or
/// sharing/printing actions in the preview. Native platform proof is Run 4.
class NativeClinicPreviewDevice implements ClinicPrintDevice {
  const NativeClinicPreviewDevice(this.context, this.valid);
  final BuildContext context;
  final ValueNotifier<bool> valid;

  @override
  Future<bool> available() async {
    if (!clinicPrintStaged ||
        !const {
          TargetPlatform.windows,
          TargetPlatform.android,
          TargetPlatform.iOS,
        }.contains(defaultTargetPlatform)) {
      return false;
    }
    return (await Printing.info()).canRaster;
  }

  @override
  Future<bool> present(Future<Uint8List> Function() layout) async {
    if (!context.mounted || !valid.value) {
      return false;
    }
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => Dialog.fullscreen(
        child: Column(
          children: [
            ListTile(
              title: const Text('Current server PDF — read-only preview'),
              trailing: IconButton(
                tooltip: 'Close preview',
                icon: const Icon(Icons.close),
                onPressed: () => Navigator.of(dialogContext).pop(),
              ),
            ),
            Expanded(
              child: ValueListenableBuilder<bool>(
                valueListenable: valid,
                builder: (context, active, child) {
                  if (!active) {
                    return const Center(
                      child: Text(
                        'Preview closed: session or document changed.',
                      ),
                    );
                  }
                  return PdfPreview(
                    build: (_) async {
                      if (!valid.value) {
                        throw const ClinicPrintUnavailable();
                      }
                      final bytes = await layout();
                      if (!valid.value) {
                        bytes.fillRange(0, bytes.length, 0);
                        throw const ClinicPrintUnavailable();
                      }
                      return bytes;
                    },
                    allowPrinting: false,
                    allowSharing: false,
                    canChangePageFormat: false,
                    canChangeOrientation: false,
                    canDebug: false,
                    useActions: false,
                    dynamicLayout: false,
                    onError: (_, _) =>
                        const Center(child: Text('Preview unavailable.')),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
    return false; // A preview is never a successful print receipt.
  }
}

/// Single explicit action, reusable from the staff workspace and patient copies.
class ClinicPrintAction extends StatefulWidget {
  const ClinicPrintAction({
    super.key,
    required this.api,
    required this.loadSession,
    required this.guard,
    required this.source,
    required this.recordId,
    this.enabled = true,
    this.device = const NativeClinicPrintDevice(),
    this.clientFactory,
  });
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
  ValueNotifier<bool>? _previewValid;
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
    if (oldWidget.recordId != widget.recordId ||
        oldWidget.source != widget.source ||
        !identical(oldWidget.guard, widget.guard) ||
        !identical(oldWidget.api, widget.api) ||
        oldWidget.loadSession != widget.loadSession ||
        oldWidget.enabled != widget.enabled ||
        oldWidget.device != widget.device ||
        oldWidget.clientFactory != widget.clientFactory) {
      _generation++;
      _previewValid?.value = false;
      _notice = null;
    }
  }

  void _invalidate() {
    _generation++;
    _previewValid?.value = false;
    if (mounted) setState(() => _notice = null);
  }

  @override
  void dispose() {
    _generation++;
    _previewValid?.value = false;
    widget.guard.removeListener(_invalidate);
    super.dispose();
  }

  Future<void> _run({bool preview = false}) async {
    if (_busy ||
        !widget.enabled ||
        !widget.guard.canAccessProtectedRoutes ||
        (preview && !clinicPrintStaged)) {
      return;
    }
    final generation = ++_generation;
    final revision = widget.guard.revision;
    final guard = widget.guard;
    final source = widget.source;
    final id = widget.recordId;
    bool active() =>
        mounted &&
        generation == _generation &&
        identical(guard, widget.guard) &&
        guard.revision == revision &&
        guard.canAccessProtectedRoutes &&
        widget.enabled &&
        widget.source == source &&
        widget.recordId == id;
    final validity = preview ? ValueNotifier<bool>(true) : null;
    _previewValid = validity;
    setState(() {
      _busy = true;
      _notice = null;
    });
    try {
      final ok = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: Text(
            preview
                ? 'Preview current authorized copy?'
                : 'Print current authorized copy?',
          ),
          content: Text(
            '${source.label}\nThe server will retrieve and audit the current document. Use only an authorized printer. The operating system may spool or retain a print job.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(dialogContext, true),
              child: const Text('Continue'),
            ),
          ],
        ),
      );
      if (ok != true || !mounted || !active()) {
        return;
      }
      final service = ClinicPrintService(
        api: widget.api,
        loadSession: widget.loadSession,
        guard: guard,
        device: preview
            ? NativeClinicPreviewDevice(context, validity!)
            : widget.device,
        clientFactory: widget.clientFactory,
      );
      final result = await service.printCurrent(
        source: source,
        id: id,
        active: active,
      );
      if (active()) {
        setState(
          () => _notice = preview
              ? 'Preview closed; no print or export performed.'
              : result
              ? 'Print dialog reported completion; physical output is not verified.'
              : 'Print dialog cancelled or not completed.',
        );
      }
    } on Object {
      if (active()) {
        setState(
          () => _notice = 'Print unavailable or outcome unconfirmed. Review the device and server before trying again.',
        );
      }
    } finally {
      validity?.value = false;
      _previewValid = null;
      // The dialog can still be completing its exit animation; its notifier
      // remains owned by that short-lived route until it is collected.
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      FilledButton.icon(
        key: const ValueKey('clinic-print-current'),
        onPressed:
            _busy ||
                !widget.enabled ||
                !widget.guard.canAccessProtectedRoutes ||
                widget.recordId <= 0
            ? null
            : () => unawaited(_run()),
        icon: const Icon(Icons.print_outlined),
        label: Text(
          _busy ? 'Print request in progress' : 'Print current server copy',
        ),
      ),
      if (clinicPrintStaged)
        OutlinedButton.icon(
          key: const ValueKey('clinic-preview-current'),
          onPressed:
              _busy ||
                  !widget.enabled ||
                  !widget.guard.canAccessProtectedRoutes ||
                  widget.recordId <= 0
              ? null
              : () => unawaited(_run(preview: true)),
          icon: const Icon(Icons.picture_as_pdf_outlined),
          label: const Text('Preview current server PDF'),
        ),
      if (_notice != null) Text(_notice!),
    ],
  );
}
