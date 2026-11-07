import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_clinical_note_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_consultation.dart';

/// One online clinical-note workflow. Never queues PHI or retries a write.
/// After any unconfirmed write, the whole editor remains locked. Re-enter
/// only after reviewing the latest consultation and its amendment history.
class ConsultationClinicalNoteScreen extends StatefulWidget {
  const ConsultationClinicalNoteScreen({
    super.key,
    required this.consultationDataSource,
    required this.patientId,
    required this.consultationId,
  });

  final ConsultationRemoteDataSource consultationDataSource;
  final int patientId;
  final int consultationId;

  @override
  State<ConsultationClinicalNoteScreen> createState() =>
      _ConsultationClinicalNoteScreenState();
}

class _ConsultationClinicalNoteScreenState
    extends State<ConsultationClinicalNoteScreen> {
  final _complaint = TextEditingController();
  final _symptoms = TextEditingController();
  final _diagnosis = TextEditingController();
  final _plan = TextEditingController();
  final _notes = TextEditingController();
  final _reason = TextEditingController();

  PatientConsultation? _record;
  List<ConsultationNoteAmendment>? _amendments;
  String? _role;
  String? _message;
  bool _loading = true;
  bool _busy = false;
  bool _uncertain = false;
  int _generation = 0;

  late final ConsultationClinicalNoteDataSource _source;
  SessionNavigationGuard get _guard =>
      widget.consultationDataSource.sessionNavigationGuard;

  @override
  void initState() {
    super.initState();
    _source = ConsultationClinicalNoteDataSource(
      consultations: widget.consultationDataSource,
    );
    _guard.addListener(_onSessionChanged);
    unawaited(_load());
  }

  @override
  void dispose() {
    _generation++;
    _guard.removeListener(_onSessionChanged);
    for (final c in <TextEditingController>[
      _complaint,
      _symptoms,
      _diagnosis,
      _plan,
      _notes,
      _reason,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  void _clearFields() {
    for (final c in <TextEditingController>[
      _complaint,
      _symptoms,
      _diagnosis,
      _plan,
      _notes,
      _reason,
    ]) {
      c.clear();
    }
  }

  void _onSessionChanged() {
    _generation++;
    _clearFields();
    _record = null;
    _role = null;
    _message = null;
    _amendments = null;
    _busy = false;
    _uncertain = false;
    _loading = false;
    if (!mounted) {
      return;
    }
    setState(() {});
    if (_guard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  void _populate(PatientConsultation record) {
    _complaint.text = record.chiefComplaint ?? '';
    _symptoms.text = record.symptoms ?? '';
    _diagnosis.text = record.diagnosis ?? '';
    _plan.text = record.treatmentPlan ?? '';
    _notes.text = record.notes ?? '';
    _reason.clear();
  }

  Future<void> _load() async {
    if (_uncertain) {
      return;
    }
    final generation = ++_generation;
    final revision = _guard.revision;
    if (!mounted) {
      return;
    }
    setState(() {
      _loading = true;
      _record = null;
      _role = null;
      _amendments = null;
      _message = null;
    });
    try {
      final role = await _source.authorizedRole();
      final record = await _source.load(
        patientId: widget.patientId,
        consultationId: widget.consultationId,
      );
      final afterRole = await _source.authorizedRole();
      if (!mounted ||
          generation != _generation ||
          _guard.revision != revision ||
          !_guard.canAccessProtectedRoutes ||
          role != afterRole) {
        return;
      }
      _populate(record);
      setState(() {
        _record = record;
        _role = role;
        _loading = false;
      });
    } on Object {
      if (!mounted ||
          generation != _generation ||
          _guard.revision != revision) {
        return;
      }
      _clearFields();
      setState(() {
        _loading = false;
        _message = 'Clinical note unavailable for this session.';
      });
    }
  }

  Map<String, String>? _draftChanges(PatientConsultation record) {
    final candidates = <String, String>{
      'chief_complaint': _complaint.text.trim(),
      'symptoms': _symptoms.text.trim(),
      'diagnosis': _diagnosis.text.trim(),
      'treatment_plan': _plan.text.trim(),
      'notes': _notes.text.trim(),
    };
    final existing = <String, String?>{
      'chief_complaint': record.chiefComplaint,
      'symptoms': record.symptoms,
      'diagnosis': record.diagnosis,
      'treatment_plan': record.treatmentPlan,
      'notes': record.notes,
    };
    final changed = <String, String>{};
    for (final entry in candidates.entries) {
      if (entry.value != (existing[entry.key] ?? '').trim()) {
        // Clearing a field is not supported by the backend draft PATCH.
        if (entry.value.isEmpty) {
          return null;
        }
        changed[entry.key] = entry.value;
      }
    }
    return changed;
  }

  Map<String, String?> _document(PatientConsultation record) =>
      <String, String?>{
        'icd10_code': record.icd10Code,
        'chief_complaint': _nullIfEmpty(_complaint.text),
        'symptoms': _nullIfEmpty(_symptoms.text),
        'diagnosis': _nullIfEmpty(_diagnosis.text),
        'treatment_plan': _nullIfEmpty(_plan.text),
        'notes': _nullIfEmpty(_notes.text),
        'voice_note_url': record.voiceNoteUrl,
        'transcribed_text': record.transcribedText,
      };

  static String? _nullIfEmpty(String text) {
    final value = text.trim();
    return value.isEmpty ? null : value;
  }

  bool get _canAct =>
      _record != null &&
      _role != null &&
      !_loading &&
      !_busy &&
      !_uncertain &&
      _guard.canAccessProtectedRoutes;

  Future<void> _attempt(
    Future<PatientConsultation> Function(PatientConsultation record) action,
  ) async {
    final original = _record;
    if (!_canAct || original == null) {
      return;
    }
    final generation = _generation;
    final revision = _guard.revision;
    setState(() {
      _busy = true;
      _message = 'Submitting the clinical action once...';
    });
    try {
      final result = await action(original);
      if (!mounted ||
          generation != _generation ||
          revision != _guard.revision ||
          !_guard.canAccessProtectedRoutes) {
        return;
      }
      _populate(result);
      setState(() {
        _record = result;
        _amendments = null;
        _busy = false;
        _message = 'Clinical action confirmed by the backend.';
      });
    } on Object {
      if (!mounted ||
          generation != _generation ||
          revision != _guard.revision ||
          !_guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _busy = false;
        _uncertain = true;
        _message =
            'Outcome not confirmed. No retry was sent. Reopen the consultation '
            'and check its current record and amendment history before any further action.';
      });
    }
  }

  void _saveDraft() {
    final record = _record;
    if (!_canAct || record == null) {
      return;
    }
    final changes = _draftChanges(record);
    if (changes == null || changes.isEmpty) {
      setState(() {
        _message = 'Enter at least one changed, nonblank draft field.';
      });
      return;
    }
    unawaited(_attempt((current) => _source.saveDraft(current, changes)));
  }

  void _complete() {
    final diagnosis = _diagnosis.text.trim();
    if (!_canAct) {
      return;
    }
    if (diagnosis.isEmpty || diagnosis.length > 5000) {
      setState(
        () =>
            _message = 'A diagnosis is required to complete the consultation.',
      );
      return;
    }
    final plan = _nullIfEmpty(_plan.text);
    final notes = _nullIfEmpty(_notes.text);
    unawaited(
      _attempt(
        (current) => _source.complete(
          current,
          diagnosis: diagnosis,
          treatmentPlan: plan,
          notes: notes,
        ),
      ),
    );
  }

  void _cancel() {
    if (!_canAct) {
      return;
    }
    final reason = _nullIfEmpty(_reason.text);
    if (reason != null && reason.length > 500) {
      setState(() => _message = 'Cancellation reason is too long.');
      return;
    }
    unawaited(_attempt((current) => _source.cancel(current, reason: reason)));
  }

  void _finalize() {
    if (!_canAct || _role != 'doctor') {
      return;
    }
    unawaited(_attempt(_source.finalize));
  }

  void _amend() {
    final record = _record;
    if (!_canAct || record == null || _role != 'doctor') {
      return;
    }
    final reason = _reason.text.trim();
    if (reason.isEmpty || reason.length > 10000) {
      setState(() => _message = 'An amendment reason is required.');
      return;
    }
    final doc = _document(record);
    if (doc['chief_complaint'] == record.chiefComplaint &&
        doc['symptoms'] == record.symptoms &&
        doc['diagnosis'] == record.diagnosis &&
        doc['treatment_plan'] == record.treatmentPlan &&
        doc['notes'] == record.notes) {
      setState(
        () => _message = 'An amendment must change the clinical document.',
      );
      return;
    }
    unawaited(
      _attempt(
        (current) => _source.amend(current, reason: reason, document: doc),
      ),
    );
  }

  Future<void> _readAmendments() async {
    final record = _record;
    if (record == null || !_canAct) {
      return;
    }
    final revision = _guard.revision;
    final generation = _generation;
    setState(() => _busy = true);
    try {
      final items = await _source.loadAmendments(record);
      if (!mounted ||
          revision != _guard.revision ||
          generation != _generation ||
          !_guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _amendments = items;
        _busy = false;
      });
    } on Object {
      if (!mounted ||
          revision != _guard.revision ||
          generation != _generation) {
        return;
      }
      setState(() {
        _busy = false;
        _message = 'Amendment history unavailable.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final record = _record;
    final editable =
        record != null &&
        record.status != PatientConsultationStatus.cancelled &&
        (record.recordStatus == PatientClinicalRecordStatus.draft ||
            (_role == 'doctor' &&
                record.status == PatientConsultationStatus.completed));
    final draft =
        record != null &&
        record.recordStatus == PatientClinicalRecordStatus.draft;
    final inProgress = record?.status == PatientConsultationStatus.inProgress;
    final completed = record?.status == PatientConsultationStatus.completed;

    return Scaffold(
      appBar: AppBar(title: const Text('Clinical note workflow')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : record == null
          ? Center(
              child: Text(
                _message ?? 'Clinical note unavailable.',
                key: const ValueKey('p23c-note-unavailable'),
              ),
            )
          : ListView(
              key: const ValueKey('p23c-note-loaded'),
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Consultation #${record.id} · Patient #${record.patientId}',
                ),
                Text(
                  'Status: ${record.status.name}; Record: '
                  '${record.recordStatus.name} v${record.recordVersion}',
                ),
                const Text(
                  'Online-only. Writes are never queued or automatically retried.',
                ),
                for (final field
                    in <
                      ({String name, TextEditingController controller, int max})
                    >[
                      (
                        name: 'Chief complaint',
                        controller: _complaint,
                        max: 2000,
                      ),
                      (name: 'Symptoms', controller: _symptoms, max: 5000),
                      (name: 'Diagnosis', controller: _diagnosis, max: 5000),
                      (name: 'Treatment plan', controller: _plan, max: 5000),
                      (name: 'Notes', controller: _notes, max: 10000),
                    ])
                  TextField(
                    key: ValueKey('p23c-note-${field.name}'),
                    controller: field.controller,
                    decoration: InputDecoration(labelText: field.name),
                    minLines: 1,
                    maxLines: 3,
                    maxLength: field.max,
                    enabled: editable && _canAct,
                  ),
                TextField(
                  key: const ValueKey('p23c-note-reason'),
                  controller: _reason,
                  decoration: const InputDecoration(
                    labelText: 'Reason (cancellation or amendment)',
                  ),
                  maxLines: 2,
                  enabled: _canAct,
                ),
                const SizedBox(height: 12),
                if (draft)
                  ElevatedButton(
                    key: const ValueKey('p23c-note-save'),
                    onPressed:
                        _canAct &&
                            record.status != PatientConsultationStatus.cancelled
                        ? _saveDraft
                        : null,
                    child: const Text('Save draft'),
                  ),
                if (inProgress && draft)
                  ElevatedButton(
                    key: const ValueKey('p23c-note-complete'),
                    onPressed: _canAct ? _complete : null,
                    child: const Text('Complete consultation'),
                  ),
                if (inProgress && draft)
                  OutlinedButton(
                    key: const ValueKey('p23c-note-cancel'),
                    onPressed: _canAct ? _cancel : null,
                    child: const Text('Cancel consultation'),
                  ),
                if (completed && draft && _role == 'doctor')
                  ElevatedButton(
                    key: const ValueKey('p23c-note-finalize'),
                    onPressed: _canAct ? _finalize : null,
                    child: const Text('Finalize clinical note'),
                  ),
                if (completed && !draft && _role == 'doctor')
                  ElevatedButton(
                    key: const ValueKey('p23c-note-amend'),
                    onPressed: _canAct ? _amend : null,
                    child: const Text('Amend signed note'),
                  ),
                OutlinedButton(
                  key: const ValueKey('p23c-note-history'),
                  onPressed: _canAct
                      ? () => unawaited(_readAmendments())
                      : null,
                  child: const Text('Review amendments'),
                ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('p23c-note-message')),
                if (_uncertain)
                  const Text(
                    'Actions locked until you leave and verify the server record.',
                  ),
                if (_amendments != null)
                  for (final amendment in _amendments!)
                    ListTile(
                      title: Text(
                        'Version ${amendment.previousVersion} → '
                        '${amendment.newVersion}',
                      ),
                      subtitle: Text(amendment.reason),
                    ),
              ],
            ),
    );
  }
}
