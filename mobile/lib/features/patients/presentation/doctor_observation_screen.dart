import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/patients/data/doctor_observation_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_allergy.dart';
import 'doctor_clinical_records_screen.dart';

/// Patient-chart entry. Never mounts an editable form from a stale patient
/// profile or emergency read grant. The source rechecks live Doctor scope.
class DoctorObservationEntry extends StatefulWidget {
  const DoctorObservationEntry({super.key, required this.source,
    required this.patientId, required this.clinicId});
  final DoctorObservationSource source;
  final int patientId;
  final int clinicId;

  @override
  State<DoctorObservationEntry> createState() => _DoctorObservationEntryState();
}

class _DoctorObservationEntryState extends State<DoctorObservationEntry> {
  bool _allowed = false;
  bool _opening = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.reader.sessionNavigationGuard.addListener(_changed);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant DoctorObservationEntry oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source) ||
        oldWidget.patientId != widget.patientId || oldWidget.clinicId != widget.clinicId) {
      oldWidget.source.reader.sessionNavigationGuard.removeListener(_changed);
      widget.source.reader.sessionNavigationGuard.addListener(_changed);
      _changed();
    }
  }

  void _changed() {
    ++_generation;
    if (!mounted) {
      return;
    }
    setState(() { _allowed = false; _opening = false; });
    unawaited(_load());
  }

  Future<void> _load() async {
    final generation = ++_generation;
    final allowed = await widget.source.canOffer(widget.clinicId);
    if (mounted && generation == _generation) {
      setState(() => _allowed = allowed);
    }
  }

  Future<void> _open() async {
    if (!_allowed || _opening) {
      return;
    }
    final generation = ++_generation;
    final source = widget.source;
    final patient = widget.patientId;
    setState(() => _opening = true);
    try {
      final review = await source.inspect(patient);
      if (mounted && generation == _generation && identical(source, widget.source) &&
          review.clinicId == widget.clinicId && patient == widget.patientId &&
          source.current(review.revision)) {
        await Navigator.of(context).push<void>(MaterialPageRoute<void>(
          builder: (_) => DoctorObservationScreen(source: source, review: review),
        ));
      }
    } on Object {
      if (mounted && generation == _generation) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('Patient observation access could not be verified.'),
        ));
      }
    } finally {
      if (mounted && generation == _generation) {
        setState(() => _opening = false);
      }
    }
  }

  @override
  void dispose() {
    ++_generation;
    widget.source.reader.sessionNavigationGuard.removeListener(_changed);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_allowed) {
      return const SizedBox.shrink();
    }
    return ListTile(
      key: const ValueKey('doctor-observations-entry'),
      leading: const Icon(Icons.fact_check_outlined),
      title: const Text('Doctor patient documentation'),
      subtitle: const Text('Vitals, structured allergies, discharge and amendment history.'),
      onTap: _opening ? null : _open,
    );
  }
}

class DoctorObservationScreen extends StatefulWidget {
  const DoctorObservationScreen({super.key, required this.source, required this.review});
  final DoctorObservationSource source;
  final DoctorObservationReview review;

  @override
  State<DoctorObservationScreen> createState() => _DoctorObservationScreenState();
}

class _DoctorObservationScreenState extends State<DoctorObservationScreen> {
  final _measurements = <String, TextEditingController>{
    for (final field in DoctorObservationSource.vitalFields.keys) field: TextEditingController(),
  };
  final _allergyName = TextEditingController();
  final _allergyCode = TextEditingController();
  final _reaction = TextEditingController();
  final _notes = TextEditingController();
  final _onset = TextEditingController();
  String _type = 'other';
  String _severity = 'unknown';
  bool _vitals = true;
  bool _busy = false;
  bool _uncertain = false;
  bool _locked = false;
  String? _message;
  PatientAllergyPage? _page;
  PatientAllergy? _detail;
  PatientAllergy? _editing;
  final _reason = TextEditingController();
  String _clinical = '';
  String _verification = '';
  String _intent() => List.generate(20, (_) => _random.nextInt(256).toRadixString(16).padLeft(2, '0')).join();
  void _beginEdit(PatientAllergy item) {
    setState(() {
      _editing = item;
      _allergyName.text = item.allergenName;
      _allergyCode.text = item.allergenCode ?? '';
      _reaction.text = item.reaction ?? '';
      _notes.text = item.notes ?? '';
      _onset.text = item.onsetAt?.toUtc().toIso8601String() ?? '';
      _type = item.allergenType.name;
      _severity = item.severity == PatientAllergySeverity.lifeThreatening ? 'life_threatening' : item.severity.name;
      _message = null;
    });
  }
  void _cancelEdit() {
    _clear();
    setState(() { _editing = null; _type = 'other'; _severity = 'unknown'; });
  }
  Future<void> _transition(PatientAllergy row) async {
    if (_busy || _locked || _uncertain || _editing != null) {
      return;
    }
    final clinical = _clinical.isEmpty ? null : _clinical;
    final verification = _verification.isEmpty ? null : _verification;
    final reason = _reason.text.trim();
    if (reason.isEmpty || (clinical == null && verification == null)) {
      setState(() => _message = 'Choose a status and document the reason.');
      return;
    }
    final generation = ++_generation;
    setState(() { _busy = true; _message = null; });
    final accepted = await showDialog<bool>(context: context, builder: (ctx) => AlertDialog(
      title: const Text('Confirm allergy status review'),
      content: Text('Clinical status: ${clinical ?? 'unchanged'}. Verification: ${verification ?? 'unchanged'}. The backend enforces the final status.'),
      actions: [TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Confirm transition'))],
    ));
    if (!_current(generation)) {
      return;
    }
    if (accepted != true) {
      setState(() => _busy = false);
      return;
    }
    try {
      final result = await widget.source.transitionAllergy(widget.review, row,
        intentId: _intent(), reason: reason, clinicalStatus: clinical, verificationStatus: verification);
      if (_current(generation)) {
        _clear();
        setState(() { _detail = result; _page = null; _clinical = ''; _verification = ''; _message = 'Server confirmed allergy status.'; });
      }
    } on DoctorObservationConflict {
      if (_current(generation)) {
        _clear();
        setState(() { _detail = null; _page = null; _clinical = ''; _verification = ''; _message = 'Conflict: reload and review current allergy before a new decision.'; });
      }
    } on DoctorObservationUnconfirmed {
      if (_current(generation)) {
        setState(() { _uncertain = true; _detail = null; _page = null; _message = 'Outcome unconfirmed. Reconcile server records; do not retry.'; });
      }
    } on Object {
      if (_current(generation)) {
        setState(() => _message = 'Status review unavailable.');
      }
    } finally {
      if (_current(generation)) {
        setState(() => _busy = false);
      }
    }
  }

  int _generation = 0;
  final _random = Random.secure();

  @override
  void initState() {
    super.initState();
    widget.source.reader.sessionNavigationGuard.addListener(_invalidate);
    _locked = !widget.source.current(widget.review.revision);
  }

  void _clear() {
    for (final controller in [..._measurements.values, _allergyName, _allergyCode,
      _reaction, _notes, _onset, _reason]) {
      controller.clear();
    }
  }

  void _invalidate() {
    if (widget.source.current(widget.review.revision) || !mounted) {
      return;
    }
    ++_generation;
    _clear();
    setState(() {
      _locked = true;
      _page = null;
      _detail = null;
      _editing = null;
      _message = null;
      _busy = false;
    });
  }

  @override
  void dispose() {
    ++_generation;
    widget.source.reader.sessionNavigationGuard.removeListener(_invalidate);
    for (final controller in [..._measurements.values, _allergyName, _allergyCode,
      _reaction, _notes, _onset, _reason]) {
      controller.dispose();
    }
    super.dispose();
  }

  bool _current(int generation) => mounted && generation == _generation &&
      !_locked && widget.source.current(widget.review.revision);

  Future<void> _loadAllergies({int page = 1}) async {
    if (_busy || _locked) {
      return;
    }
    final generation = ++_generation;
    setState(() { _busy = true; _page = null; _detail = null; _message = null; });
    try {
      final data = await widget.source.allergies(widget.review, page: page);
      if (_current(generation)) {
        setState(() => _page = data);
      }
    } on Object {
      if (_current(generation)) {
        setState(() => _message = 'Allergy records could not be verified.');
      }
    } finally {
      if (_current(generation)) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _readDetail(PatientAllergy row) async {
    if (_busy || _locked) {
      return;
    }
    final generation = ++_generation;
    _reason.clear();
    setState(() { _busy = true; _detail = null; _clinical = ''; _verification = ''; });
    try {
      final result = await widget.source.detail(widget.review, row.id);
      if (_current(generation)) {
        setState(() => _detail = result);
      }
    } on Object {
      if (_current(generation)) {
        setState(() => _message = 'Allergy details could not be verified.');
      }
    } finally {
      if (_current(generation)) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _submit() async {
    if (_busy || _locked || _uncertain) {
      return;
    }
    final generation = ++_generation;
    final vitalMode = _vitals;
    final editing = _editing;
    final measurements = {for (final e in _measurements.entries) e.key: e.value.text};
    final name = _allergyName.text;
    final code = _allergyCode.text;
    final reaction = _reaction.text;
    final notes = _notes.text;
    final onset = _onset.text;
    final type = _type;
    final severity = _severity;
    try {
      if (vitalMode) {
        DoctorObservationSource.vitalDraft(measurements);
      } else {
        DoctorObservationSource.allergyDraft(name: name, type: type,
          severity: severity, code: code, reaction: reaction, notes: notes, onset: onset);
      }
    } on FormatException {
      setState(() => _message = 'Review the required fields and measurement format.');
      return;
    }
    // Disable the entire form during review and dispatch; the snapshot is
    // immutable for this intent. No automatic retry after any dispatch.
    setState(() { _busy = true; _message = null; });
    final accepted = await showDialog<bool>(context: context, builder: (ctx) => AlertDialog(
      title: Text(vitalMode ? 'Record measured vitals?' : editing == null ? 'Record a new allergy?' : 'Save reviewed allergy changes?'),
      content: Text('Confirm the selected patient and entered observations. '
        'The backend will validate and save the record. No clinical clearance is implied.'),
      actions: [TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Submit to server'))],
    ));
    if (!_current(generation)) {
      return;
    }
    if (accepted != true) {
      setState(() => _busy = false);
      return;
    }
    final intent = _intent();
    try {
      final id = vitalMode
        ? (await widget.source.recordVitals(widget.review, intentId: intent, measurements: measurements)).id
        : editing != null
        ? (await widget.source.editAllergy(widget.review, editing, intentId: intent,
            name: name, type: type, severity: severity, code: code,
            reaction: reaction, notes: notes, onset: onset)).id
        : (await widget.source.createAllergy(widget.review, intentId: intent,
            name: name, type: type, severity: severity, code: code,
            reaction: reaction, notes: notes, onset: onset)).id;
      if (_current(generation)) {
        _clear();
        setState(() {
          _page = null; _detail = null; _editing = null;
          _message = 'Server confirmed record #$id. Refresh the patient chart to review it.';
        });
      }
    } on DoctorObservationConflict {
      if (_current(generation)) {
        _clear();
        setState(() { _editing = null; _detail = null; _page = null; _message = 'Conflict: reload and review the current server record. No retry was sent.'; });
      }
    } on DoctorObservationUnconfirmed {
      if (_current(generation)) {
        setState(() {
          _uncertain = true;
          _message = 'The server outcome is uncertain. Do not submit again. '
            'Return to the patient chart and reconcile the server records.';
        });
      }
    } on Object {
      if (_current(generation)) {
        setState(() => _message = 'Request unavailable. Verify the session and patient again.');
      }
    } finally {
      if (_current(generation)) {
        setState(() => _busy = false);
      }
    }
  }

  Widget _field(TextEditingController c, String label, {int? limit, bool numeric = false}) =>
    TextField(controller: c, enabled: !_busy && !_uncertain, maxLength: limit,
      keyboardType: numeric ? const TextInputType.numberWithOptions(decimal: true) : TextInputType.text,
      decoration: InputDecoration(labelText: label));

  @override
  Widget build(BuildContext context) {
    if (_locked || !widget.source.current(widget.review.revision)) {
      return const Scaffold(body: Center(child: Text('Patient observation session unavailable.')));
    }
    final page = _page;
    final detail = _detail;
    return Scaffold(appBar: AppBar(title: const Text('Patient observations')),
      body: SafeArea(child: ListView(padding: const EdgeInsets.all(16), children: [
        Text(widget.review.patientLabel, style: Theme.of(context).textTheme.titleLarge),
        Text('Patient #${widget.review.patientId} · Clinic #${widget.review.clinicId}'),
        const Text('Record actual measurements and documented observations. Do not use this screen to infer a diagnosis.'),
        OutlinedButton(onPressed: _busy || _editing != null ? null : () => Navigator.of(context).push<void>(MaterialPageRoute<void>(
          builder: (_) => DoctorClinicalRecordsScreen(source: widget.source, patient: widget.review))),
          child: const Text('Discharge and clinical record history')),
        const SizedBox(height: 12),
        SegmentedButton<bool>(segments: const [ButtonSegment(value: true, label: Text('Vitals')),
          ButtonSegment(value: false, label: Text('Allergies'))], selected: {_vitals},
          onSelectionChanged: _busy || _uncertain || _editing != null ? null : (values) => setState(() { _vitals = values.single; _message = null; })),
        if (_busy) const LinearProgressIndicator(),
        if (_vitals) ...[
          for (final e in _measurements.entries) _field(e.value, DoctorObservationSource.vitalFields[e.key]!, numeric: true),
        ] else ...[
          _field(_allergyName, 'Allergen name', limit: 255),
          _field(_allergyCode, 'Allergen code (optional)', limit: 64),
          DropdownButtonFormField<String>(key: ValueKey('doctor-type-$_type'), initialValue: _type,
            decoration: const InputDecoration(labelText: 'Allergen type'),
            items: DoctorObservationSource.allergyTypes.map((x) => DropdownMenuItem(value: x, child: Text(x))).toList(),
            onChanged: _busy || _uncertain ? null : (x) { if (x != null) { setState(() => _type = x); } }),
          DropdownButtonFormField<String>(key: ValueKey('doctor-severity-$_severity'), initialValue: _severity,
            decoration: const InputDecoration(labelText: 'Documented severity'),
            items: DoctorObservationSource.severities.map((x) => DropdownMenuItem(value: x, child: Text(x))).toList(),
            onChanged: _busy || _uncertain ? null : (x) { if (x != null) { setState(() => _severity = x); } }),
          _field(_reaction, 'Reaction (optional)', limit: 500),
          _field(_notes, 'Notes (optional)', limit: 1000),
          _field(_onset, 'Onset ISO date/time with timezone (optional)', limit: 50),
          if (_editing != null) ...[
            Text('Editing reviewed allergy #${_editing!.id}'),
            TextButton(onPressed: _busy || _uncertain ? null : _cancelEdit, child: const Text('Cancel edit')),
          ] else const Text('New allergies remain unconfirmed until verified by the authorized clinical workflow.'),
        ],
        const SizedBox(height: 12),
        FilledButton(key: const ValueKey('doctor-observation-submit'),
          onPressed: _busy || _uncertain ? null : _submit,
          child: Text(_vitals ? 'Review measured vitals' : _editing == null ? 'Review allergy record' : 'Review allergy edit')),
        if (_message != null) Text(_message!),
        if (!_vitals) ...[
          OutlinedButton(onPressed: _busy || _editing != null ? null : () => _loadAllergies(), child: const Text('Review server allergy records')),
          if (page != null) ...[
            for (final row in page.items) ListTile(title: Text(row.allergenName),
              subtitle: Text('${row.clinicalStatus.name} · ${row.verificationStatus.name}'),
              onTap: _busy || _editing != null ? null : () => _readDetail(row)),
            if (page.page > 1) TextButton(onPressed: _busy || _editing != null ? null : () => _loadAllergies(page: page.page - 1), child: const Text('Previous')),
            if (page.page * 20 < page.total) TextButton(onPressed: _busy || _editing != null ? null : () => _loadAllergies(page: page.page + 1), child: const Text('Next')),
          ],
          if (detail != null) Card(child: Padding(padding: const EdgeInsets.all(12), child: Column(
            crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Allergy #${detail.id} · ${detail.allergenName}'),
              Text('Type: ${detail.allergenType.name} · Severity: ${detail.severity.name}'),
              Text('Clinical status: ${detail.clinicalStatus.name} · Verification: ${detail.verificationStatus.name}'),
              if (detail.reaction != null) Text('Reaction: ${detail.reaction}'),
              if (detail.notes != null) Text('Notes: ${detail.notes}'),
              Text('Recorded: ${detail.recordedAt.toLocal()}'),
              Text('Reviewed revision: ${detail.updatedAt.toUtc().toIso8601String()}'),
              if (!_uncertain && _editing == null) ...[
                OutlinedButton(onPressed: _busy ? null : () => _beginEdit(detail), child: const Text('Edit reviewed allergy')),
                DropdownButtonFormField<String>(key: ValueKey('doctor-clinical-$_clinical'), initialValue: _clinical,
                  decoration: const InputDecoration(labelText: 'Clinical status'),
                  items: ['', 'active', 'inactive', 'resolved'].map((x) => DropdownMenuItem(value: x, child: Text(x.isEmpty ? 'Unchanged' : x))).toList(),
                  onChanged: _busy ? null : (x) { if (x != null) { setState(() => _clinical = x); } }),
                DropdownButtonFormField<String>(key: ValueKey('doctor-verification-$_verification'), initialValue: _verification,
                  decoration: const InputDecoration(labelText: 'Verification status'),
                  items: ['', 'unconfirmed', 'confirmed', 'refuted'].map((x) => DropdownMenuItem(value: x, child: Text(x.isEmpty ? 'Unchanged' : x))).toList(),
                  onChanged: _busy ? null : (x) { if (x != null) { setState(() => _verification = x); } }),
                _field(_reason, 'Reason for status change', limit: 500),
                FilledButton(onPressed: _busy ? null : () => _transition(detail), child: const Text('Review status change')),
              ],
            ]))),
        ],
      ])));
  }
}
