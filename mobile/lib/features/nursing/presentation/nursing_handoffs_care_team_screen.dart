import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/nursing/data/models/nursing_handoff_care_team.dart';
import 'package:clinic_system_pro/features/nursing/data/nursing_handoff_care_team_remote_data_source.dart';

/// P23E.F: admission-scoped handoffs + authoritative care team.
/// Server enforces outgoing/incoming nurse and admin-only assignment rights.
class NursingHandoffsCareTeamScreen extends StatefulWidget {
  const NursingHandoffsCareTeamScreen({super.key, required this.dataSource});

  final NursingHandoffCareTeamRemoteDataSource dataSource;

  @override
  State<NursingHandoffsCareTeamScreen> createState() =>
      _NursingHandoffsCareTeamScreenState();
}

class _NursingHandoffsCareTeamScreenState
    extends State<NursingHandoffsCareTeamScreen> {
  final _patient = TextEditingController();
  final _staff = TextEditingController();
  final _reason = TextEditingController();
  final _incoming = TextEditingController();
  final _start = TextEditingController();
  final _end = TextEditingController();
  final _situation = TextEditingController();
  final _background = TextEditingController();
  final _assessment = TextEditingController();
  final _recommendation = TextEditingController();
  final _safety = TextEditingController();
  final _pending = TextEditingController();

  NursingHandoffCareReview? _review;
  NursingHandoffEntry? _selected;
  PrimaryClinicianKind _assignmentKind = PrimaryClinicianKind.primaryNurse;
  HandoffSummaryCounts? _summary;
  bool _busy = false;
  String? _notice;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
  }

  @override
  void didUpdateWidget(covariant NursingHandoffsCareTeamScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(widget.dataSource, oldWidget.dataSource)) {
      oldWidget.dataSource.sessionNavigationGuard.removeListener(
        _onSessionChanged,
      );
      widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
      _clear();
    }
  }

  void _clearClinicalFields() {
    for (final field in <TextEditingController>[
      _staff,
      _reason,
      _incoming,
      _start,
      _end,
      _situation,
      _background,
      _assessment,
      _recommendation,
      _safety,
      _pending,
    ]) {
      field.clear();
    }
  }

  void _clear() {
    _generation++;
    _patient.clear();
    _clearClinicalFields();
    _assignmentKind = PrimaryClinicianKind.primaryNurse;
    _review = null;
    _selected = null;
    _summary = null;
    _busy = false;
    _notice = null;
  }

  void _onSessionChanged() {
    if (mounted) {
      setState(_clear);
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onSessionChanged);
    for (final field in <TextEditingController>[
      _patient,
      _staff,
      _reason,
      _incoming,
      _start,
      _end,
      _situation,
      _background,
      _assessment,
      _recommendation,
      _safety,
      _pending,
    ]) {
      field.dispose();
    }
    super.dispose();
  }

  bool _valid(int generation, int revision) =>
      mounted &&
      _generation == generation &&
      widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.dataSource.sessionNavigationGuard.revision == revision;

  void _patientChanged(String _) {
    setState(() {
      _clearClinicalFields();
      _generation++;
      _review = null;
      _selected = null;
      _summary = null;
      _notice = 'Patient changed. Review current admission again.';
    });
  }

  Future<void> _inspect() async {
    if (_busy || widget.dataSource.writeAttempted) {
      return;
    }
    final patientId = int.tryParse(_patient.text.trim());
    if (patientId == null || patientId <= 0 || patientId > 2147483647) {
      setState(() => _notice = 'Enter a valid patient ID.');
      return;
    }
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _notice = null;
      _review = null;
      _selected = null;
      _summary = null;
      _clearClinicalFields();
    });
    try {
      final review = await widget.dataSource.inspect(patientId: patientId);
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _review = review;
        _notice = review == null
            ? 'No active admission for this patient.'
            : null;
      });
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _notice = 'Care team or handoff data unavailable. No write sent.';
      });
    }
  }

  void _select(NursingHandoffEntry? handoff) {
    if (_busy || widget.dataSource.writeAttempted) {
      return;
    }
    setState(() {
      _selected = handoff;
      _summary = null;
      _notice = null;
      _incoming.text = handoff?.incomingStaffId?.toString() ?? '';
      _start.text = handoff?.shiftStartAt.toLocal().toIso8601String() ?? '';
      _end.text = handoff?.shiftEndAt.toLocal().toIso8601String() ?? '';
      _situation.text = handoff?.situation ?? '';
      _background.text = handoff?.background ?? '';
      _assessment.text = handoff?.assessment ?? '';
      _recommendation.text = handoff?.recommendation ?? '';
      _safety.text = handoff?.safetyConcerns ?? '';
      _pending.text = handoff?.pendingActions ?? '';
    });
  }

  Future<void> _preview(NursingHandoffEntry entry) async {
    final review = _review;
    if (review == null || _busy) {
      return;
    }
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;
    setState(() => _busy = true);
    try {
      final summary = await widget.dataSource.summary(
        reviewed: review,
        selected: entry,
      );
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _summary = summary;
        _notice =
            'Live clinical summary counts loaded. Review chart and alerts.';
      });
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _notice = 'Clinical handoff summary unavailable; no write sent.';
      });
    }
  }

  Future<void> _perform(
    String action,
    Future<Object> Function(NursingHandoffCareReview) work, {
    required bool admin,
  }) async {
    final review = _review;
    if (_busy ||
        review == null ||
        widget.dataSource.writeAttempted ||
        !(admin ? review.mayTeamWrite : review.mayHandoffWrite)) {
      return;
    }
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;
    final confirm = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Confirm $action?'),
        content: Text(
          'Patient ${review.admission.patientId}, admission ${review.admission.id}. '
          'This is a single online clinical write with no automatic replay. '
          'Handoff acknowledgement can atomically transfer primary nurse '
          'responsibility and update case-chat membership. Confirm only '
          'after reviewing current clinical responsibility.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('p23e-f-confirm'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Confirm once'),
          ),
        ],
      ),
    );
    if (confirm != true ||
        !_valid(generation, revision) ||
        widget.dataSource.writeAttempted ||
        _busy) {
      return;
    }
    setState(() {
      _busy = true;
      _notice = null;
    });
    try {
      final result = await work(review);
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _review = null;
        _selected = null;
        _summary = null;
        _clearClinicalFields();
        _notice =
            'Server confirmed $action (${result.runtimeType}). '
            'Return to ward dashboard to start a new clinical action.';
      });
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _review = null;
        _selected = null;
        _summary = null;
        _clearClinicalFields();
        _notice = widget.dataSource.writeAttempted
            ? 'Write outcome unconfirmed. Do not repeat this operation. '
                  'Reconcile the handoff, primary assignment and case-chat '
                  'membership through fresh server reads.'
            : 'Clinical authorization or state changed before write. '
                  'Reopen this workflow and review again.';
      });
    }
  }

  Widget _clinicalField(
    String label,
    TextEditingController controller, {
    bool enabled = true,
    int lines = 1,
    String? keyName,
    ValueChanged<String>? onChanged,
  }) => TextField(
    key: keyName == null ? null : ValueKey(keyName),
    controller: controller,
    enabled: enabled,
    maxLines: lines,
    onChanged: onChanged ?? (_) => setState(() {}),
    decoration: InputDecoration(labelText: label),
  );

  @override
  Widget build(BuildContext context) {
    final available =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;
    final locked = widget.dataSource.writeAttempted;
    final review = _review;
    final selected = _selected;
    final nurse =
        available &&
        review != null &&
        review.mayHandoffWrite &&
        !_busy &&
        !locked;
    final admin =
        available && review != null && review.mayTeamWrite && !_busy && !locked;
    final canEdit = nurse && (selected == null || selected.mayEdit);
    final current = review?.careTeam.assignment(_assignmentKind);
    final staffId = int.tryParse(_staff.text.trim());
    final incomingId = int.tryParse(_incoming.text.trim());
    final startTime = DateTime.tryParse(_start.text.trim());
    final endTime = DateTime.tryParse(_end.text.trim());
    final handoffFormReady =
        incomingId != null &&
        incomingId > 0 &&
        startTime != null &&
        endTime != null &&
        endTime.isAfter(startTime) &&
        _situation.text.trim().isNotEmpty &&
        _assessment.text.trim().isNotEmpty &&
        _recommendation.text.trim().isNotEmpty;

    return Scaffold(
      appBar: AppBar(title: const Text('Nursing handoffs & care team')),
      body: !available
          ? const Center(
              key: ValueKey('p23e-f-unavailable'),
              child: Text('Nursing session unavailable.'),
            )
          : ListView(
              key: const ValueKey('p23e-f-screen'),
              padding: const EdgeInsets.all(16),
              children: [
                const Text(
                  'Admission-scoped clinical handoffs and primary responsibility',
                ),
                _clinicalField(
                  'Patient ID',
                  _patient,
                  keyName: 'p23e-f-patient',
                  onChanged: _patientChanged,
                ),
                FilledButton(
                  key: const ValueKey('p23e-f-load'),
                  onPressed: _busy || locked ? null : _inspect,
                  child: const Text('Load active admission'),
                ),
                if (_busy) const LinearProgressIndicator(),
                if (_notice != null)
                  Text(_notice!, key: const ValueKey('p23e-f-notice')),
                if (locked)
                  const Text(
                    'One clinical mutation attempted. Further writes disabled.',
                  ),
                if (review != null) ...[
                  const Divider(),
                  Text(
                    'Admission ${review.admission.id} · patient ${review.admission.patientId}',
                  ),
                  const Text('Authoritative primary care team'),
                  Text(
                    'Doctor: ${review.careTeam.primaryDoctor?.displayName ?? 'Unassigned'}',
                  ),
                  Text(
                    'Nurse: ${review.careTeam.primaryNurse?.displayName ?? 'Unassigned'}',
                  ),
                  Text(
                    'Both confirmed: ${review.careTeam.confirmed ? 'Yes' : 'No'}',
                  ),
                  Text(
                    'Assignment history: ${review.assignmentHistory.length} record(s)',
                  ),
                  for (final entry in review.assignmentHistory)
                    Text(
                      '${primaryKindWire(entry.kind)} · ${entry.displayName} · '
                      '${entry.current ? 'current' : 'ended'}',
                    ),
                  const Divider(),
                  const Text('Admin-only primary assignment management'),
                  DropdownButton<PrimaryClinicianKind>(
                    key: const ValueKey('p23e-f-kind'),
                    value: _assignmentKind,
                    onChanged: admin
                        ? (value) {
                            if (value != null) {
                              setState(() => _assignmentKind = value);
                            }
                          }
                        : null,
                    items: [
                      for (final kind in PrimaryClinicianKind.values)
                        DropdownMenuItem(
                          value: kind,
                          child: Text(primaryKindWire(kind)),
                        ),
                    ],
                  ),
                  Text('Current: ${current?.displayName ?? 'Unassigned'}'),
                  _clinicalField(
                    'Verified staff ID',
                    _staff,
                    enabled: admin,
                    keyName: 'p23e-f-staff',
                  ),
                  _clinicalField(
                    'Assignment/end reason',
                    _reason,
                    enabled: admin,
                    keyName: 'p23e-f-reason',
                  ),
                  FilledButton(
                    key: const ValueKey('p23e-f-assign'),
                    onPressed:
                        admin &&
                            staffId != null &&
                            staffId > 0 &&
                            _reason.text.trim().isNotEmpty
                        ? () => _perform(
                            'assign primary clinician',
                            (s) => widget.dataSource.assignPrimary(
                              reviewed: s,
                              kind: _assignmentKind,
                              staffId: int.parse(_staff.text.trim()),
                              reason: _reason.text,
                            ),
                            admin: true,
                          )
                        : null,
                    child: const Text('Assign / replace primary clinician'),
                  ),
                  OutlinedButton(
                    key: const ValueKey('p23e-f-end-assignment'),
                    onPressed:
                        admin &&
                            current != null &&
                            _reason.text.trim().isNotEmpty
                        ? () => _perform(
                            'end primary assignment',
                            (s) => widget.dataSource.endPrimary(
                              reviewed: s,
                              kind: _assignmentKind,
                              reason: _reason.text,
                            ),
                            admin: true,
                          )
                        : null,
                    child: const Text('End current primary assignment'),
                  ),
                  const Divider(),
                  const Text('Nursing shift handoffs'),
                  for (final handoff in review.handoffs)
                    ListTile(
                      key: ValueKey('p23e-f-handoff-${handoff.id}'),
                      title: Text(
                        'Handoff ${handoff.id} · ${handoff.status.name}',
                      ),
                      subtitle: Text(
                        'Incoming nurse ${handoff.incomingStaffId ?? 'unassigned'}',
                      ),
                      onTap: locked || _busy ? null : () => _select(handoff),
                    ),
                  OutlinedButton(
                    key: const ValueKey('p23e-f-new'),
                    onPressed: nurse ? () => _select(null) : null,
                    child: const Text('Prepare new handoff'),
                  ),
                  Text(
                    selected == null
                        ? 'New shift handoff'
                        : 'Handoff ${selected.id}',
                  ),
                  _clinicalField(
                    'Incoming nurse staff ID',
                    _incoming,
                    enabled: canEdit,
                    keyName: 'p23e-f-incoming',
                  ),
                  _clinicalField(
                    'Shift start ISO 8601',
                    _start,
                    enabled: canEdit,
                    keyName: 'p23e-f-shift-start',
                  ),
                  _clinicalField(
                    'Shift end ISO 8601',
                    _end,
                    enabled: canEdit,
                    keyName: 'p23e-f-shift-end',
                  ),
                  _clinicalField(
                    'Situation',
                    _situation,
                    enabled: canEdit,
                    lines: 2,
                    keyName: 'p23e-f-situation',
                  ),
                  _clinicalField(
                    'Background',
                    _background,
                    enabled: canEdit,
                    lines: 2,
                  ),
                  _clinicalField(
                    'Assessment',
                    _assessment,
                    enabled: canEdit,
                    lines: 2,
                    keyName: 'p23e-f-assessment',
                  ),
                  _clinicalField(
                    'Recommendation',
                    _recommendation,
                    enabled: canEdit,
                    lines: 2,
                    keyName: 'p23e-f-recommendation',
                  ),
                  _clinicalField(
                    'Safety concerns',
                    _safety,
                    enabled: canEdit,
                    lines: 2,
                  ),
                  _clinicalField(
                    'Pending actions',
                    _pending,
                    enabled: canEdit,
                    lines: 2,
                  ),
                  if (selected == null)
                    FilledButton(
                      key: const ValueKey('p23e-f-create'),
                      onPressed: canEdit && handoffFormReady
                          ? () => _perform(
                              'create handoff draft',
                              (s) => widget.dataSource.createHandoff(
                                reviewed: s,
                                incomingStaffId: int.parse(
                                  _incoming.text.trim(),
                                ),
                                shiftStart: DateTime.parse(_start.text.trim()),
                                shiftEnd: DateTime.parse(_end.text.trim()),
                                situation: _situation.text,
                                assessment: _assessment.text,
                                recommendation: _recommendation.text,
                                background: _background.text,
                                safetyConcerns: _safety.text,
                                pendingActions: _pending.text,
                              ),
                              admin: false,
                            )
                          : null,
                      child: const Text('Create handoff draft'),
                    ),
                  if (selected != null) ...[
                    OutlinedButton(
                      key: const ValueKey('p23e-f-summary'),
                      onPressed: _busy ? null : () => _preview(selected),
                      child: const Text('Review live handoff summary'),
                    ),
                    if (_summary != null && _summary!.handoffId == selected.id)
                      Text(
                        'Clinical summary: ${_summary!.criticalAlerts} critical alerts · '
                        '${_summary!.dueAdministrations} due administrations · '
                        '${_summary!.pendingLabOrders} pending labs · '
                        '${_summary!.tasks} tasks',
                      ),
                    if (selected.mayEdit)
                      OutlinedButton(
                        key: const ValueKey('p23e-f-edit'),
                        onPressed: nurse && handoffFormReady
                            ? () => _perform(
                                'edit handoff draft',
                                (s) => widget.dataSource.editHandoff(
                                  reviewed: s,
                                  selected: selected,
                                  incomingStaffId: int.parse(
                                    _incoming.text.trim(),
                                  ),
                                  situation: _situation.text,
                                  assessment: _assessment.text,
                                  recommendation: _recommendation.text,
                                  background: _background.text,
                                  safetyConcerns: _safety.text,
                                  pendingActions: _pending.text,
                                ),
                                admin: false,
                              )
                            : null,
                        child: const Text('Save handoff draft'),
                      ),
                    if (selected.mayReady)
                      FilledButton(
                        key: const ValueKey('p23e-f-ready'),
                        onPressed: nurse
                            ? () => _perform(
                                'mark handoff ready',
                                (s) => widget.dataSource.transitionHandoff(
                                  reviewed: s,
                                  selected: selected,
                                  action: HandoffLife.ready,
                                ),
                                admin: false,
                              )
                            : null,
                        child: const Text('Mark ready'),
                      ),
                    if (selected.mayAcknowledge)
                      FilledButton(
                        key: const ValueKey('p23e-f-ack'),
                        onPressed: nurse && _summary?.handoffId == selected.id
                            ? () => _perform(
                                'acknowledge and transfer primary nurse',
                                (s) => widget.dataSource.transitionHandoff(
                                  reviewed: s,
                                  selected: selected,
                                  action: HandoffLife.acknowledged,
                                ),
                                admin: false,
                              )
                            : null,
                        child: const Text('Acknowledge handoff'),
                      ),
                    if (selected.mayCancel)
                      OutlinedButton(
                        key: const ValueKey('p23e-f-cancel'),
                        onPressed: nurse
                            ? () => _perform(
                                'cancel handoff',
                                (s) => widget.dataSource.transitionHandoff(
                                  reviewed: s,
                                  selected: selected,
                                  action: HandoffLife.cancelled,
                                ),
                                admin: false,
                              )
                            : null,
                        child: const Text('Cancel handoff'),
                      ),
                  ],
                ],
              ],
            ),
    );
  }
}
