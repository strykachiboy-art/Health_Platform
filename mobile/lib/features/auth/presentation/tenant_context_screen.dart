import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/clinic_context_switch_coordinator.dart';

/// GAP-B member-selected hospital/clinic context. No local grants are created.
/// The backend verifies active organization and clinic memberships on every use.
class TenantContextScreen extends StatefulWidget {
  const TenantContextScreen({
    super.key,
    required this.loadSession,
    required this.remote,
    required this.switcher,
    required this.guard,
  });

  final Future<AuthSessionState> Function() loadSession;
  final AuthRemoteDataSource remote;
  final ClinicContextSwitchCoordinator switcher;
  final SessionNavigationGuard guard;

  @override
  State<TenantContextScreen> createState() => _TenantContextScreenState();
}

class _TenantContextScreenState extends State<TenantContextScreen> {
  final _organization = TextEditingController();
  final _clinic = TextEditingController();
  TenantContextDetails? _context;
  bool _loading = true;
  bool _changing = false;
  String? _error;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.guard.addListener(_guardChanged);
    unawaited(_load());
  }

  @override
  void dispose() {
    widget.guard.removeListener(_guardChanged);
    _generation++;
    _organization.dispose();
    _clinic.dispose();
    super.dispose();
  }

  void _guardChanged() {
    _generation++;
    if (!mounted) return;
    if (widget.guard.canAccessProtectedRoutes) {
      unawaited(_load());
    } else {
      setState(() {
        _context = null;
        _loading = false;
        _error = 'The previous workspace is no longer available.';
      });
    }
  }

  bool _current(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.guard.canAccessProtectedRoutes &&
      widget.guard.revision == revision;

  Future<void> _load() async {
    final generation = ++_generation;
    final revision = widget.guard.revision;
    setState(() {
      _loading = true;
      _context = null;
      _error = null;
    });
    try {
      if (!widget.guard.canAccessProtectedRoutes) {
        throw StateError('Session unavailable');
      }
      final before = await widget.loadSession();
      final actor = before.session;
      if (!_current(generation, revision) ||
          before.disposition != AuthSessionDisposition.ready ||
          actor == null) {
        throw StateError('Session unavailable');
      }
      final context = await widget.remote.getTenantContext(
        accessToken: actor.accessToken,
      );
      final after = await widget.loadSession();
      if (!_current(generation, revision) ||
          after.disposition != AuthSessionDisposition.ready ||
          after.session?.accessToken != actor.accessToken ||
          after.session?.refreshToken != actor.refreshToken ||
          after.session?.tenantId != actor.tenantId ||
          after.session?.clinicContextId != actor.clinicContextId ||
          context.tenantId != actor.tenantId ||
          context.clinicId != actor.clinicContextId) {
        throw StateError('Tenant context changed during verification');
      }
      setState(() {
        _context = context;
        _loading = false;
      });
    } on Object {
      if (!_current(generation, revision)) return;
      setState(() {
        _loading = false;
        _context = null;
        _error = 'Tenant context could not be verified. Retry online.';
      });
    }
  }

  Future<void> _change({
    String? tenantId,
    int? clinicId,
    bool clear = false,
  }) async {
    if (_changing ||
        _loading ||
        _context == null ||
        !widget.guard.canAccessProtectedRoutes) {
      return;
    }
    setState(() {
      _changing = true;
      _context = null;
      _error = null;
    });
    try {
      final result = clear
          ? await widget.switcher.clearTenant()
          : tenantId != null
          ? await widget.switcher.selectTenant(tenantId)
          : await widget.switcher.selectMemberClinic(clinicId!);
      if (!mounted) return;
      if (result.disposition != AuthSessionDisposition.ready) {
        setState(
          () => _error = 'Context change is unconfirmed. Recover the secure session; do not retry automatically.',
        );
      }
    } on Object {
      if (!mounted) return;
      setState(
        () => _error =
            'Context change is unconfirmed. Recover the secure session.',
      );
    } finally {
      if (mounted) setState(() => _changing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final verified = _context;
    return Scaffold(
      appBar: AppBar(title: const Text('Hospital and clinic access')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          const Text(
            'Your hospital and clinic access is granted and checked by the server. '
            'Switching closes the current clinical workspace first.',
          ),
          if (_loading) const LinearProgressIndicator(),
          if (verified != null) ...[
            const SizedBox(height: 16),
            Text(
              'Selected hospital: ${verified.tenantId ?? 'None'}',
              key: const ValueKey('gapb-tenant-current'),
            ),
            Text(
              'Selected clinic: ${verified.clinicId?.toString() ?? 'None'}',
              key: const ValueKey('gapb-clinic-current'),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _organization,
              key: const ValueKey('gapb-enter-tenant'),
              decoration: const InputDecoration(
                labelText: 'Authorized hospital organization UUID',
              ),
            ),
            FilledButton(
              key: const ValueKey('gapb-select-tenant'),
              onPressed: _changing
                  ? null
                  : () =>
                        unawaited(_change(tenantId: _organization.text.trim())),
              child: const Text('Select hospital'),
            ),
            if (verified.tenantId != null) ...[
              TextField(
                controller: _clinic,
                key: const ValueKey('gapb-enter-clinic'),
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'Authorized clinic ID',
                ),
              ),
              FilledButton(
                key: const ValueKey('gapb-select-clinic'),
                onPressed: _changing
                    ? null
                    : () {
                        final id = int.tryParse(_clinic.text.trim());
                        if (id == null || id <= 0) {
                          setState(
                            () => _error = 'Enter a valid positive clinic ID.',
                          );
                          return;
                        }
                        unawaited(_change(clinicId: id));
                      },
                child: const Text('Select clinic'),
              ),
              OutlinedButton(
                key: const ValueKey('gapb-clear-tenant'),
                onPressed: _changing
                    ? null
                    : () => unawaited(_change(clear: true)),
                child: const Text('Clear hospital selection'),
              ),
            ],
          ],
          if (_error != null)
            Text(_error!, key: const ValueKey('gapb-tenant-error')),
          if (!_loading && verified == null)
            TextButton(
              onPressed: _changing ? null : () => unawaited(_load()),
              child: const Text('Retry verification'),
            ),
        ],
      ),
    );
  }
}
