// P23I.S09: guarded navigation entry only. Backend owns all write authority.
// Mutation pages have no public GoRouter paths; entry requires this workspace.
import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/billing/data/billing_outstanding_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/models/outstanding_invoice.dart';
import 'package:clinic_system_pro/features/billing/data/billing_invoice_create_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/billing_payment_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/presentation/billing_outstanding_screen.dart';
import 'package:clinic_system_pro/features/billing/presentation/billing_invoice_create_screen.dart';
import 'package:clinic_system_pro/features/billing/presentation/billing_payment_screen.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_dispatch_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_lifecycle_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_invoice_link_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/presentation/ambulance_read_screen.dart';
import 'package:clinic_system_pro/features/ambulance/presentation/ambulance_dispatch_screen.dart';
import 'package:clinic_system_pro/features/ambulance/presentation/ambulance_lifecycle_screen.dart';
import 'package:clinic_system_pro/features/ambulance/presentation/ambulance_invoice_link_screen.dart';

enum P23IWorkspaceMode { billing, ambulance }

/// A navigation suggestion and verified-session gate, never an RBAC grant.
/// The server and each P23I adapter recheck authority immediately before IO.
class P23IWorkspaceScreen extends StatefulWidget {
  const P23IWorkspaceScreen({
    super.key,
    required this.mode,
    required this.guard,
    required this.loadSession,
    required this.billingReader,
    required this.ambulanceReader,
    required this.invoiceCreationSource,
    required this.paymentSource,
    required this.dispatchSource,
    required this.lifecycleSource,
    required this.invoiceLinkSource,
  });

  final P23IWorkspaceMode mode;
  final SessionNavigationGuard guard;
  final Future<AuthSessionState> Function() loadSession;
  final BillingOutstandingRemoteDataSource billingReader;
  final AmbulanceReadRemoteDataSource ambulanceReader;
  final BillingInvoiceCreateRemoteDataSource Function(int patientId)
  invoiceCreationSource;
  final BillingPaymentRemoteDataSource Function(int invoiceId) paymentSource;
  final AmbulanceDispatchRemoteDataSource Function() dispatchSource;
  final AmbulanceLifecycleRemoteDataSource Function() lifecycleSource;
  final AmbulanceInvoiceLinkRemoteDataSource Function() invoiceLinkSource;

  @override
  State<P23IWorkspaceScreen> createState() => _P23IWorkspaceScreenState();
}

class _P23IWorkspaceScreenState extends State<P23IWorkspaceScreen> {
  static const _ambulanceViewRoles = <String>{
    'admin',
    'ambulance_coordinator',
    'ambulance_dispatcher',
    'driver',
    'paramedic',
    'emt',
  };
  static const _dispatchRoles = <String>{
    'admin',
    'ambulance_coordinator',
    'ambulance_dispatcher',
  };

  String? _role;
  int? _clinicId;
  int _generation = 0;
  int _revision = -1;
  bool _loading = true;
  bool _busy = false;
  String? _message;

  bool get _active =>
      widget.guard.canAccessProtectedRoutes &&
      widget.guard.revision == _revision &&
      _role != null &&
      _clinicId != null &&
      _clinicId! > 0;

  bool get _authorized =>
      _active &&
      (widget.mode == P23IWorkspaceMode.billing
          ? _role == 'admin'
          : _ambulanceViewRoles.contains(_role));

  @override
  void initState() {
    super.initState();
    widget.guard.addListener(_guardChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant P23IWorkspaceScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.guard, widget.guard)) {
      oldWidget.guard.removeListener(_guardChanged);
      widget.guard.addListener(_guardChanged);
    }
    if (oldWidget.mode != widget.mode ||
        !identical(oldWidget.guard, widget.guard) ||
        !identical(oldWidget.loadSession, widget.loadSession)) {
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.guard.removeListener(_guardChanged);
    super.dispose();
  }

  void _guardChanged() {
    if (!mounted) {
      return;
    }
    _generation++;
    setState(() {
      _role = null;
      _clinicId = null;
      _message = null;
      _busy = false;
      _revision = -1;
      _loading = true;
    });
    if (widget.guard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  Future<void> _load() async {
    final generation = ++_generation;
    final revision = widget.guard.revision;
    if (!mounted) {
      return;
    }
    setState(() {
      _role = null;
      _clinicId = null;
      _revision = -1;
      _loading = true;
      _message = null;
    });
    if (!widget.guard.canAccessProtectedRoutes) {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
      return;
    }
    try {
      final state = await widget.loadSession();
      if (!mounted ||
          generation != _generation ||
          widget.guard.revision != revision ||
          !widget.guard.canAccessProtectedRoutes) {
        return;
      }
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken.trim() != session.accessToken) {
        setState(() => _loading = false);
        return;
      }
      setState(() {
        _role = session.role.trim().toLowerCase();
        _clinicId = session.clinicContextId;
        _revision = revision;
        _loading = false;
      });
    } on Object {
      if (mounted &&
          generation == _generation &&
          widget.guard.revision == revision) {
        setState(() => _loading = false);
      }
    }
  }

  void _open(Widget page) {
    if (!_authorized || _busy) {
      return;
    }
    unawaited(
      Navigator.of(context)
          .push<void>(MaterialPageRoute<void>(builder: (context) => page))
          .then<void>((_) {}),
    );
  }

  Future<void> _createInvoice() async {
    if (!_authorized || _role != 'admin' || _busy) {
      return;
    }
    final revision = widget.guard.revision;
    String rawId = '';
    final patientId = await showDialog<int>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('Choose patient for invoice'),
        content: TextField(
          key: const ValueKey('p23i-patient-id'),
          keyboardType: TextInputType.number,
          onChanged: (value) => rawId = value.trim(),
          decoration: const InputDecoration(labelText: 'Patient ID'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialog),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () {
              final id = int.tryParse(rawId);
              if (id == null || id <= 0 || id > 2147483647) {
                return;
              }
              Navigator.pop(dialog, id);
            },
            child: const Text('Continue'),
          ),
        ],
      ),
    );
    if (!mounted ||
        !_authorized ||
        widget.guard.revision != revision ||
        patientId == null) {
      return;
    }
    _open(
      BillingInvoiceCreateScreen(
        source: widget.invoiceCreationSource(patientId),
        patientId: patientId,
      ),
    );
  }

  Future<void> _choosePayment() async {
    if (!_authorized || _role != 'admin' || _busy) {
      return;
    }
    final revision = widget.guard.revision;
    setState(() => _busy = true);
    try {
      var page = 1;
      const perPage = 20;
      while (mounted && _authorized && widget.guard.revision == revision) {
        final items = await widget.billingReader.fetchOutstanding(
          page: page,
          perPage: perPage,
        );
        if (!mounted || !_authorized || widget.guard.revision != revision) {
          return;
        }
        final selected = await showDialog<Object>(
          context: context,
          builder: (dialog) => SimpleDialog(
            title: Text('Outstanding invoices — page $page'),
            children: [
              if (items.items.isEmpty)
                const Padding(
                  padding: EdgeInsets.all(16),
                  child: Text('No outstanding invoices on this page.'),
                ),
              ...items.items.map(
                (invoice) => SimpleDialogOption(
                  onPressed: () => Navigator.pop(dialog, invoice),
                  child: Text(
                    '${invoice.invoiceNumber} · patient #${invoice.patientId} '
                    '· ${invoice.currency ?? 'currency unavailable'}',
                  ),
                ),
              ),
              if (page > 1)
                SimpleDialogOption(
                  onPressed: () => Navigator.pop(dialog, -1),
                  child: const Text('Previous page'),
                ),
              if (items.hasNext)
                SimpleDialogOption(
                  onPressed: () => Navigator.pop(dialog, 1),
                  child: const Text('Next page'),
                ),
              SimpleDialogOption(
                onPressed: () => Navigator.pop(dialog),
                child: const Text('Cancel'),
              ),
            ],
          ),
        );
        if (!mounted || !_authorized || widget.guard.revision != revision) {
          return;
        }
        if (selected == 1) {
          page++;
          continue;
        }
        if (selected == -1) {
          page--;
          continue;
        }
        if (selected is OutstandingInvoice) {
          // The payment adapter re-reads the same page and compares the row
          // immediately before POST. This picker cannot create write authority.
          final invoice = selected;
          setState(() => _busy = false);
          _open(
            BillingPaymentScreen(
              source: widget.paymentSource(invoice.id),
              invoice: invoice,
              sourcePage: page,
              sourcePerPage: perPage,
            ),
          );
        }
        return;
      }
    } on Object {
      if (mounted && _authorized && widget.guard.revision == revision) {
        setState(
          () => _message =
              'Outstanding invoice evidence unavailable. No payment started.',
        );
      }
    } finally {
      if (mounted && widget.guard.revision == revision) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final title = widget.mode == P23IWorkspaceMode.billing
        ? 'Billing workspace'
        : 'Ambulance workspace';
    if (_loading) {
      return Scaffold(
        appBar: AppBar(title: Text(title)),
        body: const Center(child: CircularProgressIndicator()),
      );
    }
    if (!_authorized) {
      return Scaffold(
        appBar: AppBar(title: Text(title)),
        body: Center(
          child: Text(
            widget.mode == P23IWorkspaceMode.billing
                ? 'Billing access unavailable.'
                : 'Ambulance access unavailable.',
          ),
        ),
      );
    }
    final billing = widget.mode == P23IWorkspaceMode.billing;
    return Scaffold(
      key: ValueKey('p23i-workspace-${widget.mode.name}'),
      appBar: AppBar(title: Text(title)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (billing) ...[
            ListTile(
              key: const ValueKey('p23i-billing-outstanding'),
              title: const Text('Outstanding invoices'),
              subtitle: const Text('Server-owned outstanding balances only'),
              onTap: () => _open(
                BillingOutstandingScreen(dataSource: widget.billingReader),
              ),
            ),
            ListTile(
              key: const ValueKey('p23i-billing-create'),
              title: const Text('Create invoice'),
              subtitle: const Text(
                'Confirm a specific patient and billable items',
              ),
              onTap: _createInvoice,
            ),
            ListTile(
              key: const ValueKey('p23i-billing-payment'),
              title: const Text('Record or initialize payment'),
              subtitle: const Text(
                'Choose a server-listed outstanding invoice',
              ),
              onTap: _choosePayment,
            ),
          ] else ...[
            ListTile(
              key: const ValueKey('p23i-ambulance-read'),
              title: const Text('Fleet and trips'),
              subtitle: const Text('Server-confirmed ambulance status'),
              onTap: () => _open(
                AmbulanceReadScreen(dataSource: widget.ambulanceReader),
              ),
            ),
            if (_dispatchRoles.contains(_role))
              ListTile(
                key: const ValueKey('p23i-ambulance-dispatch'),
                title: const Text('Request or dispatch trip'),
                subtitle: const Text(
                  'One-shot confirmation and fresh evidence',
                ),
                onTap: () => _open(
                  AmbulanceDispatchScreen(source: widget.dispatchSource()),
                ),
              ),
            ListTile(
              key: const ValueKey('p23i-ambulance-lifecycle'),
              title: const Text('Crew and trip lifecycle'),
              subtitle: const Text('Server-authorized trip and fleet actions'),
              onTap: () => _open(
                AmbulanceLifecycleScreen(source: widget.lifecycleSource()),
              ),
            ),
            if (_role == 'admin')
              ListTile(
                key: const ValueKey('p23i-ambulance-invoice-link'),
                title: const Text('Link completed trip to invoice'),
                subtitle: const Text('Admin-only matching clinic and patient'),
                onTap: () => _open(
                  AmbulanceInvoiceLinkScreen(
                    source: widget.invoiceLinkSource(),
                  ),
                ),
              ),
          ],
          if (_busy) const Center(child: CircularProgressIndicator()),
          if (_message != null)
            Padding(padding: const EdgeInsets.all(8), child: Text(_message!)),
        ],
      ),
    );
  }
}
