import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';
import 'package:clinic_system_pro/features/billing/data/billing_invoice_create_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/models/invoice_creation.dart';

/// Standalone P23I.S03 invoice-entry workflow; routing belongs to a later gate.
/// Patient comes from the caller's patient context, not an arbitrary clinic ID.
class BillingInvoiceCreateScreen extends StatefulWidget {
  const BillingInvoiceCreateScreen({
    super.key,
    required this.source,
    required this.patientId,
  }) : assert(patientId > 0);

  final BillingInvoiceCreateRemoteDataSource source;
  final int patientId;

  @override
  State<BillingInvoiceCreateScreen> createState() =>
      _BillingInvoiceCreateScreenState();
}

class _BillingInvoiceCreateScreenState
    extends State<BillingInvoiceCreateScreen> {
  final _currency = TextEditingController();
  final _description = TextEditingController();
  final _quantity = TextEditingController(text: '1');
  final _unitPrice = TextEditingController();
  final _dueDate = TextEditingController();
  final _provider = TextEditingController();
  bool _insurance = false;
  bool _reviewed = false;
  bool _attempted = false;
  bool _busy = false;
  int _revision = 0;
  String? _message;
  CreatedInvoice? _confirmed;

  @override
  void initState() {
    super.initState();
    _revision = widget.source.reader.sessionNavigationGuard.revision;
    widget.source.reader.sessionNavigationGuard.addListener(_onSessionChanged);
    for (final field in _fields) {
      field.addListener(_invalidateReview);
    }
  }

  List<TextEditingController> get _fields => <TextEditingController>[
    _currency,
    _description,
    _quantity,
    _unitPrice,
    _dueDate,
    _provider,
  ];

  void _invalidateReview() {
    if (_reviewed && mounted) setState(() => _reviewed = false);
  }

  @override
  void didUpdateWidget(covariant BillingInvoiceCreateScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source) ||
        oldWidget.patientId != widget.patientId) {
      oldWidget.source.reader.sessionNavigationGuard.removeListener(
        _onSessionChanged,
      );
      widget.source.reader.sessionNavigationGuard.addListener(
        _onSessionChanged,
      );
      _revision = widget.source.reader.sessionNavigationGuard.revision;
      _attempted = true; // Never reuse a State object to re-enable a POST.
      _reviewed = false;
      _confirmed = null;
      _message = 'Patient or billing source changed. Reopen a fresh workflow.';
    }
  }

  @override
  void dispose() {
    widget.source.reader.sessionNavigationGuard.removeListener(
      _onSessionChanged,
    );
    for (final field in _fields) {
      field.dispose();
    }
    super.dispose();
  }

  bool get _current =>
      widget.source.reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.source.reader.sessionNavigationGuard.revision == _revision;

  void _onSessionChanged() {
    if (!mounted) return;
    setState(() {
      _confirmed = null;
      _reviewed = false;
      _attempted = true;
      _message = 'Session changed. Invoice data cleared; reopen workflow.';
    });
    for (final field in _fields) {
      field.clear();
    }
  }

  InvoiceDraft _draft() {
    final quantity = int.tryParse(_quantity.text.trim());
    if (quantity == null) {
      throw const FormatException('Invalid invoice quantity.');
    }
    final draft = InvoiceDraft(
      patientId: widget.patientId,
      currency: _currency.text.trim().toUpperCase(),
      items: <InvoiceDraftItem>[
        InvoiceDraftItem(
          description: _description.text.trim(),
          quantity: quantity,
          unitPrice: _unitPrice.text.trim(),
        ),
      ],
      dueDate: _dueDate.text.trim().isEmpty ? null : _dueDate.text.trim(),
      isInsuranceClaim: _insurance,
      insuranceProvider: _insurance ? _provider.text.trim() : null,
    );
    draft.toJson();
    return draft;
  }

  Future<void> _create() async {
    if (!_current ||
        _busy ||
        _attempted ||
        widget.source.writeAttempted ||
        !_reviewed) {
      return;
    }
    late final InvoiceDraft draft;
    late final String frozen;
    try {
      draft = _draft();
      frozen = jsonEncode(draft.toJson());
    } on Object {
      setState(
        () => _message = 'Check currency, invoice item, quantity, price, date and insurance. Nothing sent.',
      );
      return;
    }
    final currentSource = widget.source;
    final currentPatient = widget.patientId;
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('Confirm invoice creation'),
        content: Text(
          'Create one invoice for patient #${draft.patientId}, '
          '${draft.currency}, ${draft.items.single.quantity} item unit(s) '
          'at ${draft.items.single.unitPrice} per unit? '
          'The server will calculate the authoritative totals. '
          'Do not repeat submission if the outcome is uncertain.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: const Text('Back'),
          ),
          FilledButton(
            key: const ValueKey('p23i-s03-dialog-confirm'),
            onPressed: () => Navigator.pop(dialog, true),
            child: const Text('Confirm once'),
          ),
        ],
      ),
    );
    if (!mounted ||
        accepted != true ||
        !_current ||
        _busy ||
        _attempted ||
        !identical(widget.source, currentSource) ||
        widget.patientId != currentPatient) {
      return;
    }
    try {
      if (jsonEncode(_draft().toJson()) != frozen || !_reviewed) {
        setState(
          () => _message = 'Form changed after review. No invoice sent.',
        );
        return;
      }
    } on Object {
      setState(
        () => _message = 'Form invalidated after review. No invoice sent.',
      );
      return;
    }
    // Disable before the first async boundary: no second click or auto replay.
    setState(() {
      _attempted = true;
      _busy = true;
      _message = null;
      _confirmed = null;
    });
    try {
      final result = await widget.source.createOnce(
        draft: draft,
        idempotencyKey: const Uuid().v4(),
      );
      if (!mounted || !_current) return;
      setState(() {
        _confirmed = result;
        _message =
            'Server confirmed invoice creation. This does not record payment.';
      });
    } on BillingInvoiceCreateScopeUnavailable {
      if (!mounted || !_current) return;
      setState(
        () => _message = 'Admin session unavailable. No additional invoice attempt is allowed.',
      );
    } on Object {
      if (!mounted || !_current) return;
      setState(
        () => _message = 'Invoice outcome unconfirmed. Verify it in the backend before any new attempt.',
      );
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final allowed =
        _current && !_busy && !_attempted && !widget.source.writeAttempted;
    return Scaffold(
      appBar: AppBar(title: const Text('Create invoice')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          Text('Patient #${widget.patientId} — admin-only invoice creation.'),
          const Text(
            'Server-generated invoice number, totals and status. No payment is recorded.',
          ),
          TextField(
            key: const ValueKey('p23i-s03-currency'),
            controller: _currency,
            enabled: allowed,
            decoration: const InputDecoration(
              labelText: 'Currency (3-letter code)',
            ),
          ),
          TextField(
            key: const ValueKey('p23i-s03-description'),
            controller: _description,
            enabled: allowed,
            decoration: const InputDecoration(
              labelText: 'Billable item description',
            ),
          ),
          TextField(
            key: const ValueKey('p23i-s03-quantity'),
            controller: _quantity,
            enabled: allowed,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: 'Quantity'),
          ),
          TextField(
            key: const ValueKey('p23i-s03-price'),
            controller: _unitPrice,
            enabled: allowed,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(labelText: 'Exact unit price'),
          ),
          TextField(
            key: const ValueKey('p23i-s03-date'),
            controller: _dueDate,
            enabled: allowed,
            decoration: const InputDecoration(
              labelText: 'Due date (optional YYYY-MM-DD)',
            ),
          ),
          CheckboxListTile(
            key: const ValueKey('p23i-s03-insurance'),
            value: _insurance,
            onChanged: allowed
                ? (value) => setState(() {
                    _insurance = value ?? false;
                    _reviewed = false;
                  })
                : null,
            title: const Text('Insurance claim'),
          ),
          if (_insurance)
            TextField(
              key: const ValueKey('p23i-s03-provider'),
              controller: _provider,
              enabled: allowed,
              decoration: const InputDecoration(
                labelText: 'Insurance provider',
              ),
            ),
          CheckboxListTile(
            key: const ValueKey('p23i-s03-reviewed'),
            value: _reviewed,
            onChanged: allowed
                ? (value) => setState(() => _reviewed = value ?? false)
                : null,
            title: const Text('I reviewed the patient and invoice details'),
          ),
          FilledButton(
            key: const ValueKey('p23i-s03-create'),
            onPressed: allowed && _reviewed ? () => unawaited(_create()) : null,
            child: const Text('Create invoice once'),
          ),
          if (_busy) const LinearProgressIndicator(),
          if (_message != null)
            Text(_message!, key: const ValueKey('p23i-s03-message')),
          if (_confirmed != null) ...<Widget>[
            Text(
              'Invoice ${_confirmed!.invoiceNumber}',
              key: const ValueKey('p23i-s03-confirmed'),
            ),
            Text(
              'Status: ${_confirmed!.status.name} • '
              'Total: ${_confirmed!.currency} ${_confirmed!.totalAmount} • '
              'Paid: ${_confirmed!.amountPaid}',
            ),
          ],
        ],
      ),
    );
  }
}
