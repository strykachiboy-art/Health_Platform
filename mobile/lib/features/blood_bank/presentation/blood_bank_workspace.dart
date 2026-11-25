import 'dart:async';

import 'package:flutter/material.dart';

import '../data/blood_bank_contract.dart';
import '../data/blood_bank_forms.dart';
import '../data/blood_bank_source.dart';
import 'blood_bank_bedside.dart';
import 'blood_bank_fields.dart';

const _orderActions = {
  BbAction.order,
  BbAction.compatibility,
  BbAction.reserve,
  BbAction.unreserve,
  BbAction.issue,
  BbAction.cancel,
  BbAction.closePartial,
  BbAction.review,
  BbAction.followup,
};
const _transfusionActions = {
  BbAction.transfusion,
  BbAction.complete,
  BbAction.stop,
  BbAction.reaction,
  BbAction.patientRelease,
};
const _unitActions = {BbAction.release, BbAction.returnUnit, BbAction.discard};
const _patientActions = {
  BbAction.orders,
  BbAction.order,
  BbAction.createOrder,
  BbAction.history,
  BbAction.transfusion,
  BbAction.complete,
  BbAction.stop,
  BbAction.reaction,
  BbAction.patientRelease,
  BbAction.cancel,
  BbAction.closePartial,
  BbAction.review,
};

class BloodBankWorkspace extends StatefulWidget {
  const BloodBankWorkspace({
    super.key,
    required this.source,
    this.patientId,
    this.orderId,
    this.transfusionId,
  });
  final BloodBankSource source;
  final int? patientId, orderId, transfusionId;
  @override
  State<BloodBankWorkspace> createState() => _BloodBankWorkspaceState();
}

class _BloodBankWorkspaceState extends State<BloodBankWorkspace> {
  BloodBankSession? _session;
  String? _message;
  bool _busy = true;
  int _generation = 0;
  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_invalidate);
    unawaited(_load());
  }

  void _invalidate() {
    if (mounted && _session?.active != true) {
      setState(() {
        _generation++;
        _session = null;
        _busy = false;
        _message = 'Session context changed.';
      });
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.guard.removeListener(_invalidate);
    super.dispose();
  }

  Future<void> _load() async {
    final n = ++_generation;
    setState(() {
      _busy = true;
      _session = null;
      _message = null;
    });
    try {
      final s = await widget.source.open();
      if (widget.patientId != null && !bbClinicalRoles.contains(s.role)) {
        throw const BbUnavailable();
      }
      if (mounted && n == _generation && s.active) setState(() => _session = s);
    } on Object catch (e) {
      if (mounted && n == _generation) setState(() => _message = bankError(e));
    } finally {
      if (mounted && n == _generation) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = _session;
    if (s != null && s.active) {
      if (widget.orderId != null) {
        return BbOperation(
          session: s,
          action: BbAction.order,
          patientId: widget.patientId,
          initialId: widget.orderId,
        );
      }
      if (widget.transfusionId != null) {
        return BbOperation(
          session: s,
          action: BbAction.transfusion,
          patientId: widget.patientId,
          initialId: widget.transfusionId,
        );
      }
      if (s.role == 'patient') {
        return BbOperation(session: s, action: BbAction.myHistory);
      }
    }
    final actions = BbAction.values
        .where(
          (a) =>
              s != null &&
              s.allows(a) &&
              !a.dedicated &&
              (widget.patientId == null || _patientActions.contains(a)),
        )
        .toList();
    return Scaffold(
      appBar: AppBar(title: const Text('Blood Bank')),
      body: _busy
          ? const Center(child: CircularProgressIndicator())
          : s == null || !s.active
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(_message ?? 'Blood Bank unavailable.'),
                    const Text(
                      'Staged integration: server approval and clinical authority are required.',
                    ),
                    TextButton(
                      onPressed: _load,
                      child: const Text('Check availability'),
                    ),
                  ],
                ),
              ),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text('Clinic #${s.clinic} · ${s.role.replaceAll('_', ' ')}'),
                if (widget.patientId != null)
                  Text('Patient #${widget.patientId}'),
                const Text(
                  'Live records only. No local clearance, compatibility calculation, or automatic replay.',
                ),
                if (bbClinicalRoles.contains(s.role))
                  Card(
                    child: ListTile(
                      title: const Text('Bedside blood barcode verification'),
                      leading: const Icon(Icons.qr_code_scanner),
                      onTap: () => Navigator.of(context).push<void>(
                        MaterialPageRoute(
                          builder: (_) => BbBedside(
                            session: s,
                            patientId: widget.patientId,
                          ),
                        ),
                      ),
                    ),
                  ),
                for (final section
                    in actions.map((a) => a.section).toSet()) ...[
                  Padding(
                    padding: const EdgeInsets.only(top: 16),
                    child: Text(
                      section,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                  ),
                  for (final a in actions.where((a) => a.section == section))
                    Card(
                      child: ListTile(
                        title: Text(a.label),
                        subtitle: Text(
                          a.write ? 'Explicit online action' : 'Live read',
                        ),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => Navigator.of(context).push<void>(
                          MaterialPageRoute(
                            builder: (_) => BbOperation(
                              session: s,
                              action: a,
                              patientId: widget.patientId,
                              initialId: a == BbAction.history
                                  ? widget.patientId
                                  : null,
                            ),
                          ),
                        ),
                      ),
                    ),
                ],
              ],
            ),
    );
  }
}

class BbOperation extends StatefulWidget {
  const BbOperation({
    super.key,
    required this.session,
    required this.action,
    this.initialId,
    this.patientId,
  });
  final BloodBankSession session;
  final BbAction action;
  final int? initialId, patientId;
  @override
  State<BbOperation> createState() => _BbOperationState();
}

class _BbOperationState extends State<BbOperation> {
  final _id = TextEditingController(),
      _secondary = TextEditingController(),
      _patient = TextEditingController();
  final _values = <String, dynamic>{};
  BbJson? _result;
  String? _message;
  bool _busy = false, _submitted = false;
  int _generation = 0;
  int? _lastTarget;
  BloodBankSession get _s => widget.session;
  BbAction get _a => widget.action;
  bool get _current => mounted && _s.active;
  bool _live(int n) => _current && n == _generation;
  bool get _allowed =>
      _s.allows(_a) &&
      (widget.patientId == null || _patientActions.contains(_a));
  bool get _patientInput => _a == BbAction.orders || _a == BbAction.createOrder;
  @override
  void initState() {
    super.initState();
    _s.source.guard.addListener(_invalidate);
    if (widget.initialId != null) _id.text = '${widget.initialId}';
    if (widget.patientId != null) _patient.text = '${widget.patientId}';
    if (!_a.write &&
        !_patientInput &&
        (!_a.needsId || widget.initialId != null)) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_current) unawaited(_read());
      });
    }
  }

  void _invalidate() {
    if (mounted && !_s.active) {
      setState(() {
        _generation++;
        _result = null;
        _values.clear();
        _message = null;
        _id.clear();
        _patient.clear();
        _secondary.clear();
      });
    }
  }

  @override
  void dispose() {
    _generation++;
    _s.source.guard.removeListener(_invalidate);
    _id.dispose();
    _secondary.dispose();
    _patient.dispose();
    _values.clear();
    super.dispose();
  }

  int? _target() => _a.needsId ? bbId(int.tryParse(_id.text.trim())) : null;
  int? _second() =>
      _a.needsSecondary ? bbId(int.tryParse(_secondary.text.trim())) : null;
  Future<void> _run(Future<void> Function(int) fn) async {
    if (!_current || !_allowed || _busy) return;
    final n = ++_generation;
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await fn(n);
    } on Object catch (e) {
      if (_live(n)) {
        setState(() {
          _result = null;
          _message = bankError(e);
        });
      }
    } finally {
      if (_live(n)) setState(() => _busy = false);
    }
  }

  Future<void> _read({bool next = false}) => _run((n) async {
    final id = _target();
    final cursor = next ? (_result?['next_after_id']) : null;
    if (next && (cursor == null || id != _lastTarget)) bbInvalid();
    final patient = _patientInput
        ? bbId(int.tryParse(_patient.text))
        : widget.patientId;
    final admission = _a == BbAction.orders
        ? await _s.currentAdmission(patient!)
        : null;
    final result = await _s.read(
      _a,
      id: id,
      admission: admission,
      patient: patient,
      after: cursor == null ? null : bbId(cursor),
    );
    if (_live(n)) {
      setState(() {
        _result = result;
        _lastTarget = id;
        _message = 'Live server response received.';
      });
    }
  });
  Future<void> _submit() => _run((n) async {
    if (_submitted || _s.writeBlocked) throw const BbUnknownOutcome();
    final id = _target(), second = _second();
    final body = bbActionPayload(_a, _values);
    BbJson? order;
    List<BbUnit> reserved = [];
    var description = '${_a.label}${id == null ? '' : ' · record #$id'}';
    int? expectedPatient = widget.patientId, admission;
    if (_a == BbAction.createOrder) {
      expectedPatient = bbId(int.tryParse(_patient.text));
      admission = await _s.currentAdmission(expectedPatient);
      body['admission_id'] = admission;
      description += '\nPatient #$expectedPatient, admission #$admission';
    }
    if (_orderActions.contains(_a)) {
      order = await _s.read(
        BbAction.order,
        id: bbId(id),
        patient: widget.patientId,
      );
      if (order['inventory_required'] != true) {
        throw const FormatException('Legacy order is read only here.');
      }
      expectedPatient = bbId(order['patient_id']);
      admission = bbId(order['admission_id']);
      description +=
          '\nPatient #$expectedPatient, admission #$admission, order version ${order['version']}';
      if (_a == BbAction.unreserve || _a == BbAction.closePartial) {
        body['expected_order_version'] = bbId(order['version']);
      }
      if (_a == BbAction.issue) {
        reserved = await _s.reserved(order);
        if (reserved.isEmpty) bbInvalid();
        description +=
            '\nIssue exactly: ${reserved.map((u) => '${u.barcode} (${u.group})').join(', ')}';
      }
      if (_a == BbAction.reserve) {
        description +=
            '\nReserve ${bbRows(body['units']).length} specified components; not issuance.';
      }
    }
    if (_unitActions.contains(_a)) {
      final unit = BbUnit.fromJson(
        await _s.read(BbAction.unit, id: bbId(id)),
        _s.clinic,
      );
      if (_a != BbAction.discard) body['expected_version'] = unit.version;
      description +=
          '\nComponent ${unit.barcode} · ${unit.status} · v${unit.version}';
    }
    if (_transfusionActions.contains(_a)) {
      final row = await _s.read(
        BbAction.transfusion,
        id: bbId(id),
        patient: widget.patientId,
      );
      expectedPatient = bbId(row['patient_id']);
      description +=
          '\nPatient #$expectedPatient, admission #${row['admission_id']}, component #${row['blood_unit_id']} · ${row['status']}';
    }
    await _s.check();
    if (!mounted || !_live(n)) return;
    final ok = await confirmBankAction(context, _a.label, description);
    if (!ok || !_live(n)) return;
    BbJson result;
    if (_a == BbAction.reserve) {
      result = await _s.reserve(order: order!, units: bbRows(body['units']));
    } else if (_a == BbAction.issue) {
      result = await _s.issue(
        order: order!,
        displayed: reserved,
        note: bbText(body['fulfillment_note'], max: 10000),
      );
    } else {
      result = await _s.submit(
        _a,
        body,
        id: id,
        secondary: second,
        patient: expectedPatient,
        admission: admission,
      );
    }
    if (_live(n)) {
      setState(() {
        _result = result;
        _submitted = true;
        _values.clear();
        _message = 'Server response validated. Submitted once.';
      });
    }
  });
  void _open(BbAction action, int id) {
    Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) => BbOperation(
          session: _s,
          action: action,
          initialId: id,
          patientId: widget.patientId,
        ),
      ),
    );
  }

  List<Widget> _recordLinks(BbJson r) {
    final id = r['id'];
    if (id is! int || id <= 0) return [];
    if ((_a == BbAction.orders || _a == BbAction.order) &&
        !r.containsKey('units_requested')) {
      return [];
    }
    if ((_a == BbAction.transfusion || _a == BbAction.history) &&
        !r.containsKey('blood_product_order_id')) {
      return [];
    }
    if ((_a == BbAction.unit || _a == BbAction.inventory) &&
        !r.containsKey('unit_barcode')) {
      return [];
    }
    final actions = switch (_a) {
      BbAction.orders ||
      BbAction.order => _orderActions.where((a) => a != BbAction.order),
      BbAction.history || BbAction.transfusion => _transfusionActions.where(
        (a) => a != BbAction.transfusion,
      ),
      BbAction.inventory || BbAction.unit => _unitActions,
      BbAction.artifacts || BbAction.artifact => {BbAction.verifyArtifact},
      BbAction.inbox => {BbAction.acknowledge},
      _ => const <BbAction>{},
    };
    return [
      if ((_a == BbAction.orders || _a == BbAction.order) &&
          bbClinicalRoles.contains(_s.role) &&
          r['inventory_required'] == true)
        OutlinedButton(
          onPressed: () => Navigator.of(context).push<void>(
            MaterialPageRoute(
              builder: (_) => BbBedside(
                session: _s,
                patientId: widget.patientId,
                orderId: id,
              ),
            ),
          ),
          child: const Text('Bedside verification'),
        ),
      for (final a in actions.where(
        (a) =>
            _s.allows(a) &&
            (widget.patientId == null || _patientActions.contains(a)),
      ))
        TextButton(onPressed: () => _open(a, id), child: Text(a.label)),
    ];
  }

  // A fixed display allowlist avoids accidentally rendering added secret or PHI fields.
  static const _displayKeys = {
    'id',
    'clinic_id',
    'patient_id',
    'admission_id',
    'order_id',
    'blood_product_order_id',
    'blood_unit_id',
    'blood_type',
    'product_type',
    'product_blood_type',
    'unit_identifier',
    'unit_barcode',
    'din',
    'status',
    'version',
    'units_requested',
    'units_issued',
    'inventory_required',
    'compatibility_status',
    'emergency_release_requested',
    'issued_at',
    'started_at',
    'ended_at',
    'expires_at',
    'recall_blocked',
    'reaction_reported',
    'created_at',
    'kind',
    'marker',
    'result',
    'verified',
    'reference',
    'name',
    'supplier_code',
    'donor_code',
    'full_name',
    'phone',
    'consent_to_contact',
    'deferral_status',
    'donation_number',
    'tti_screening_status',
    'available_units',
    'reserved_units',
    'quarantined_units',
    'stock_level',
    'minimum_units',
    'critical_units',
    'total_available',
    'total_quarantined',
    'message',
    'reason',
    'outcome',
    'acknowledged_at',
    'release_authorized',
    'missing_verified_markers',
    'recall_ids',
    'affected_component_ids',
    'automatic_replay',
    'server_no_effect_disposition',
    'operation_type',
    'method',
    'path',
    'policy_version',
    'automatic_replay_authorizations',
    'issued_units',
    'lab_followups',
    'items',
    'units',
    'donation',
    'transfusion',
    'eligible_donors',
    'stock',
    'unit_status_counts',
    'transfusion_status_counts',
    'investigation_event_counts',
    'recall_count',
    'unacknowledged_alerts',
  };
  Widget _show(Object? value) {
    if (value is Map<String, dynamic>) {
      return Card(
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (final e in value.entries.where(
                (e) => _displayKeys.contains(e.key),
              )) ...[
                (e.value is Map || e.value is List)
                    ? Text(
                        e.key.replaceAll('_', ' '),
                        style: const TextStyle(fontWeight: FontWeight.bold),
                      )
                    : Text('${e.key.replaceAll('_', ' ')}: ${e.value ?? '—'}'),
                if (e.value is Map || e.value is List) _show(e.value),
              ],
              ..._recordLinks(value),
            ],
          ),
        ),
      );
    }
    if (value is List) {
      return Column(children: [for (final row in value) _show(row)]);
    }
    return Text('${value ?? '—'}');
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(_a.label)),
    body: !_current || !_allowed
        ? const Center(child: Text('Session or role unavailable.'))
        : ListView(
            padding: const EdgeInsets.all(16),
            children: [
              const Text(
                'Server-authoritative, online-only record. No automatic replay.',
              ),
              if (_a.needsId)
                TextField(
                  controller: _id,
                  enabled: !_busy && !_submitted && widget.initialId == null,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Server record ID',
                  ),
                ),
              if (_a.needsSecondary)
                TextField(
                  controller: _secondary,
                  enabled: !_busy && !_submitted,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Issued-unit record ID',
                  ),
                ),
              if (_patientInput)
                TextField(
                  controller: _patient,
                  enabled: !_busy && !_submitted && widget.patientId == null,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Patient ID (active admission verified online)',
                  ),
                ),
              if (_a.write && !_submitted) ...[
                BbFields(
                  fields: bbFields(_a),
                  values: _values,
                  enabled: !_busy && !_s.writeBlocked,
                ),
                FilledButton(
                  onPressed: _busy || _s.writeBlocked ? null : _submit,
                  child: const Text('Review and submit once'),
                ),
              ],
              if (!_a.write)
                OutlinedButton(
                  onPressed: _busy ? null : _read,
                  child: const Text('Load live records'),
                ),
              if (_busy) const LinearProgressIndicator(),
              if (_message != null) Text(_message!),
              if (_result != null) _show(_result),
              if (!_a.write && _result?['next_after_id'] != null)
                OutlinedButton(
                  onPressed: _busy ? null : () => _read(next: true),
                  child: const Text('Next page'),
                ),
              if (_s.writeBlocked)
                const Text(
                  'An earlier request may have an unresolved outcome. Writes are locked for this session scope; reconcile manually.',
                ),
            ],
          ),
  );
}
