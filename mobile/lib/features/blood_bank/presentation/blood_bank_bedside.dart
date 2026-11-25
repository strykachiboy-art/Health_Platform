import 'dart:async';

import 'package:flutter/material.dart';

import '../data/blood_bank_contract.dart';
import '../data/blood_bank_source.dart';
import '../data/blood_bank_forms.dart';
import 'blood_bank_camera.dart';
import 'blood_bank_fields.dart';

class BbBedside extends StatefulWidget {
  const BbBedside({
    super.key,
    required this.session,
    this.patientId,
    this.orderId,
    this.capture = captureBankCode,
  });
  final BloodBankSession session;
  final int? patientId, orderId;
  final BankCameraCapture capture;
  @override
  State<BbBedside> createState() => _BbBedsideState();
}

class _BbBedsideState extends State<BbBedside> with WidgetsBindingObserver {
  final _id = TextEditingController();
  final _vitals = <String, dynamic>{};
  BbJson? _order, _transfusion;
  BankCapturedCode? _patient, _unit;
  BbVerifiedScan? _verified;
  String? _message;
  bool _busy = false;
  int _generation = 0;
  BloodBankSession get _s => widget.session;
  bool get _current => mounted && _s.active;
  bool _live(int n) => _current && n == _generation;
  void _clearScan() {
    _patient = null;
    _unit = null;
    _verified = null;
  }

  void _invalidate() {
    if (mounted && !_s.active) {
      setState(() {
        _generation++;
        _busy = false;
        _order = null;
        _transfusion = null;
        _clearScan();
        _vitals.clear();
        _id.clear();
        _message = null;
      });
    }
  }

  @override
  void initState() {
    super.initState();
    _s.source.guard.addListener(_invalidate);
    WidgetsBinding.instance.addObserver(this);
    if (widget.orderId != null) {
      _id.text = '${widget.orderId}';
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_current) unawaited(_load());
      });
    }
  }

  @override
  void dispose() {
    _generation++;
    _s.source.guard.removeListener(_invalidate);
    WidgetsBinding.instance.removeObserver(this);
    _id.dispose();
    _vitals.clear();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (mounted && state != AppLifecycleState.resumed && _verified != null) {
      setState(_clearScan);
    }
  }

  Future<void> _run(Future<void> Function(int) fn) async {
    if (!_current || _busy) return;
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
          _clearScan();
          _message = bankError(e);
        });
      }
    } finally {
      if (_live(n)) setState(() => _busy = false);
    }
  }

  Future<void> _load() => _run((n) async {
    setState(() {
      _order = null;
      _transfusion = null;
      _clearScan();
      _vitals.clear();
    });
    final row = await _s.read(
      BbAction.order,
      id: bbId(int.tryParse(_id.text)),
      patient: widget.patientId,
    );
    if (row['inventory_required'] != true) {
      throw const FormatException('Physical order required');
    }
    if (_live(n)) setState(() => _order = row);
  });
  Future<void> _capture(BankScanKind kind) => _run((n) async {
    await _s.check();
    if (!mounted || !_live(n)) return;
    final result = await widget.capture(context, kind, _s.source.guard);
    await _s.check();
    if (!_live(n)) return;
    setState(() {
      _verified = null;
      if (kind == BankScanKind.wristband) {
        _patient = result;
        _unit = null;
      } else {
        _unit = result;
      }
    });
  });
  Future<void> _verify() => _run((n) async {
    final o = _order, p = _patient, u = _unit;
    if (o == null || p == null || u == null) return;
    final scan = await _s.verify(
      order: o,
      patientBarcode: p.value,
      unitBarcode: u.value,
      capturedAt: p.at.isBefore(u.at) ? p.at : u.at,
    );
    if (_live(n)) {
      setState(() {
        _clearScan();
        _verified = scan;
        _message = scan == null
            ? 'Server denied verification. Do not start.'
            : 'Verified by server. Transfusion has not started.';
      });
    }
  });
  Future<void> _start() => _run((n) async {
    final scan = _verified;
    if (scan == null || !scan.fresh) return;
    final vitals = bbPayload(observations, _vitals);
    await _s.check();
    if (!mounted || !_live(n)) return;
    final ok = await confirmBankAction(
      context,
      'Record transfusion start',
      'Patient #${scan.patientId}, admission #${scan.admissionId}, order #${scan.orderId}, component #${scan.unitId}. Start is not completion.',
    );
    if (!ok || !_live(n) || !identical(scan, _verified)) return;
    setState(_clearScan);
    final t = await _s.start(scan, vitals);
    if (_live(n)) {
      setState(() {
        _transfusion = t;
        _vitals.clear();
        _message =
            'Server recorded transfusion in progress. Completion is separate.';
      });
    }
  });
  @override
  Widget build(BuildContext context) {
    final o = _order, t = _transfusion;
    return Scaffold(
      appBar: AppBar(title: const Text('Bedside blood barcode verification')),
      body: !_current
          ? const Center(child: Text('Session unavailable.'))
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Text(
                  'Online only. Follow approved clinical protocol. Camera capture and issuance do not record administration.',
                ),
                TextField(
                  controller: _id,
                  enabled: !_busy && t == null,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Blood order ID',
                  ),
                ),
                OutlinedButton(
                  onPressed: _busy || t != null ? null : _load,
                  child: const Text('Load live order'),
                ),
                if (o != null) ...[
                  Text(
                    'Patient #${o['patient_id']} · admission #${o['admission_id']} · order #${o['id']}',
                  ),
                  Text(
                    'Issued ${o['units_issued']} of ${o['units_requested']}',
                  ),
                  for (final r in bbRows(o['issued_units'], max: 100))
                    Text(
                      'Physical issue #${r['blood_unit_id']} · ${r['unit_identifier']} · ${r['product_blood_type']}',
                    ),
                  if (t == null && bbCount(o['units_issued']) > 0) ...[
                    OutlinedButton.icon(
                      onPressed: _busy || _s.writeBlocked
                          ? null
                          : () => _capture(BankScanKind.wristband),
                      icon: const Icon(Icons.qr_code_scanner),
                      label: Text(
                        _patient == null
                            ? 'Scan patient wristband'
                            : 'Wristband captured — rescan',
                      ),
                    ),
                    OutlinedButton.icon(
                      onPressed: _busy || _s.writeBlocked || _patient == null
                          ? null
                          : () => _capture(BankScanKind.component),
                      icon: const Icon(Icons.qr_code_scanner),
                      label: Text(
                        _unit == null
                            ? 'Scan physical component'
                            : 'Component captured — rescan',
                      ),
                    ),
                    FilledButton(
                      onPressed:
                          _busy ||
                              _patient == null ||
                              _unit == null ||
                              _s.writeBlocked
                          ? null
                          : _verify,
                      child: const Text('Verify both barcodes with server'),
                    ),
                    BbFields(
                      fields: observations,
                      values: _vitals,
                      enabled: !_busy,
                    ),
                    FilledButton(
                      onPressed: _busy || _verified == null || _s.writeBlocked
                          ? null
                          : _start,
                      child: const Text('Record verified transfusion start'),
                    ),
                  ],
                ],
                if (t != null)
                  Text(
                    'Transfusion #${t['id']} · ${t['status']}. Open Transfusion detail in the Blood Bank workspace for completion, stop or reaction documentation.',
                  ),
                if (_busy) const LinearProgressIndicator(),
                if (_message != null) Text(_message!),
              ],
            ),
    );
  }
}
