// P23I.S08 — standalone admin review for a completed ambulance trip.
// No automatic writes, no route registration, no retries after uncertain POST.
import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_invoice_link_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_invoice_link.dart';

class AmbulanceInvoiceLinkScreen extends StatefulWidget {
  const AmbulanceInvoiceLinkScreen({super.key, required this.source});
  final AmbulanceInvoiceLinkRemoteDataSource source;

  @override
  State<AmbulanceInvoiceLinkScreen> createState() =>
      _AmbulanceInvoiceLinkScreenState();
}

class _AmbulanceInvoiceLinkScreenState
    extends State<AmbulanceInvoiceLinkScreen> {
  final _tripId = TextEditingController();
  final _invoiceId = TextEditingController();
  final _page = TextEditingController(text: '1');
  AmbulanceInvoiceLinkCommand? _command;
  AmbulanceInvoiceLinkInspection? _proof;
  AmbulanceInvoiceLinkConfirmation? _confirmed;
  bool _reviewed = false;
  bool _busy = false;
  bool _locked = false;
  String? _message;
  int _generation = 0;
  late int _guardRevision;

  @override
  void initState() {
    super.initState();
    _guardRevision =
        widget.source.ambulanceReader.sessionNavigationGuard.revision;
    widget.source.ambulanceReader.sessionNavigationGuard.addListener(
      _sessionChanged,
    );
  }

  @override
  void didUpdateWidget(covariant AmbulanceInvoiceLinkScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source)) {
      oldWidget.source.ambulanceReader.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      widget.source.ambulanceReader.sessionNavigationGuard.addListener(
        _sessionChanged,
      );
      _generation++;
      _locked = true;
      _reviewed = false;
      _command = null;
      _proof = null;
      _confirmed = null;
      _message = 'Workflow source changed. Reopen this billing action.';
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.ambulanceReader.sessionNavigationGuard.removeListener(
      _sessionChanged,
    );
    _tripId.dispose();
    _invoiceId.dispose();
    _page.dispose();
    super.dispose();
  }

  bool get _current =>
      !_locked &&
      widget
          .source
          .ambulanceReader
          .sessionNavigationGuard
          .canAccessProtectedRoutes &&
      widget.source.ambulanceReader.sessionNavigationGuard.revision ==
          _guardRevision;

  bool _accept(int generation) =>
      mounted && generation == _generation && _current;

  void _sessionChanged() {
    if (!mounted) {
      return;
    }
    _generation++;
    setState(() {
      _locked = true;
      _busy = false;
      _reviewed = false;
      _command = null;
      _proof = null;
      _confirmed = null;
      _message = 'Session or clinic changed. Reopen the workflow.';
    });
  }

  void _edited() {
    setState(() {
      _generation++;
      _reviewed = false;
      _command = null;
      _proof = null;
      _confirmed = null;
      _message = null;
    });
  }

  Future<void> _inspect() async {
    if (!_current || _busy || widget.source.writeAttempted) {
      return;
    }
    final command = AmbulanceInvoiceLinkCommand(
      tripId: int.tryParse(_tripId.text.trim()) ?? 0,
      invoiceId: int.tryParse(_invoiceId.text.trim()) ?? 0,
      invoicePage: int.tryParse(_page.text.trim()) ?? 0,
    );
    try {
      command.validate();
    } on Object {
      setState(
        () => _message = 'Enter valid trip, invoice and invoice page IDs.',
      );
      return;
    }
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _reviewed = false;
      _command = null;
      _proof = null;
      _confirmed = null;
      _message = null;
    });
    try {
      final proof = await widget.source.inspect(command);
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        _command = command;
        _proof = proof;
        _message = 'Completed trip and outstanding invoice verified. Review both identities.';
      });
    } on Object {
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        _message = 'Authorized invoice evidence unavailable. No write sent.';
      });
    }
  }

  Future<void> _link() async {
    final command = _command;
    final proof = _proof;
    if (!_current ||
        _busy ||
        !_reviewed ||
        widget.source.writeAttempted ||
        command == null ||
        proof == null) {
      return;
    }
    final generation = ++_generation;
    final approved = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('Confirm one ambulance invoice link'),
        content: Text(
          'Link invoice #${command.invoiceId} to completed '
          'trip #${command.tripId}? This cannot be automatically retried. '
          'The backend verifies patient and clinic matching.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: const Text('Back'),
          ),
          FilledButton(
            key: const ValueKey('p23i-s08-confirm-once'),
            onPressed: () => Navigator.pop(dialog, true),
            child: const Text('Confirm once'),
          ),
        ],
      ),
    );
    if (!_accept(generation) || approved != true) {
      return;
    }
    setState(() {
      _busy = true;
      _reviewed = false;
      _confirmed = null;
      _message = null;
    });
    try {
      final confirmation = await widget.source.linkOnce(
        command: command,
        inspection: proof,
      );
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        _confirmed = confirmation;
        _message = 'Backend confirmed the invoice link.';
      });
    } on Object {
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        _message = widget.source.writeAttempted
            ? 'Outcome unconfirmed. Do not resubmit. Verify through authorized operations.'
            : 'Evidence changed or authorization failed. No write sent.';
      });
    }
  }

  Widget _field(String label, String key, TextEditingController controller) =>
      TextField(
        key: ValueKey(key),
        controller: controller,
        enabled: _current && !_busy && !widget.source.writeAttempted,
        onChanged: (_) => _edited(),
        keyboardType: TextInputType.number,
        decoration: InputDecoration(labelText: label),
      );

  @override
  Widget build(BuildContext context) {
    final proof = _proof;
    return Scaffold(
      appBar: AppBar(title: const Text('Ambulance invoice linking')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            const Text(
              'Admin-only. Completed trips and outstanding invoices only. '
              'Paid invoices are not discoverable through the available billing API.',
            ),
            _field('Completed trip ID', 'p23i-s08-trip-id', _tripId),
            _field('Outstanding invoice ID', 'p23i-s08-invoice-id', _invoiceId),
            _field('Invoice list page', 'p23i-s08-page', _page),
            FilledButton(
              key: const ValueKey('p23i-s08-inspect'),
              onPressed: _current && !_busy && !widget.source.writeAttempted
                  ? _inspect
                  : null,
              child: const Text('Inspect server records'),
            ),
            if (proof != null) ...<Widget>[
              Text(
                'Trip #${proof.trip.id} — ${proof.trip.status} — '
                'patient #${proof.trip.patientId} — clinic #${proof.trip.clinicId}',
              ),
              Text(
                'Invoice #${proof.invoice.id} (${proof.invoice.invoiceNumber}) — '
                '${proof.invoice.currency ?? 'currency unavailable'} '
                '${proof.invoice.totalAmount} — ${proof.invoice.status.name}',
              ),
              CheckboxListTile(
                key: const ValueKey('p23i-s08-reviewed'),
                title: const Text(
                  'I reviewed both server records and their matching patient/clinic.',
                ),
                value: _reviewed,
                onChanged: _current && !_busy && !widget.source.writeAttempted
                    ? (value) => setState(() => _reviewed = value ?? false)
                    : null,
              ),
            ],
            FilledButton(
              key: const ValueKey('p23i-s08-submit'),
              onPressed:
                  _current &&
                      !_busy &&
                      _reviewed &&
                      proof != null &&
                      !widget.source.writeAttempted
                  ? _link
                  : null,
              child: const Text('Link invoice once'),
            ),
            if (_message != null)
              Text(_message!, key: const ValueKey('p23i-s08-message')),
            if (_confirmed != null)
              Text(
                'Server confirmed trip #${_confirmed!.trip.id} → '
                'invoice #${_confirmed!.trip.invoiceId}',
                key: const ValueKey('p23i-s08-confirmed'),
              ),
          ],
        ),
      ),
    );
  }
}
