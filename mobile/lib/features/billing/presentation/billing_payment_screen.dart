import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';
import 'package:clinic_system_pro/features/billing/data/billing_payment_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/models/billing_payment.dart';
import 'package:clinic_system_pro/features/billing/data/models/outstanding_invoice.dart';

/// P23I.S04 — standalone, unrouted admin payment workflow. A selected
/// outstanding invoice is revalidated against its original page before POST.
class BillingPaymentScreen extends StatefulWidget {
  const BillingPaymentScreen({
    super.key,
    required this.source,
    required this.invoice,
    required this.sourcePage,
    required this.sourcePerPage,
  }) : assert(sourcePage > 0),
       assert(sourcePerPage > 0);

  final BillingPaymentRemoteDataSource source;
  final OutstandingInvoice invoice;
  final int sourcePage;
  final int sourcePerPage;

  @override
  State<BillingPaymentScreen> createState() => _BillingPaymentScreenState();
}

class _BillingPaymentScreenState extends State<BillingPaymentScreen> {
  final _amount = TextEditingController();
  final _reference = TextEditingController();
  BillingPaymentFlow _flow = BillingPaymentFlow.gatewayInitialize;
  String _method = 'card';
  String _gateway = 'paystack';
  bool _reviewed = false;
  bool _attempted = false;
  bool _busy = false;
  String? _message;
  BillingPaymentConfirmation? _confirmed;
  late int _revision;

  @override
  void initState() {
    super.initState();
    _revision = widget.source.reader.sessionNavigationGuard.revision;
    widget.source.reader.sessionNavigationGuard.addListener(_sessionChanged);
    _amount.addListener(_invalidateReview);
    _reference.addListener(_invalidateReview);
  }

  @override
  void didUpdateWidget(covariant BillingPaymentScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source) ||
        oldWidget.invoice.id != widget.invoice.id ||
        oldWidget.invoice.clinicId != widget.invoice.clinicId ||
        oldWidget.invoice.patientId != widget.invoice.patientId ||
        oldWidget.sourcePage != widget.sourcePage ||
        oldWidget.sourcePerPage != widget.sourcePerPage) {
      oldWidget.source.reader.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      widget.source.reader.sessionNavigationGuard.addListener(_sessionChanged);
      _revision = widget.source.reader.sessionNavigationGuard.revision;
      _attempted = true;
      _reviewed = false;
      _confirmed = null;
      _message = 'Invoice or payment source changed. Reopen workflow.';
      _amount.clear();
      _reference.clear();
    }
  }

  @override
  void dispose() {
    widget.source.reader.sessionNavigationGuard.removeListener(_sessionChanged);
    _amount.dispose();
    _reference.dispose();
    super.dispose();
  }

  bool get _current =>
      widget.source.reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.source.reader.sessionNavigationGuard.revision == _revision;

  void _invalidateReview() {
    if (_reviewed && mounted) setState(() => _reviewed = false);
  }

  void _sessionChanged() {
    if (!mounted) return;
    setState(() {
      _reviewed = false;
      _attempted = true;
      _confirmed = null;
      _message = 'Session changed; payment data cleared. Reopen workflow.';
    });
    _amount.clear();
    _reference.clear();
  }

  BillingPaymentDraft _draft() {
    final draft = BillingPaymentDraft(
      invoice: widget.invoice,
      flow: _flow,
      amount: _amount.text.trim(),
      method: _method,
      gateway: _flow == BillingPaymentFlow.gatewayInitialize ? _gateway : null,
      reference: _reference.text.trim().isEmpty ? null : _reference.text.trim(),
    );
    draft.toJson();
    return draft;
  }

  Future<void> _submit() async {
    if (!_current ||
        _busy ||
        _attempted ||
        widget.source.writeAttempted ||
        !_reviewed) {
      return;
    }
    late final BillingPaymentDraft draft;
    late final String snapshot;
    try {
      draft = _draft();
      snapshot = jsonEncode(draft.toJson());
    } on Object {
      setState(
        () => _message =
            'Check payment mode, method, balance and amount. No write sent.',
      );
      return;
    }
    final currentSource = widget.source;
    final invoiceId = widget.invoice.id;
    final sourcePage = widget.sourcePage;
    final sourcePerPage = widget.sourcePerPage;
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('Confirm financial payment operation'),
        content: Text(
          'Invoice #${draft.invoice.id}, patient #${draft.invoice.patientId}, '
          '${draft.invoice.currency} ${draft.amount} via ${draft.method}. '
          '${draft.flow == BillingPaymentFlow.gatewayInitialize ? 'Initialization does not prove provider settlement. ' : 'This directly records a manual payment. '}'
          'Check the server before another attempt if the outcome is uncertain.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: const Text('Back'),
          ),
          FilledButton(
            key: const ValueKey('p23i-s04-dialog-confirm'),
            onPressed: () => Navigator.pop(dialog, true),
            child: const Text('Submit once'),
          ),
        ],
      ),
    );
    if (!mounted ||
        accepted != true ||
        !_current ||
        _busy ||
        _attempted ||
        !_reviewed ||
        !identical(widget.source, currentSource) ||
        widget.invoice.id != invoiceId ||
        widget.sourcePage != sourcePage ||
        widget.sourcePerPage != sourcePerPage) {
      return;
    }
    try {
      if (jsonEncode(_draft().toJson()) != snapshot) {
        setState(() => _message = 'Payment details changed. No write sent.');
        return;
      }
    } on Object {
      setState(() => _message = 'Payment review expired. No write sent.');
      return;
    }
    setState(() {
      _attempted = true;
      _busy = true;
      _confirmed = null;
      _message = null;
    });
    try {
      final result = await currentSource.submitOnce(
        draft: draft,
        idempotencyKey: const Uuid().v4(),
        sourcePage: sourcePage,
        sourcePerPage: sourcePerPage,
      );
      if (!mounted || !_current) return;
      setState(() {
        _confirmed = result;
        _message = result.pendingProviderConfirmation
            ? 'Payment is PENDING provider confirmation. Do not mark paid.'
            : 'The backend confirmed this payment. No second write permitted.';
      });
    } on BillingPaymentEvidenceStale {
      if (!mounted || !_current) return;
      setState(
        () => _message = 'Invoice changed or left outstanding list. No financial write sent.',
      );
    } on BillingPaymentScopeUnavailable {
      if (!mounted || !_current) return;
      setState(() => _message = 'Admin session unavailable. Reopen workflow.');
    } on Object {
      if (!mounted || !_current) return;
      setState(
        () => _message = 'Payment outcome UNCONFIRMED. Reconcile with backend/provider manually. No automatic retry.',
      );
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final allowed =
        _current && !_attempted && !_busy && !widget.source.writeAttempted;
    final gatewayFlow = _flow == BillingPaymentFlow.gatewayInitialize;
    return Scaffold(
      appBar: AppBar(title: const Text('Controlled payment')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          Text(
            'Invoice ${widget.invoice.invoiceNumber} • patient #${widget.invoice.patientId}',
          ),
          Text(
            'Status: ${widget.invoice.status.name} • '
            'Total: ${widget.invoice.currency} ${widget.invoice.totalAmount} • '
            'Already paid: ${widget.invoice.amountPaid}',
          ),
          const Text(
            'Finance operations are admin-only and online-only. '
            'The server validates balance and owns all payment status.',
          ),
          ChoiceChip(
            key: const ValueKey('p23i-s04-mode-gateway'),
            label: const Text('Initialize provider payment'),
            selected: gatewayFlow,
            onSelected: allowed
                ? (_) => setState(() {
                    _flow = BillingPaymentFlow.gatewayInitialize;
                    _method = 'card';
                    _reviewed = false;
                  })
                : null,
          ),
          ChoiceChip(
            key: const ValueKey('p23i-s04-mode-direct'),
            label: const Text('Record direct payment'),
            selected: !gatewayFlow,
            onSelected: allowed
                ? (_) => setState(() {
                    _flow = BillingPaymentFlow.directRecord;
                    _method = 'cash';
                    _reviewed = false;
                  })
                : null,
          ),
          DropdownButton<String>(
            key: const ValueKey('p23i-s04-method'),
            value: _method,
            onChanged: allowed
                ? (value) => setState(() {
                    _method = value ?? _method;
                    _reviewed = false;
                  })
                : null,
            items:
                (gatewayFlow
                        ? const <String>[
                            'card',
                            'bank_transfer',
                            'mobile_money',
                          ]
                        : const <String>['cash', 'insurance'])
                    .map(
                      (value) => DropdownMenuItem<String>(
                        value: value,
                        child: Text(value),
                      ),
                    )
                    .toList(growable: false),
          ),
          if (gatewayFlow)
            DropdownButton<String>(
              key: const ValueKey('p23i-s04-gateway'),
              value: _gateway,
              onChanged: allowed
                  ? (value) => setState(() {
                      _gateway = value ?? _gateway;
                      _reviewed = false;
                    })
                  : null,
              items: const <String>['paystack', 'flutterwave', 'stripe']
                  .map(
                    (value) => DropdownMenuItem<String>(
                      value: value,
                      child: Text(value),
                    ),
                  )
                  .toList(growable: false),
            ),
          TextField(
            key: const ValueKey('p23i-s04-amount'),
            controller: _amount,
            enabled: allowed,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(
              labelText: 'Exact payment amount',
            ),
          ),
          TextField(
            key: const ValueKey('p23i-s04-reference'),
            controller: _reference,
            enabled: allowed,
            decoration: const InputDecoration(
              labelText: 'Payment reference (optional)',
            ),
          ),
          CheckboxListTile(
            key: const ValueKey('p23i-s04-reviewed'),
            value: _reviewed,
            onChanged: allowed
                ? (value) => setState(() => _reviewed = value ?? false)
                : null,
            title: const Text(
              'I verified invoice, amount, method and payment authority',
            ),
          ),
          FilledButton(
            key: const ValueKey('p23i-s04-submit'),
            onPressed: allowed && _reviewed ? () => unawaited(_submit()) : null,
            child: Text(gatewayFlow ? 'Initialize once' : 'Record once'),
          ),
          if (_busy) const LinearProgressIndicator(),
          if (_message != null)
            Text(_message!, key: const ValueKey('p23i-s04-message')),
          if (_confirmed != null) ...<Widget>[
            Text(
              'Server payment #${_confirmed!.id}',
              key: const ValueKey('p23i-s04-confirmed'),
            ),
            Text(
              'Status: ${_confirmed!.status.name} • '
              'Amount: ${_confirmed!.amount} • '
              'Invoice: ${_confirmed!.invoiceId}',
            ),
          ],
        ],
      ),
    );
  }
}
