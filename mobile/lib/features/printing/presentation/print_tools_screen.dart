import 'dart:async';

import 'package:flutter/material.dart';

import '../data/print_tools_remote.dart';

class PrintToolsScreen extends StatefulWidget {
  const PrintToolsScreen({super.key, required this.source});
  final PrintToolsRemote source;
  @override
  State<PrintToolsScreen> createState() => _PrintToolsScreenState();
}

class _PrintToolsScreenState extends State<PrintToolsScreen> {
  final _token = TextEditingController();
  final _hash = TextEditingController();
  final _payment = TextEditingController();
  int _paper = 80, _generation = 0;
  bool _busy = false, _thermal = false;
  String? _message;
  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_invalidate);
    if (widget.source.staging) {
      unawaited(_load());
    }
  }

  @override
  void didUpdateWidget(covariant PrintToolsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source)) {
      oldWidget.source.guard.removeListener(_invalidate);
      widget.source.guard.addListener(_invalidate);
      _invalidate();
    }
  }

  void _invalidate() {
    _generation++;
    if (mounted) {
      setState(() {
        _token.clear();
        _hash.clear();
        _payment.clear();
        _busy = false;
        _thermal = false;
        _message = 'Session changed. Reopen document tools.';
      });
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.guard.removeListener(_invalidate);
    _token.dispose();
    _hash.dispose();
    _payment.dispose();
    super.dispose();
  }

  bool _active(int g, int r) =>
      mounted &&
      _generation == g &&
      widget.source.guard.revision == r &&
      widget.source.guard.canAccessProtectedRoutes;
  Future<void> _load() async {
    final g = ++_generation;
    final r = widget.source.guard.revision;
    try {
      final allowed = await widget.source.canValidateThermal(
        () => _active(g, r),
      );
      if (_active(g, r)) {
        setState(() => _thermal = allowed);
      }
    } on Object {
      /* No role grant on failure. */
    }
  }

  Future<void> _run({required bool thermal}) async {
    if (_busy || !widget.source.staging || (thermal && !_thermal)) {
      return;
    }
    final g = ++_generation;
    final r = widget.source.guard.revision;
    bool active() => _active(g, r);
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      if (thermal) {
        final id = int.tryParse(_payment.text.trim()) ?? 0;
        final approved = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('Validate payment stream?'),
            content: Text(
              'One online request for payment #$id, $_paper mm. No printing or dispatch.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Confirm'),
              ),
            ],
          ),
        );
        if (approved != true || !active()) {
          return;
        }
        final receipt = await widget.source.thermal(id, _paper, active);
        if (active()) {
          setState(
            () => _message =
                'Validated ${receipt.paper} mm ESC/POS stream (${receipt.length} bytes). SHA-256: ${receipt.sha256}. No print job sent.',
          );
        }
      } else {
        final value = _hash.text.trim().toLowerCase();
        final receipt = await widget.source.verify(
          _token.text.trim().toLowerCase(),
          value.isEmpty ? null : value,
          active,
        );
        if (active()) {
          setState(
            () => _message =
                'Historical issuance found. ${receipt.hashMatch == null
                    ? 'Exact PDF hash not checked.'
                    : receipt.hashMatch!
                    ? 'Submitted PDF hash matches.'
                    : 'Submitted PDF hash does not match.'}\n${HistoricalVerification.notice}',
          );
        }
      }
    } on Object {
      if (active()) {
        setState(
          () => _message = 'Verification or stream validation unavailable. No print job sent. Check the server before another request.',
        );
      }
    } finally {
      if (active()) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Document verification and thermal proof'),
    ),
    body: !widget.source.staging
        ? const Center(
            child: Text('Document tools locked pending certification.'),
          )
        : ListView(
            padding: const EdgeInsets.all(20),
            children: [
              const Text(HistoricalVerification.notice),
              TextField(
                controller: _token,
                enabled: !_busy,
                maxLength: 32,
                decoration: const InputDecoration(
                  labelText:
                      'Verification reference (last 32 characters of QR URL)',
                ),
              ),
              TextField(
                controller: _hash,
                enabled: !_busy,
                maxLength: 64,
                decoration: const InputDecoration(
                  labelText: 'Optional exact PDF SHA-256 (not source SHA-256)',
                ),
              ),
              FilledButton(
                onPressed: _busy ? null : () => unawaited(_run(thermal: false)),
                child: const Text('Verify issuance'),
              ),
              if (_thermal) ...[
                const Divider(),
                const Text(
                  'Thermal payment-record validation only. Physical and silent printing are locked.',
                ),
                TextField(
                  controller: _payment,
                  enabled: !_busy,
                  keyboardType: TextInputType.number,
                  maxLength: 10,
                  decoration: const InputDecoration(
                    labelText: 'Payment record ID (not invoice ID)',
                  ),
                ),
                Wrap(
                  spacing: 12,
                  children: [
                    ChoiceChip(
                      label: const Text('58 mm'),
                      selected: _paper == 58,
                      onSelected: _busy
                          ? null
                          : (_) => setState(() => _paper = 58),
                    ),
                    ChoiceChip(
                      label: const Text('80 mm'),
                      selected: _paper == 80,
                      onSelected: _busy
                          ? null
                          : (_) => setState(() => _paper = 80),
                    ),
                  ],
                ),
                OutlinedButton(
                  onPressed: _busy
                      ? null
                      : () => unawaited(_run(thermal: true)),
                  child: const Text('Validate thermal stream'),
                ),
              ],
              if (_busy) const LinearProgressIndicator(),
              if (_message != null)
                Text(_message!, key: const ValueKey('document-tools-result')),
            ],
          ),
  );
}
