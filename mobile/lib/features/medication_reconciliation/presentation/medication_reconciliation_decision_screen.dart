// P23G.S03.D — explicit, online-only medication reconciliation draft editor.
// This is an injectable screen, NOT a route. It never finalizes or queues writes.
import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/models/medication_reconciliation.dart';

class MedicationReconciliationDecisionScreen extends StatefulWidget {
  MedicationReconciliationDecisionScreen({
    super.key,
    required this.readSource,
    required this.mutationSource,
    required this.patientId,
    required this.admissionId,
    required this.event,
    this.wardTransferId,
    this.reconciliationId,
  }) : assert(patientId > 0),
       assert(admissionId > 0),
       assert(reconciliationId == null || reconciliationId > 0),
       assert(
         (event == MedicationReconciliationEvent.transfer) ==
             (wardTransferId != null),
       ),
       assert(wardTransferId == null || wardTransferId > 0),
       assert(
         identical(
           readSource.sessionNavigationGuard,
           mutationSource.sessionNavigationGuard,
         ),
       );

  final MedicationReconciliationReadRemoteDataSource readSource;
  final MedicationReconciliationMutationRemoteDataSource mutationSource;
  final int patientId;
  final int admissionId;
  final MedicationReconciliationEvent event;
  final int? wardTransferId;
  final int? reconciliationId;

  @override
  State<MedicationReconciliationDecisionScreen> createState() =>
      _MedicationReconciliationDecisionScreenState();
}

class _MedicationEntry {
  _MedicationEntry({MedicationReconciliationItem? from})
    : sourceId = from?.sourcePrescriptionItemId,
      name = TextEditingController(text: from?.medicationName ?? ''),
      decision = from?.decision ?? MedicationReconciliationDecision.verify,
      replacement = TextEditingController(
        text: from?.replacementMedicationName ?? '',
      ),
      dose = TextEditingController(text: from?.doseText ?? ''),
      route = TextEditingController(text: from?.routeText ?? ''),
      frequency = TextEditingController(text: from?.frequencyText ?? ''),
      instructions = TextEditingController(text: from?.instructions ?? ''),
      rationale = TextEditingController(text: from?.rationale ?? '');

  final int? sourceId;
  final TextEditingController name;
  MedicationReconciliationDecision decision;
  final TextEditingController replacement;
  final TextEditingController dose;
  final TextEditingController route;
  final TextEditingController frequency;
  final TextEditingController instructions;
  final TextEditingController rationale;

  MedicationReconciliationItem toItem() {
    String? optional(TextEditingController c) {
      final value = c.text.trim();
      return value.isEmpty ? null : value;
    }

    return MedicationReconciliationItem.fromJson(<String, dynamic>{
      'source_prescription_item_id': sourceId,
      'medication_name': name.text.trim(),
      'decision': switch (decision) {
        MedicationReconciliationDecision.continueMedication => 'continue',
        MedicationReconciliationDecision.hold => 'hold',
        MedicationReconciliationDecision.stop => 'stop',
        MedicationReconciliationDecision.replace => 'replace',
        MedicationReconciliationDecision.newMedication => 'new',
        MedicationReconciliationDecision.verify => 'verify',
        MedicationReconciliationDecision.unknown => 'unknown',
      },
      'replacement_medication_name': optional(replacement),
      'dose_text': optional(dose),
      'route_text': optional(route),
      'frequency_text': optional(frequency),
      'instructions': optional(instructions),
      'rationale': optional(rationale),
    });
  }

  void dispose() {
    name.dispose();
    replacement.dispose();
    dose.dispose();
    route.dispose();
    frequency.dispose();
    instructions.dispose();
    rationale.dispose();
  }
}

class _MedicationReconciliationDecisionScreenState
    extends State<MedicationReconciliationDecisionScreen> {
  final TextEditingController _notes = TextEditingController();
  final List<_MedicationEntry> _items = <_MedicationEntry>[];
  MedicationReconciliationRecord? _currentDraft;
  bool _noMedications = false;
  bool _loading = true;
  bool _saving = false;
  bool _dirty = false;
  bool _canWrite = false;
  bool _mustReopen = false;
  bool _requiresRecheck = false;
  bool _readOnlyRecord = false;
  bool _saved = false;
  String? _error;
  String? _validationError;
  int _generation = 0;

  bool get _sessionValid =>
      !_mustReopen &&
      widget.readSource.sessionNavigationGuard.canAccessProtectedRoutes;
  bool _fresh(int generation, int revision) =>
      mounted &&
      _generation == generation &&
      _sessionValid &&
      widget.readSource.sessionNavigationGuard.revision == revision;
  bool get _editable =>
      _sessionValid &&
      _canWrite &&
      !_loading &&
      !_saving &&
      !_requiresRecheck &&
      !_readOnlyRecord &&
      _error == null;

  @override
  void initState() {
    super.initState();
    widget.readSource.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant MedicationReconciliationDecisionScreen old) {
    super.didUpdateWidget(old);
    if (!identical(old.readSource, widget.readSource) ||
        !identical(old.mutationSource, widget.mutationSource) ||
        old.patientId != widget.patientId ||
        old.admissionId != widget.admissionId ||
        old.event != widget.event ||
        old.wardTransferId != widget.wardTransferId ||
        old.reconciliationId != widget.reconciliationId) {
      old.readSource.sessionNavigationGuard.removeListener(_sessionChanged);
      widget.readSource.sessionNavigationGuard.addListener(_sessionChanged);
      _generation++;
      _clearClinicalInputs();
      _mustReopen = false;
      _requiresRecheck = false;
      _saving = false;
      _loading = false;
      _error = null;
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.readSource.sessionNavigationGuard.removeListener(_sessionChanged);
    _clearClinicalInputs();
    _notes.dispose();
    super.dispose();
  }

  void _clearClinicalInputs() {
    for (final item in _items) {
      item.dispose();
    }
    _items.clear();
    _notes.clear();
    _currentDraft = null;
    _canWrite = false;
    _readOnlyRecord = false;
    _dirty = false;
    _saved = false;
    _noMedications = false;
    _validationError = null;
  }

  void _sessionChanged() {
    // A session transition permanently locks this instance, including any
    // in-flight save response. Never rehydrate a previous clinical document.
    _generation++;
    _clearClinicalInputs();
    _mustReopen = true;
    _saving = false;
    _loading = false;
    _error = null;
    if (mounted) {
      setState(() {});
    }
  }

  Future<void> _load() async {
    if (!_sessionValid || _saving) {
      return;
    }
    final generation = ++_generation;
    final revision = widget.readSource.sessionNavigationGuard.revision;
    setState(() {
      _clearClinicalInputs();
      _requiresRecheck = false;
      _error = null;
      _loading = true;
    });
    try {
      final state = await widget.mutationSource.loadSession();
      final session = state.session;
      if (!_fresh(generation, revision)) {
        return;
      }
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken != session.accessToken.trim() ||
          !const <String>{
            'doctor',
            'pharmacist',
          }.contains(session.role.trim().toLowerCase())) {
        throw const MedicationReconciliationMutationSessionException();
      }
      MedicationReconciliationRecord? record;
      if (widget.reconciliationId != null) {
        record = await widget.readSource.getById(
          reconciliationId: widget.reconciliationId!,
          expectedAdmissionId: widget.admissionId,
          expectedPatientId: widget.patientId,
        );
      } else {
        final page = await widget.readSource.listForAdmission(
          admissionId: widget.admissionId,
          expectedPatientId: widget.patientId,
          event: widget.event,
          page: 1,
          perPage: 100,
        );
        if (page.hasNext || page.total > 100) {
          throw StateError('Cannot establish unique reconciliation event.');
        }
        final matching = page.items
            .where(
              (item) =>
                  item.event == widget.event &&
                  item.wardTransferId == widget.wardTransferId,
            )
            .toList();
        if (matching.length > 1) {
          throw StateError('Ambiguous reconciliation event.');
        }
        if (matching.isNotEmpty) {
          record = await widget.readSource.getById(
            reconciliationId: matching.single.id,
            expectedAdmissionId: widget.admissionId,
            expectedPatientId: widget.patientId,
          );
        }
      }
      if (!_fresh(generation, revision)) {
        return;
      }
      if (record != null &&
          (record.event != widget.event ||
              record.wardTransferId != widget.wardTransferId ||
              (widget.reconciliationId != null &&
                  record.id != widget.reconciliationId))) {
        throw const FormatException('Reconciliation event identity mismatch.');
      }
      setState(() {
        _clearClinicalInputs();
        _canWrite = true;
        _currentDraft = record;
        _readOnlyRecord =
            record != null &&
            record.status != MedicationReconciliationRecordStatus.draft;
        _noMedications = record?.noMedicationsReported ?? false;
        _notes.text = record?.notes ?? '';
        for (final item in record?.items ?? <MedicationReconciliationItem>[]) {
          _items.add(_MedicationEntry(from: item));
        }
        _loading = false;
      });
    } on Object {
      if (!_fresh(generation, revision)) {
        return;
      }
      setState(() {
        _clearClinicalInputs();
        _loading = false;
        _error =
            'Cannot confirm authorized reconciliation context. '
            'Refresh the server record before editing.';
      });
    }
  }

  MedicationReconciliationDraftDocument _document() {
    if (!_noMedications && _items.isEmpty) {
      throw const FormatException(
        'Add a medication decision or explicitly record no medications.',
      );
    }
    final document = MedicationReconciliationDraftDocument(
      noMedicationsReported: _noMedications,
      notes: _notes.text.trim().isEmpty ? null : _notes.text.trim(),
      items: _items.map((item) => item.toItem()).toList(growable: false),
    );
    document.toJson();
    return document;
  }

  Future<void> _save() async {
    if (!_editable || !_dirty) {
      return;
    }
    final MedicationReconciliationDraftDocument document;
    try {
      document = _document();
    } on Object {
      setState(
        () => _validationError =
            'Invalid medication decision. '
            'Check medication names, replacement fields and text limits.',
      );
      return;
    }
    final generation = ++_generation;
    final revision = widget.readSource.sessionNavigationGuard.revision;
    setState(() {
      _saving = true;
      _saved = false;
      _error = null;
    });
    try {
      final existing = _currentDraft;
      final result = existing == null
          ? await widget.mutationSource.createDraft(
              admissionId: widget.admissionId,
              expectedPatientId: widget.patientId,
              event: widget.event,
              wardTransferId: widget.wardTransferId,
              document: document,
            )
          : await widget.mutationSource.updateDraft(
              current: existing,
              document: document,
            );
      if (!_fresh(generation, revision)) {
        return;
      }
      if (result.event != widget.event ||
          result.wardTransferId != widget.wardTransferId ||
          result.patientId != widget.patientId ||
          result.admissionId != widget.admissionId ||
          result.status != MedicationReconciliationRecordStatus.draft) {
        throw const MedicationReconciliationWriteRecheckRequiredException();
      }
      setState(() {
        _clearClinicalInputs();
        _canWrite = true;
        _currentDraft = result;
        _noMedications = result.noMedicationsReported;
        _notes.text = result.notes ?? '';
        for (final item in result.items) {
          _items.add(_MedicationEntry(from: item));
        }
        _saving = false;
        _saved = true;
      });
    } on Object {
      if (!_fresh(generation, revision)) {
        return;
      }
      setState(() {
        // Even an error response may represent an uncertain clinical write.
        // Discard the draft and force GET instead of replaying POST/PATCH.
        _clearClinicalInputs();
        _requiresRecheck = true;
        _saving = false;
        _error = null;
      });
    }
  }

  void _markDirty(String _) {
    if (_editable) {
      setState(() {
        _dirty = true;
        _saved = false;
        _validationError = null;
      });
    }
  }

  Widget _textField(
    String label,
    TextEditingController controller,
    String key,
    int maxLength, {
    bool enabled = true,
    int maxLines = 1,
  }) => TextField(
    key: ValueKey(key),
    controller: controller,
    maxLength: maxLength,
    maxLines: maxLines,
    enabled: _editable && enabled,
    onChanged: _markDirty,
    decoration: InputDecoration(
      labelText: label,
      border: const OutlineInputBorder(),
    ),
  );

  Widget _medicationRow(int index, _MedicationEntry item) => Card(
    key: ValueKey('p23g-s03d-row-$index'),
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Medication ${index + 1}',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          if (item.sourceId != null)
            Text('Linked prescription item #${item.sourceId}'),
          _textField(
            'Medication name',
            item.name,
            'p23g-s03d-medication-name-$index',
            255,
          ),
          const SizedBox(height: 8),
          DropdownButton<MedicationReconciliationDecision>(
            key: ValueKey('p23g-s03d-decision-$index'),
            isExpanded: true,
            value: item.decision,
            onChanged: !_editable
                ? null
                : (value) {
                    if (value == null) {
                      return;
                    }
                    setState(() {
                      item.decision = value;
                      if (value != MedicationReconciliationDecision.replace) {
                        item.replacement.clear();
                      }
                      _dirty = true;
                      _saved = false;
                    });
                  },
            items: const [
              DropdownMenuItem(
                value: MedicationReconciliationDecision.continueMedication,
                child: Text('Continue'),
              ),
              DropdownMenuItem(
                value: MedicationReconciliationDecision.hold,
                child: Text('Hold'),
              ),
              DropdownMenuItem(
                value: MedicationReconciliationDecision.stop,
                child: Text('Stop'),
              ),
              DropdownMenuItem(
                value: MedicationReconciliationDecision.replace,
                child: Text('Replace'),
              ),
              DropdownMenuItem(
                value: MedicationReconciliationDecision.newMedication,
                child: Text('New'),
              ),
              DropdownMenuItem(
                value: MedicationReconciliationDecision.verify,
                child: Text('Verify — unresolved'),
              ),
              DropdownMenuItem(
                value: MedicationReconciliationDecision.unknown,
                child: Text('Unknown — unresolved'),
              ),
            ],
          ),
          if (item.decision == MedicationReconciliationDecision.replace)
            _textField(
              'Replacement medication name',
              item.replacement,
              'p23g-s03d-replacement-$index',
              255,
            ),
          _textField(
            'Dose (documented text)',
            item.dose,
            'p23g-s03d-dose-$index',
            255,
          ),
          _textField(
            'Route (documented text)',
            item.route,
            'p23g-s03d-route-$index',
            128,
          ),
          _textField(
            'Frequency (documented text)',
            item.frequency,
            'p23g-s03d-frequency-$index',
            255,
          ),
          _textField(
            'Instructions',
            item.instructions,
            'p23g-s03d-instructions-$index',
            1000,
            maxLines: 2,
          ),
          _textField(
            'Clinical rationale',
            item.rationale,
            'p23g-s03d-rationale-$index',
            1000,
            maxLines: 2,
          ),
          Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              key: ValueKey('p23g-s03d-remove-$index'),
              onPressed: !_editable
                  ? null
                  : () => setState(() {
                      final removed = _items.removeAt(index);
                      removed.dispose();
                      _dirty = true;
                      _saved = false;
                    }),
              icon: const Icon(Icons.remove_circle_outline),
              label: const Text('Remove medication'),
            ),
          ),
        ],
      ),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final disabled = _mustReopen || !_sessionValid;
    return Scaffold(
      appBar: AppBar(title: const Text('Medication reconciliation draft')),
      body: disabled
          ? const Center(
              key: ValueKey('p23g-s03d-session-locked'),
              child: Text(
                'Session changed. Reopen from the authenticated admission.',
              ),
            )
          : ListView(
              key: const ValueKey('p23g-s03d-editor'),
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Patient #${widget.patientId} · Admission #${widget.admissionId}',
                ),
                Text(
                  'Event: ${widget.event.wireValue}'
                  '${widget.wardTransferId == null ? '' : ' · Transfer #${widget.wardTransferId}'}',
                ),
                const SizedBox(height: 8),
                const Text(
                  'Draft only. Values are clinician-documented, not automatically verified. '
                  'Saving does not finalize a reconciliation or place medication orders.',
                ),
                const SizedBox(height: 12),
                if (_loading)
                  const Center(
                    child: CircularProgressIndicator(
                      key: ValueKey('p23g-s03d-loading'),
                    ),
                  ),
                if (_error != null) ...[
                  Text(_error!, key: const ValueKey('p23g-s03d-error')),
                  TextButton(
                    key: const ValueKey('p23g-s03d-retry-read'),
                    onPressed: _saving ? null : () => unawaited(_load()),
                    child: const Text('Refresh server record'),
                  ),
                ],
                if (_requiresRecheck) ...[
                  const Text(
                    'Save outcome may be uncertain or draft has changed. '
                    'A fresh server read is required; the previous write will not be retried.',
                    key: ValueKey('p23g-s03d-recheck-required'),
                  ),
                  TextButton(
                    key: const ValueKey('p23g-s03d-recheck'),
                    onPressed: _saving ? null : () => unawaited(_load()),
                    child: const Text('Recheck server'),
                  ),
                ],
                if (_validationError != null)
                  Text(
                    _validationError!,
                    key: const ValueKey('p23g-s03d-validation-error'),
                  ),
                if (_readOnlyRecord)
                  const Text(
                    'This reconciliation is finalized or amended and cannot be edited as a draft.',
                    key: ValueKey('p23g-s03d-non-draft'),
                  ),
                if (_editable) ...[
                  if (_saved)
                    const Text(
                      'Draft saved and confirmed by server readback.',
                      key: ValueKey('p23g-s03d-saved'),
                    ),
                  if (_currentDraft != null)
                    Text(
                      'Draft #${_currentDraft!.id} · '
                      'CAS timestamp: ${_currentDraft!.updatedAt?.toIso8601String() ?? 'missing'}',
                    ),
                  CheckboxListTile(
                    key: const ValueKey('p23g-s03d-no-medications'),
                    title: const Text(
                      'No medications reported (clinical declaration)',
                    ),
                    subtitle: const Text(
                      'This does not verify a medication history.',
                    ),
                    value: _noMedications,
                    onChanged: _items.isNotEmpty
                        ? null
                        : (value) => setState(() {
                            _noMedications = value ?? false;
                            _dirty = true;
                            _saved = false;
                          }),
                  ),
                  _textField(
                    'Clinical notes',
                    _notes,
                    'p23g-s03d-notes',
                    10000,
                    maxLines: 3,
                  ),
                  for (var i = 0; i < _items.length; i++)
                    _medicationRow(i, _items[i]),
                  TextButton.icon(
                    key: const ValueKey('p23g-s03d-add-medication'),
                    onPressed: _noMedications || _items.length >= 500
                        ? null
                        : () => setState(() {
                            _items.add(_MedicationEntry());
                            _dirty = true;
                            _saved = false;
                          }),
                    icon: const Icon(Icons.add),
                    label: const Text('Add medication decision'),
                  ),
                  FilledButton(
                    key: const ValueKey('p23g-s03d-save'),
                    onPressed: !_dirty || _saving
                        ? null
                        : () => unawaited(_save()),
                    child: Text(
                      _currentDraft == null
                          ? 'Create draft'
                          : 'Save draft changes',
                    ),
                  ),
                ],
                if (_saving)
                  const Text(
                    'Saving once and verifying server readback...',
                    key: ValueKey('p23g-s03d-saving'),
                  ),
              ],
            ),
    );
  }
}
