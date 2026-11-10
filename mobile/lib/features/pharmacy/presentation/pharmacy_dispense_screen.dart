import 'dart:async';

import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';

import 'package:clinic_system_pro/features/pharmacy/data/models/pharmacy_dispense.dart';
import 'package:clinic_system_pro/features/pharmacy/data/pharmacy_dispense_remote_data_source.dart';

/// P23H.S03B — pharmacist-only clinical confirmation of one batch allocation.
/// A reviewed screen never queues an offline dispense or replays a POST.
class PharmacyDispenseScreen extends StatefulWidget {
  const PharmacyDispenseScreen({
    super.key,
    required this.source,
    required this.patientId,
    required this.prescriptionId,
  }) : assert(patientId > 0),
       assert(prescriptionId > 0);

  final PharmacyDispenseRemoteDataSource source;
  final int patientId;
  final int prescriptionId;

  @override
  State<PharmacyDispenseScreen> createState() => _PharmacyDispenseScreenState();
}

class _PharmacyDispenseScreenState extends State<PharmacyDispenseScreen> {
  final _itemId = TextEditingController();
  final _batchId = TextEditingController();
  final _quantity = TextEditingController();
  final _notes = TextEditingController();
  PharmacyDispenseReview? _review;
  PharmacyDispenseRecord? _confirmed;
  String? _message;
  bool _busy = false;
  bool _attempted = false;
  int _generation = 0;
  late int _revision;

  bool get _authorized =>
      widget.source.inventory.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.source.inventory.sessionNavigationGuard.revision == _revision;

  @override
  void initState() {
    super.initState();
    _revision = widget.source.inventory.sessionNavigationGuard.revision;
    widget.source.inventory.sessionNavigationGuard.addListener(_onSession);
  }

  void _onSession() {
    if (!mounted) {
      return;
    }
    _generation++;
    setState(() {
      _review = null;
      _confirmed = null;
      _attempted = true;
      _message = 'Session or clinic changed. Reopen dispensing workflow.';
    });
  }

  @override
  void didUpdateWidget(covariant PharmacyDispenseScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(widget.source, oldWidget.source) ||
        widget.patientId != oldWidget.patientId ||
        widget.prescriptionId != oldWidget.prescriptionId) {
      oldWidget.source.inventory.sessionNavigationGuard.removeListener(
        _onSession,
      );
      widget.source.inventory.sessionNavigationGuard.addListener(_onSession);
      _generation++;
      _revision = widget.source.inventory.sessionNavigationGuard.revision;
      _review = null;
      _confirmed = null;
      _attempted = true; // Never recycle a State to authorize another write.
      _message = 'Scope changed. Reopen dispensing workflow.';
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.inventory.sessionNavigationGuard.removeListener(_onSession);
    for (final controller in <TextEditingController>[
      _itemId,
      _batchId,
      _quantity,
      _notes,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  void _invalidateReview() {
    if (!mounted || _attempted) {
      return;
    }
    setState(() {
      _generation++;
      _review = null;
      _confirmed = null;
      _busy = false;
      _message = null;
    });
  }

  Future<void> _loadReview() async {
    if (!_authorized || _busy || _attempted || widget.source.writeAttempted) {
      return;
    }
    final itemId = int.tryParse(_itemId.text.trim());
    final batchId = int.tryParse(_batchId.text.trim());
    final quantity = int.tryParse(_quantity.text.trim());
    if (itemId == null ||
        batchId == null ||
        quantity == null ||
        itemId <= 0 ||
        batchId <= 0 ||
        quantity <= 0) {
      setState(() => _message = 'Enter positive item, batch and quantity IDs.');
      return;
    }
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _review = null;
      _confirmed = null;
      _message = null;
    });
    try {
      final result = await widget.source.review(
        patientId: widget.patientId,
        prescriptionId: widget.prescriptionId,
        prescriptionItemId: itemId,
        batchId: batchId,
        quantity: quantity,
      );
      if (!mounted || !_authorized || generation != _generation) {
        return;
      }
      setState(() => _review = result);
    } on Object {
      if (!mounted || !_authorized || generation != _generation) {
        return;
      }
      setState(
        () => _message = 'Server evidence unavailable, expired or insufficient. No dispense sent.',
      );
    } finally {
      if (mounted && generation == _generation) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _confirmAndDispense() async {
    final reviewed = _review;
    if (reviewed == null ||
        !_authorized ||
        _busy ||
        _attempted ||
        widget.source.writeAttempted) {
      return;
    }
    final notes = _notes.text.trim();
    if (notes.length > 2000) {
      setState(
        () => _message = 'Dispense notes cannot exceed 2000 characters.',
      );
      return;
    }
    final approved = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('Confirm one pharmacy dispense?'),
        content: Text(
          'Prescription #${reviewed.prescriptionId}, item #${reviewed.prescriptionItemId}. '
          'Dispense ${reviewed.quantity} unit(s) of ${reviewed.drugName} '
          'from batch ${reviewed.batchNumber}. The backend will recheck '
          'remaining prescription quantity, batch expiry and stock. '
          'This is not medication administration. The outcome cannot be '
          'automatically retried if communication fails.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(dialog).pop(false),
            child: const Text('Back'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialog).pop(true),
            child: const Text('Confirm dispense once'),
          ),
        ],
      ),
    );
    if (!mounted ||
        !_authorized ||
        approved != true ||
        _attempted ||
        widget.source.writeAttempted) {
      return;
    }
    final generation = ++_generation;
    setState(() {
      _attempted = true;
      _busy = true;
      _review = null;
      _confirmed = null;
      _message = null;
    });
    try {
      final record = await widget.source.dispenseOnce(
        reviewed: reviewed,
        idempotencyKey: const Uuid().v4(),
        notes: notes.isEmpty ? null : notes,
      );
      if (!mounted || !_authorized || generation != _generation) {
        return;
      }
      setState(() {
        _confirmed = record;
        _message = 'Dispense confirmed by fresh server record readback.';
      });
    } on PharmacyDispenseEvidenceChanged {
      if (!mounted || !_authorized || generation != _generation) {
        return;
      }
      setState(
        () => _message =
            'Server stock or prescription changed. No dispense was submitted. '
            'Reopen and review fresh evidence.',
      );
    } on Object {
      if (!mounted || !_authorized || generation != _generation) {
        return;
      }
      setState(
        () => _message =
            'Dispense outcome unconfirmed. Inspect the pharmacy record '
            'before any new intentional clinical action; never replay this POST.',
      );
    } finally {
      if (mounted && generation == _generation) {
        setState(() => _busy = false);
      }
    }
  }

  Widget _field(
    String key,
    String label,
    TextEditingController controller, {
    bool numeric = false,
  }) => TextField(
    key: ValueKey(key),
    controller: controller,
    keyboardType: numeric ? TextInputType.number : TextInputType.text,
    onChanged: (_) => _invalidateReview(),
    decoration: InputDecoration(labelText: label),
  );

  @override
  Widget build(BuildContext context) {
    if (!_authorized) {
      return const Scaffold(
        body: Center(child: Text('Pharmacy session unavailable.')),
      );
    }
    final canSubmit =
        !_busy &&
        !_attempted &&
        !widget.source.writeAttempted &&
        _review != null;
    return Scaffold(
      appBar: AppBar(title: const Text('Pharmacy dispense')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          Text(
            'Patient #${widget.patientId} • Prescription #${widget.prescriptionId}',
          ),
          const Text('Pharmacist only • online one-shot • server-owned stock.'),
          _field(
            's03b-item-id',
            'Prescription item ID',
            _itemId,
            numeric: true,
          ),
          _field('s03b-batch-id', 'Stock batch ID', _batchId, numeric: true),
          _field(
            's03b-quantity',
            'Quantity to dispense',
            _quantity,
            numeric: true,
          ),
          _field('s03b-notes', 'Dispense notes (optional)', _notes),
          const SizedBox(height: 12),
          OutlinedButton(
            key: const ValueKey('s03b-review'),
            onPressed: _busy || _attempted
                ? null
                : () => unawaited(_loadReview()),
            child: const Text('Verify prescription and batch online'),
          ),
          if (_busy) const LinearProgressIndicator(),
          if (_review != null) ...<Widget>[
            Card(
              key: const ValueKey('s03b-reviewed-evidence'),
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text('Medication: ${_review!.drugName}'),
                    Text('Batch: ${_review!.batchNumber}'),
                    Text('Batch expires: ${_review!.batchExpiry}'),
                    Text('Batch quantity reported: ${_review!.batchQuantity}'),
                    Text('Prescribed quantity: ${_review!.prescribedQuantity}'),
                    Text('Requested dispense quantity: ${_review!.quantity}'),
                    const Text(
                      'These values may change. Backend enforces final quantities.',
                    ),
                  ],
                ),
              ),
            ),
            FilledButton(
              key: const ValueKey('s03b-confirm-dispense'),
              onPressed: canSubmit
                  ? () => unawaited(_confirmAndDispense())
                  : null,
              child: const Text('Review and confirm one dispense'),
            ),
          ],
          if (_confirmed != null) ...<Widget>[
            Text(
              'Dispense #${_confirmed!.id}',
              key: const ValueKey('s03b-confirmed-receipt'),
            ),
            Text('Backend status: ${_confirmed!.status}'),
            Text(
              'Dispensed quantity: ${_confirmed!.lines.single.quantityDispensed}',
            ),
          ],
          if (_message != null)
            Text(_message!, key: const ValueKey('s03b-message')),
          if (_attempted)
            const Text(
              'Clinical attempt locked. Reopen after independent verification.',
            ),
        ],
      ),
    );
  }
}
