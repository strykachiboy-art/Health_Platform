import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/billing/data/billing_outstanding_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/models/outstanding_invoice.dart';

/// P23I.S02 standalone read-only admin workspace; routing is a later gate.
class BillingOutstandingScreen extends StatefulWidget {
  const BillingOutstandingScreen({super.key, required this.dataSource});

  final BillingOutstandingRemoteDataSource dataSource;

  @override
  State<BillingOutstandingScreen> createState() =>
      _BillingOutstandingScreenState();
}

class _BillingOutstandingScreenState extends State<BillingOutstandingScreen> {
  OutstandingInvoicePage? _snapshot;
  List<OutstandingInvoice> _items = const [];
  bool _loading = false;
  String? _error;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant BillingOutstandingScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (identical(oldWidget.dataSource, widget.dataSource)) return;
    oldWidget.dataSource.sessionNavigationGuard.removeListener(
      _onSessionChanged,
    );
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
    _generation++;
    _clear();
    _loading = false;
    _error = null;
    unawaited(_load());
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onSessionChanged);
    super.dispose();
  }

  void _clear() {
    _snapshot = null;
    _items = const [];
  }

  void _onSessionChanged() {
    if (!mounted) return;
    _generation++;
    setState(() {
      _clear();
      _loading = false;
      _error = 'Billing records cleared after session change.';
    });
    // Explicit refresh after switching clinics or signing in; no silent reuse.
  }

  bool _current(
    BillingOutstandingRemoteDataSource source,
    int generation,
    int revision,
  ) =>
      mounted &&
      identical(widget.dataSource, source) &&
      generation == _generation &&
      source.sessionNavigationGuard.canAccessProtectedRoutes &&
      source.sessionNavigationGuard.revision == revision;

  String _safeError(Object failure) {
    if (failure is BillingReadSessionUnavailable) {
      return 'Billing access unavailable for this session or role.';
    }
    if (failure is ApiException &&
        const <int>{401, 403, 404}.contains(failure.statusCode)) {
      return 'Billing access denied or invoices unavailable.';
    }
    return 'Outstanding invoices could not be verified from the server.';
  }

  Future<void> _load({bool more = false}) async {
    if (_loading) return;
    final previous = _snapshot;
    if (more && (previous == null || !previous.hasNext)) return;
    final source = widget.dataSource;
    final revision = source.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _loading = true;
      _error = null;
      if (!more) _clear();
    });
    try {
      final next = await source.fetchOutstanding(
        page: more ? previous!.page + 1 : 1,
      );
      if (!_current(source, generation, revision)) return;
      final combined = more
          ? <OutstandingInvoice>[..._items, ...next.items]
          : <OutstandingInvoice>[...next.items];
      if ((more && previous!.total != next.total) ||
          combined.length > next.total ||
          combined.map((item) => item.id).toSet().length != combined.length) {
        throw const FormatException('Inconsistent invoice pages.');
      }
      setState(() {
        _items = List<OutstandingInvoice>.unmodifiable(combined);
        _snapshot = next;
        _loading = false;
      });
    } catch (failure) {
      if (!_current(source, generation, revision)) return;
      setState(() {
        _clear();
        _loading = false;
        _error = _safeError(failure);
      });
    }
  }

  String _status(OutstandingInvoiceStatus status) => switch (status) {
    OutstandingInvoiceStatus.issued => 'Issued',
    OutstandingInvoiceStatus.partiallyPaid => 'Partially paid',
    OutstandingInvoiceStatus.overdue => 'Overdue',
  };

  @override
  Widget build(BuildContext context) {
    final page = _snapshot;
    final allowed =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;
    return Scaffold(
      appBar: AppBar(title: const Text('Outstanding invoices')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            'Admin-only, server-confirmed outstanding invoices. Read-only; no payment action.',
          ),
          if (_error != null)
            Text(_error!, key: const ValueKey('p23i-s02-error')),
          if (_loading && page == null)
            const Center(child: CircularProgressIndicator()),
          if (page != null) ...[
            Text(
              'Invoices shown: ${_items.length} of ${page.total}',
              key: const ValueKey('p23i-s02-count'),
            ),
            if (_items.isEmpty)
              const Text(
                'The server returned no outstanding invoices for this clinic.',
                key: ValueKey('p23i-s02-empty'),
              ),
            for (final invoice in _items)
              Card(
                key: ValueKey('p23i-s02-invoice-${invoice.id}'),
                child: ListTile(
                  title: Text('Invoice ${invoice.invoiceNumber}'),
                  subtitle: Text(
                    'Patient ${invoice.patientId} • ${_status(invoice.status)}\n'
                    'Total: ${invoice.currency ?? 'Currency unspecified'} ${invoice.totalAmount}\n'
                    'Paid: ${invoice.amountPaid}\n'
                    'Due: ${invoice.dueDate ?? 'Not set'}',
                  ),
                  isThreeLine: true,
                ),
              ),
            if (page.hasNext && !_loading)
              OutlinedButton(
                key: const ValueKey('p23i-s02-more'),
                onPressed: allowed ? () => unawaited(_load(more: true)) : null,
                child: const Text('Load more invoices'),
              ),
          ],
          if (_loading && page != null)
            const Center(child: CircularProgressIndicator()),
          if (!_loading && allowed)
            OutlinedButton(
              key: const ValueKey('p23i-s02-refresh'),
              onPressed: () => unawaited(_load()),
              child: const Text('Refresh outstanding invoices'),
            ),
        ],
      ),
    );
  }
}
