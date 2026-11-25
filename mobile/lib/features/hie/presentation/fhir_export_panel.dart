import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/hie/data/fhir_export_preview.dart';
import 'package:clinic_system_pro/features/hie/data/hie_remote_data_source.dart';

/// Reuses the existing HIE source and session guard. The server owns export
/// approval; a Doctor role or patient route alone never grants that approval.
class FhirExportPanel extends StatefulWidget {
  const FhirExportPanel({
    super.key,
    required this.source,
    this.patientId,
    this.clinicId,
  }) : assert((patientId == null) == (clinicId == null));

  final HieRemoteDataSource source;
  final int? patientId, clinicId;

  @override
  State<FhirExportPanel> createState() => _FhirExportPanelState();
}

class _FhirExportPanelState extends State<FhirExportPanel> {
  final _id = TextEditingController();
  int _generation = 0;
  bool _doctor = false, _busy = false;
  String _section = 'all';
  FhirExportPreview? _preview;
  String? _message;

  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_invalidate);
    unawaited(_authorize());
  }

  @override
  void didUpdateWidget(covariant FhirExportPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source) ||
        oldWidget.patientId != widget.patientId ||
        oldWidget.clinicId != widget.clinicId) {
      oldWidget.source.guard.removeListener(_invalidate);
      widget.source.guard.addListener(_invalidate);
      _clear();
      unawaited(_authorize());
    }
  }

  void _clear() {
    _generation++;
    _doctor = false;
    _busy = false;
    _id.clear();
    _section = 'all';
    _preview = null;
    _message = null;
  }

  void _invalidate() {
    if (!mounted) {
      return;
    }
    setState(_clear);
    // Never rebind a fixed patient from the old chart after a scope change.
    if (widget.patientId == null) {
      unawaited(_authorize());
    }
  }

  bool _current(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.source.guard.canAccessProtectedRoutes &&
      revision == widget.source.guard.revision;

  Future<void> _authorize() async {
    final g = _generation;
    final rev = widget.source.guard.revision;
    try {
      final allowed = await widget.source.exportSessionReady(
        expectedClinicId: widget.clinicId,
      );
      if (_current(g, rev)) {
        setState(() => _doctor = allowed);
      }
    } on Object {
      // No new access or request when the session cannot be confirmed.
    }
  }

  Future<void> _load() async {
    if (!_doctor || _busy) {
      return;
    }
    final id = widget.patientId ?? int.tryParse(_id.text.trim());
    if (id == null || id <= 0 || id > 2147483647) {
      setState(() {
        _preview = null;
        _message = 'Enter a valid local patient ID.';
      });
      return;
    }
    final g = ++_generation;
    final rev = widget.source.guard.revision;
    final section = _section;
    setState(() {
      _busy = true;
      _preview = null;
      _message = null;
    });
    try {
      final result = await widget.source.exportPatient(
        patientId: id,
        section: section,
        expectedClinicId: widget.clinicId,
      );
      if (_current(g, rev)) {
        setState(() => _preview = result);
      }
    } on Object {
      if (_current(g, rev)) {
        setState(
          () => _message =
              'FHIR preview unavailable or not approved. No records returned.',
        );
      }
    } finally {
      if (_current(g, rev)) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.guard.removeListener(_invalidate);
    _id.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_doctor || !widget.source.guard.canAccessProtectedRoutes) {
      return const SizedBox.shrink();
    }
    final preview = _preview;
    return Card(
      key: const ValueKey('fhir-export-panel'),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'FHIR R4 local preview',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const Text(
              'Staged synthetic approval required. Online only; no external delivery, download or clipboard.',
            ),
            if (widget.patientId == null)
              TextField(
                key: const ValueKey('fhir-preview-patient-id'),
                controller: _id,
                enabled: !_busy,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'Local patient ID',
                ),
                onChanged: (_) => setState(() {
                  _preview = null;
                  _message = null;
                }),
              )
            else
              Text('Verified chart patient #${widget.patientId}'),
            DropdownButton<String>(
              key: const ValueKey('fhir-preview-section'),
              value: _section,
              isExpanded: true,
              items: [
                for (final section in fhirExportSections)
                  DropdownMenuItem(
                    value: section,
                    child: Text(
                      section == 'all' ? 'All permitted resources' : section,
                    ),
                  ),
              ],
              onChanged: _busy
                  ? null
                  : (section) {
                      if (section != null) {
                        setState(() {
                          _section = section;
                          _preview = null;
                          _message = null;
                        });
                      }
                    },
            ),
            OutlinedButton(
              key: const ValueKey('fhir-preview-request'),
              onPressed: _busy ? null : () => unawaited(_load()),
              child: const Text('Request approved local preview'),
            ),
            if (_busy) const LinearProgressIndicator(),
            if (_message != null)
              Text(_message!, key: const ValueKey('fhir-preview-status')),
            if (preview != null) ...[
              Text(
                'Patient #${preview.patientId} · ${preview.rows.length} validated resource(s)',
                key: const ValueKey('fhir-preview-count'),
              ),
              const Text(
                'Selected fields only. This preview is not a complete clinical chart.',
              ),
              for (final row in preview.rows.take(20))
                ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  title: Text('${row.resourceType} / ${row.id}'),
                  subtitle: row.description.isEmpty
                      ? null
                      : Text(row.description),
                ),
              if (preview.rows.length > 20)
                const Text(
                  'Showing first 20 resource summaries. Choose a narrower section.',
                ),
              TextButton(
                onPressed: () => setState(() => _preview = null),
                child: const Text('Clear preview'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
