import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/printing/data/rad7_print_contract.dart';

/// The shared hospital print workspace is visible, but RAD6 backend downloads
/// are currently disabled. No URL launching, file writes, PDF caching, or
/// raw Orthanc access can be triggered by this screen.
class Rad7PrintingScreen extends StatefulWidget {
  const Rad7PrintingScreen({
    super.key,
    required this.dashboard,
    required this.guard,
  });

  final DashboardRemoteDataSource dashboard;
  final SessionNavigationGuard guard;

  @override
  State<Rad7PrintingScreen> createState() => _Rad7PrintingScreenState();
}

class _Rad7PrintingScreenState extends State<Rad7PrintingScreen> {
  final _recordId = TextEditingController();
  List<Rad7PrintKind> _kinds = const <Rad7PrintKind>[];
  Rad7PrintKind? _selected;
  String? _notice;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.guard.addListener(_onSession);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant Rad7PrintingScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.guard, widget.guard)) {
      oldWidget.guard.removeListener(_onSession);
      widget.guard.addListener(_onSession);
    }
    if (!identical(oldWidget.guard, widget.guard) ||
        !identical(oldWidget.dashboard, widget.dashboard)) {
      _onSession();
    }
  }

  void _onSession() {
    _generation++;
    _recordId.clear();
    if (!mounted) return;
    setState(() {
      _kinds = const <Rad7PrintKind>[];
      _selected = null;
      _notice = null;
    });
    if (widget.guard.canAccessProtectedRoutes) unawaited(_load());
  }

  Future<void> _load() async {
    final gen = ++_generation;
    final revision = widget.guard.revision;
    if (!widget.guard.canAccessProtectedRoutes) return;
    try {
      final state = await widget.dashboard.loadSession();
      final actor = state.session;
      if (!mounted ||
          gen != _generation ||
          revision != widget.guard.revision ||
          !widget.guard.canAccessProtectedRoutes) {
        return;
      }
      if (state.disposition != AuthSessionDisposition.ready ||
          actor == null ||
          actor.userId <= 0 ||
          actor.clinicContextId == null ||
          actor.clinicContextId! <= 0 ||
          actor.accessToken.isEmpty) {
        return;
      }
      final kinds = Rad7PrintContract.permitted(actor.role);
      setState(() {
        _kinds = kinds;
        _selected = kinds.isEmpty ? null : kinds.first;
      });
    } on Object {
      if (mounted && gen == _generation && revision == widget.guard.revision) {
        setState(() => _notice = 'Print access unavailable.');
      }
    }
  }

  @override
  void dispose() {
    widget.guard.removeListener(_onSession);
    ++_generation;
    _recordId.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Hospital document printing')),
    body: SafeArea(
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            'Printing integration is protected until the backend release gate is approved.',
            key: ValueKey('rad7-print-release-locked'),
          ),
          const SizedBox(height: 12),
          for (final kind in _kinds)
            ListTile(
              title: Text(kind.label),
              leading: Icon(
                _selected == kind
                    ? Icons.radio_button_checked
                    : Icons.radio_button_unchecked,
              ),
              onTap: () => setState(() => _selected = kind),
            ),
          TextField(
            controller: _recordId,
            key: const ValueKey('rad7-print-record-id'),
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: 'Source record ID'),
          ),
          const SizedBox(height: 12),
          const FilledButton(
            onPressed: null,
            child: Text('Download / print (pending approval)'),
          ),
          const Text(
            'A PDF will only be available after server authorization, audit, and a separate device printing release. No automatic print or offline replay.',
          ),
          if (_notice != null) Text(_notice!),
        ],
      ),
    ),
  );
}
