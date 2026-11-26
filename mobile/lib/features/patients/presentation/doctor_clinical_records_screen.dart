import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/patients/data/doctor_observation_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/imaging_procedure_order.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/models/medication_reconciliation.dart';

class DoctorClinicalRecordsScreen extends StatefulWidget {
  const DoctorClinicalRecordsScreen({super.key, required this.source, required this.patient});
  final DoctorObservationSource source;
  final DoctorObservationReview patient;

  @override
  State<DoctorClinicalRecordsScreen> createState() => _DoctorClinicalRecordsScreenState();
}

class _DoctorClinicalRecordsScreenState extends State<DoctorClinicalRecordsScreen> {
  PatientAdmissionPage? _admissions;
  DoctorDischargeReview? _review;
  EmarAdministrationPage? _emar;
  List<ImagingProcedureOrder>? _surgical;
  MedicationReconciliationPage? _rec;
  List<Map<String, dynamic>>? _history;
  String _historyLabel = '';
  String? _error;
  bool _busy = false;
  bool _locked = false;
  bool _uncertain = false;
  int _generation = 0;
  final _random = Random.secure();
  final _fields = <String, TextEditingController>{
    for (final key in DoctorClinicalRecords.dischargeFields.keys) key: TextEditingController(),
  };
  final _secondary = TextEditingController();
  final _reason = TextEditingController();
  static const _labels = <String, String>{'primary_diagnosis': 'Primary diagnosis',
    'condition_at_discharge': 'Condition at discharge', 'instructions': 'Discharge instructions',
    'follow_up_plan': 'Follow-up plan', 'destination': 'Discharge destination'};

  @override
  void initState() {
    super.initState();
    widget.source.reader.sessionNavigationGuard.addListener(_invalidate);
    if (widget.source.current(widget.patient.revision)) {
      unawaited(_load());
    } else {
      _locked = true;
    }
  }

  void _clearForm() {
    for (final controller in [..._fields.values, _secondary, _reason]) {
      controller.clear();
    }
  }

  void _clearRecord() {
    _clearForm();
    _review = null;
    _emar = null;
    _surgical = null;
    _rec = null;
    _history = null;
    _historyLabel = '';
  }

  void _invalidate() {
    if (!mounted || widget.source.current(widget.patient.revision)) {
      return;
    }
    ++_generation;
    setState(() { _locked = true; _busy = false; _admissions = null; _clearRecord(); _error = null; });
  }

  @override
  void dispose() {
    ++_generation;
    widget.source.reader.sessionNavigationGuard.removeListener(_invalidate);
    for (final controller in [..._fields.values, _secondary, _reason]) {
      controller.dispose();
    }
    super.dispose();
  }

  bool _current(int generation) => mounted && generation == _generation && !_locked && widget.source.current(widget.patient.revision);

  Future<void> _request<T>(Future<T> Function() request, void Function(T) apply,
      {VoidCallback? clear, bool mutation = false}) async {
    if (_busy || _locked || (mutation && _uncertain)) {
      return;
    }
    final generation = ++_generation;
    setState(() { _busy = true; _error = null; clear?.call(); });
    try {
      final result = await request();
      if (_current(generation)) {
        setState(() => apply(result));
      }
    } on DoctorObservationConflict {
      if (_current(generation)) {
        setState(() { _clearRecord(); _error = 'The server record changed. Reload and review before making a new decision.'; });
      }
    } on DoctorObservationUnconfirmed {
      if (_current(generation)) {
        setState(() { _clearRecord(); _uncertain = true; _error = 'Outcome unconfirmed. Reconcile the server record. Do not submit again.'; });
      }
    } on Object {
      if (_current(generation)) {
        setState(() { _error = 'Clinical record or request could not be verified by the server.'; });
      }
    } finally {
      if (_current(generation)) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _load({int page = 1}) => _request(
    () => widget.source.admissions(widget.patient, page: page),
    (result) => _admissions = result, clear: () { _admissions = null; _clearRecord(); });

  Future<void> _inspect(PatientAdmission selected) => _request(
    () => widget.source.inspectDischarge(widget.patient, selected.id), (result) {
      _review = result;
      final summary = result.summary;
      if (summary != null) {
        for (final entry in _fields.entries) {
          entry.value.text = summary[entry.key] as String;
        }
        _secondary.text = (summary['secondary_diagnoses'] as List).join('\n');
      }
    }, clear: _clearRecord);

  Future<void> _loadEmar(PatientAdmission selected, {int page = 1}) => _request(
    () => widget.source.emarRecords(widget.patient, selected, page: page),
    (result) => _emar = result, clear: () { _emar = null; _rec = null; _history = null; });

  Future<void> _loadRec(PatientAdmission selected, {int page = 1}) => _request(
    () => widget.source.reconciliationRecords(widget.patient, selected, page: page),
    (result) => _rec = result, clear: () { _emar = null; _rec = null; _history = null; });

  Future<void> _readHistory(String label, Future<List<Map<String, dynamic>>> Function() read) => _request(
    read, (result) { _historyLabel = label; _history = result; }, clear: () { _history = null; _historyLabel = ''; });

  Future<void> _submit(DoctorDischargeReview review) async {
    if (_busy || _locked || _uncertain) {
      return;
    }
    final amendment = review.summary != null;
    final fields = {for (final e in _fields.entries) e.key: e.value.text};
    final secondary = _secondary.text;
    final reason = _reason.text;
    try {
      DoctorClinicalRecords.dischargeDocument(fields, secondary);
      if (amendment && (reason.trim().isEmpty || reason.trim().length > 10000)) {
        throw const FormatException('An amendment reason is required.');
      }
    } on FormatException {
      setState(() => _error = 'Complete all discharge documentation and any amendment reason.');
      return;
    }
    final generation = ++_generation;
    setState(() => _busy = true);
    final accepted = await showDialog<bool>(context: context, builder: (ctx) => AlertDialog(
      title: Text(amendment ? 'Amend finalized discharge summary?' : 'Submit clinical discharge?'),
      content: Text('Admission #${review.admission.id}. ${amendment ? 'This creates a versioned clinical amendment.' : 'This can close the admission and pending inpatient activities. The backend verifies medication reconciliation and all clinical discharge rules.'} Review the documentation before confirming.'),
      actions: [TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Confirm with server'))],
    ));
    if (!_current(generation)) {
      return;
    }
    setState(() => _busy = false);
    if (accepted != true) {
      return;
    }
    final intent = List.generate(20, (_) => _random.nextInt(256).toRadixString(16).padLeft(2, '0')).join();
    await _request(() => amendment
      ? widget.source.amendDischarge(widget.patient, review, intent: intent, fields: fields, secondary: secondary, reason: reason)
      : widget.source.discharge(widget.patient, review, intent: intent, fields: fields, secondary: secondary),
      (result) { _clearRecord(); _admissions = null; _error = 'Server confirmed summary #${result['id']}, version ${result['record_version']}. Reload admissions to review the committed state.'; }, mutation: true);
  }

  Future<void> _plan(PatientAdmission admission, ImagingProcedureOrder order) async {
    if (_busy || _locked || _uncertain) {
      return;
    }
    final generation = ++_generation;
    setState(() => _busy = true);
    final accepted = await showDialog<bool>(context: context, builder: (ctx) => AlertDialog(
      title: const Text('Plan surgical case only?'),
      content: Text('Procedure order #${order.id}. This creates an inert planning record. It does not authorize surgery, consent, anesthesia or medication administration.'),
      actions: [TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Create planning case'))],
    ));
    if (!_current(generation)) {
      return;
    }
    setState(() => _busy = false);
    if (accepted == true) {
      final intent = List.generate(20, (_) => _random.nextInt(256).toRadixString(16).padLeft(2, '0')).join();
      await _request(() => widget.source.planSurgicalCase(widget.patient, admission, order, intent: intent),
        (id) { _surgical = null; _error = 'Server confirmed planning case #$id. Open the existing surgery worklist to review it.'; }, mutation: true);
    }
  }

  Widget _field(TextEditingController controller, String label, int max) => TextField(
    controller: controller, enabled: !_busy && !_uncertain, maxLength: max,
    minLines: 1, maxLines: 4, decoration: InputDecoration(labelText: label));

  List<Widget> _paging(int page, int perPage, int total, Future<void> Function(int) request) => [
    if (page > 1) TextButton(onPressed: _busy ? null : () => request(page - 1), child: const Text('Previous')),
    if (page * perPage < total) TextButton(onPressed: _busy ? null : () => request(page + 1), child: const Text('Next')),
  ];

  @override
  Widget build(BuildContext context) {
    if (_locked || !widget.source.current(widget.patient.revision)) {
      return const Scaffold(body: Center(child: Text('Clinical record session unavailable.')));
    }
    final admissions = _admissions;
    final review = _review;
    final emar = _emar;
    final rec = _rec;
    final history = _history;
    return Scaffold(appBar: AppBar(title: const Text('Discharge and clinical record history')),
      body: SafeArea(child: ListView(padding: const EdgeInsets.all(16), children: [
        Text(widget.patient.patientLabel, style: Theme.of(context).textTheme.titleLarge),
        Text('Patient #${widget.patient.patientId} · Clinic #${widget.patient.clinicId}'),
        if (_busy) const LinearProgressIndicator(),
        if (_error != null) Text(_error!),
        OutlinedButton(onPressed: _busy ? null : () => _load(), child: const Text('Reload patient admissions')),
        if (admissions != null) ...[
          if (admissions.items.isEmpty) const Text('No admissions on this page.'),
          for (final row in admissions.items) ListTile(
            title: Text('Admission #${row.id} · ${row.status.name}'), subtitle: Text('Bed #${row.bedId}'),
            onTap: _busy ? null : () => _inspect(row)),
          ..._paging(admissions.page, admissions.perPage, admissions.total, (page) => _load(page: page)),
        ],
        if (review != null) ...[
          const Divider(),
          Text('Reviewed admission #${review.admission.id}', style: Theme.of(context).textTheme.titleMedium),
          Text('Status: ${review.admission.status.name}'),
          if (review.admission.isActive) ...[
            Text(review.reconciliation == null ? 'Discharge medication reconciliation not available.' :
              'Discharge reconciliation #${review.reconciliation!.id}: ${review.reconciliation!.status.name} · version ${review.reconciliation!.version}'),
            const Text('A finalized reconciliation is required, but does not establish clinical discharge clearance. The server makes the final decision.'),
          ],
          if (review.summary != null) ...[
            Text('Discharge summary #${review.summary!['id']} · ${review.summary!['record_status']} · version ${review.summary!['record_version']}'),
            const Text('Server-derived final medication list (read only):'),
            for (final item in review.summary!['final_medication_list'] as List)
              ListTile(title: Text((item as Map)['medication_name']?.toString() ?? 'Medication'),
                subtitle: Text([item['dose_text'], item['route_text'], item['frequency_text'], item['instructions']].whereType<String>().join(' · '))),
            OutlinedButton(onPressed: _busy ? null : () => _readHistory('Discharge summary amendments', () => widget.source.dischargeHistory(widget.patient, review)), child: const Text('View discharge amendment history')),
          ],
          if (widget.source.canSubmitDischarge(review) || review.summary != null) ...[
            for (final e in _fields.entries) _field(e.value, _labels[e.key]!, DoctorClinicalRecords.dischargeFields[e.key]!),
            _field(_secondary, 'Secondary diagnoses (one per line)', 20000),
            if (review.summary != null) _field(_reason, 'Amendment reason', 10000),
            FilledButton(onPressed: _busy || _uncertain ? null : () => _submit(review),
              child: Text(review.summary == null ? 'Review discharge' : 'Review amendment')),
          ],
          if (review.admission.isActive) ...[
            OutlinedButton(onPressed: _busy ? null : () => _request(
              () => widget.source.surgicalOrders(widget.patient, review.admission),
              (rows) => _surgical = rows, clear: () => _surgical = null), child: const Text('Review surgical procedure orders')),
            if (_surgical != null) ...[
              if (_surgical!.isEmpty) const Text('No active consent-required surgical orders returned.'),
              for (final order in _surgical!) ListTile(title: Text(order.name), subtitle: Text('Order #${order.id} · version ${order.version}'),
                trailing: OutlinedButton(onPressed: _busy || _uncertain ? null : () => _plan(review.admission, order), child: const Text('Plan case'))),
            ],
          ],
          OutlinedButton(onPressed: _busy ? null : () => _loadEmar(review.admission), child: const Text('Medication administration records')),
          OutlinedButton(onPressed: _busy ? null : () => _loadRec(review.admission), child: const Text('Medication reconciliation records')),
          if (emar != null) ...[
            for (final row in emar.items) ListTile(title: Text('eMAR #${row.id} · ${row.status.name}'),
              subtitle: Text('Record version ${row.recordVersion}'), onTap: _busy ? null : () => _readHistory('eMAR #${row.id} amendments', () => widget.source.emarHistory(widget.patient, review.admission, row))),
            ..._paging(emar.page, emar.perPage, emar.total, (page) => _loadEmar(review.admission, page: page)),
          ],
          if (rec != null) ...[
            for (final row in rec.items) ListTile(title: Text('Reconciliation #${row.id} · ${row.event.name}'),
              subtitle: Text('${row.status.name} · version ${row.version}'), onTap: _busy ? null : () => _readHistory('Reconciliation #${row.id} amendments', () => widget.source.reconciliationHistory(widget.patient, review.admission, row))),
            ..._paging(rec.page, rec.perPage, rec.total, (page) => _loadRec(review.admission, page: page)),
          ],
        ],
        if (history != null) ...[
          const Divider(), Text(_historyLabel, style: Theme.of(context).textTheme.titleMedium),
          if (history.isEmpty) const Text('No amendments returned by the server.'),
          for (final row in history) ListTile(title: Text('Version ${row['previous_version']} → ${row['new_version']}'),
            subtitle: Text('${row['reason']}\nStaff #${row['amended_by_staff_id']} · ${row['amended_at']}')),
        ],
      ])));
  }
}
