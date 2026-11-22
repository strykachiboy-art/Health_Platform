import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/administration/data/wd09b_access_control_mutation_remote_data_source.dart';

/// Deliberate, one-shot Super Admin access-control changes.
/// Never auto-retries after an ambiguous or failed PATCH.
class Wd09bAccessControlMutationScreen extends StatefulWidget {
  const Wd09bAccessControlMutationScreen({
    super.key,
    required this.userId,
    required this.source,
  });

  final int userId;
  final Wd09bAccessControlMutationRemoteDataSource source;

  @override
  State<Wd09bAccessControlMutationScreen> createState() =>
      _Wd09bAccessControlMutationScreenState();
}

class _Wd09bAccessControlMutationScreenState
    extends State<Wd09bAccessControlMutationScreen> {
  final _reason = TextEditingController();
  final _clinic = TextEditingController();
  Wd09bUser? _user;
  String? _role;
  String? _message;
  bool _loading = false;
  bool _attempted = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant Wd09bAccessControlMutationScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.source == widget.source &&
        oldWidget.userId == widget.userId) {
      return;
    }
    oldWidget.source.sessionNavigationGuard.removeListener(_sessionChanged);
    widget.source.sessionNavigationGuard.addListener(_sessionChanged);
    _generation++;
    _attempted = false;
    _user = null;
    _role = null;
    _message = null;
    _loading = false;
    unawaited(_load());
  }

  @override
  void dispose() {
    _generation++;
    widget.source.sessionNavigationGuard.removeListener(_sessionChanged);
    _reason.dispose();
    _clinic.dispose();
    super.dispose();
  }

  void _sessionChanged() {
    if (!mounted) {
      return;
    }
    _generation++;
    setState(() {
      _user = null;
      _role = null;
      _loading = false;
      _attempted = true;
      _message = 'Session changed. Close this screen and sign in again.';
    });
  }

  bool _current(
    int generation,
    int revision,
    Wd09bAccessControlMutationRemoteDataSource source,
  ) =>
      mounted &&
      identical(widget.source, source) &&
      generation == _generation &&
      source.sessionNavigationGuard.canAccessProtectedRoutes &&
      source.sessionNavigationGuard.revision == revision;

  Future<void> _load() async {
    if (_loading || _attempted) {
      return;
    }
    final source = widget.source;
    final generation = ++_generation;
    final revision = source.sessionNavigationGuard.revision;
    setState(() {
      _user = null;
      _loading = true;
      _message = null;
    });
    try {
      final result = await source.readUser(widget.userId);
      if (!_current(generation, revision, source)) {
        return;
      }
      setState(() {
        _user = result;
        _loading = false;
      });
    } on Object {
      if (!_current(generation, revision, source)) {
        return;
      }
      setState(() {
        _loading = false;
        _message = 'User details unavailable or access denied by the server.';
      });
    }
  }

  Future<void> _submit(Wd09bChangeKind kind) async {
    final user = _user;
    if (user == null || _loading || _attempted || user.role == 'super_admin') {
      return;
    }
    final source = widget.source;
    final revision = source.sessionNavigationGuard.revision;
    if (!source.sessionNavigationGuard.canAccessProtectedRoutes) {
      return;
    }

    String? targetRole;
    bool? targetStatus;
    int? targetClinic;
    String plan;
    if (kind == Wd09bChangeKind.role) {
      targetRole = _role;
      if (targetRole == null || targetRole == user.role) {
        return;
      }
      plan = 'Change role from ${user.role} to $targetRole';
    } else if (kind == Wd09bChangeKind.status) {
      targetStatus = !user.isActive;
      plan = targetStatus ? 'Activate account' : 'Deactivate account';
    } else {
      if (user.role == 'patient') {
        return;
      }
      targetClinic = int.tryParse(_clinic.text.trim());
      if (targetClinic == null ||
          targetClinic <= 0 ||
          targetClinic > 2147483647 ||
          targetClinic == user.clinicId) {
        setState(
          () => _message = 'Enter a different, positive destination clinic ID.',
        );
        return;
      }
      plan = 'Transfer from clinic ${user.clinicId ?? 'none'} to $targetClinic';
    }
    final reason = _reason.text.trim();
    if (reason.length > 500) {
      setState(() => _message = 'Reason must be at most 500 characters.');
      return;
    }
    final confirm = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Confirm access-control change'),
        content: Text(
          'User #${user.id} (${user.email})\n$plan\n'
          '${reason.isEmpty ? 'No reason supplied.' : 'Reason: $reason'}\n\n'
          'This updates the server immediately, may revoke user sessions, '
          'and will not be automatically retried.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('wd09b-confirm-change'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Confirm server change'),
          ),
        ],
      ),
    );
    if (confirm != true ||
        !mounted ||
        source.sessionNavigationGuard.revision != revision ||
        !source.sessionNavigationGuard.canAccessProtectedRoutes ||
        _attempted) {
      return;
    }
    final generation = ++_generation;
    setState(() {
      _loading = true;
      _attempted = true; // Do not retry in this route after any attempt.
      _message = null;
    });
    try {
      final result = await source.change(
        expected: user,
        kind: kind,
        newRole: targetRole,
        newStatus: targetStatus,
        destinationClinicId: targetClinic,
        reason: reason.isEmpty ? null : reason,
      );
      if (!_current(generation, revision, source)) {
        return;
      }
      setState(() {
        _user = result;
        _loading = false;
        _message = 'Server confirmed the change. Close and reopen to review further actions.';
      });
    } on Object {
      if (!_current(generation, revision, source)) {
        return;
      }
      setState(() {
        _loading = false;
        _message =
            'Change not confirmed. Do not repeat automatically. '
            'Refresh the user record and audit logs before any further action.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final user = _user;
    final writable =
        user != null && !_loading && !_attempted && user.role != 'super_admin';
    return ListView(
      key: const ValueKey('wd09b-management-workspace'),
      padding: const EdgeInsets.all(20),
      children: [
        Text(
          'User access management',
          style: Theme.of(context).textTheme.headlineSmall,
        ),
        const SizedBox(height: 8),
        const Text(
          'Super Admin only. All mutations are sent to existing Flask '
          'access-control endpoints, with explicit confirmation and no offline replay.',
        ),
        const SizedBox(height: 12),
        if (_loading)
          const LinearProgressIndicator(key: ValueKey('wd09b-loading')),
        if (_message != null)
          Text(_message!, key: const ValueKey('wd09b-message')),
        if (!_loading && user == null && !_attempted)
          TextButton(
            onPressed: () => unawaited(_load()),
            child: const Text('Retry read'),
          ),
        if (user != null) ...[
          Text(
            'User #${user.id}: ${user.email}',
            key: const ValueKey('wd09b-user'),
          ),
          Text('Current role: ${user.role}'),
          Text('Status: ${user.isActive ? 'Active' : 'Inactive'}'),
          Text('Clinic: ${user.clinicId ?? 'none'}'),
          const SizedBox(height: 12),
          if (user.role == 'super_admin')
            const Text('Super Administrator accounts cannot be modified here.'),
          if (user.role != 'super_admin') ...[
            TextField(
              key: const ValueKey('wd09b-reason'),
              controller: _reason,
              enabled: writable,
              maxLength: 500,
              decoration: const InputDecoration(
                labelText: 'Audit reason (optional)',
              ),
            ),
            DropdownButton<String>(
              key: const ValueKey('wd09b-role-selector'),
              isExpanded: true,
              value: _role,
              hint: const Text('Choose a new role'),
              items: [
                for (final role in wd09bAssignableRoles)
                  DropdownMenuItem<String>(value: role, child: Text(role)),
              ],
              onChanged: writable
                  ? (value) => setState(() => _role = value)
                  : null,
            ),
            FilledButton(
              key: const ValueKey('wd09b-change-role'),
              onPressed: writable && _role != null && _role != user.role
                  ? () => unawaited(_submit(Wd09bChangeKind.role))
                  : null,
              child: const Text('Change role'),
            ),
            FilledButton(
              key: const ValueKey('wd09b-change-status'),
              onPressed: writable
                  ? () => unawaited(_submit(Wd09bChangeKind.status))
                  : null,
              child: Text(
                user.isActive ? 'Deactivate account' : 'Activate account',
              ),
            ),
            if (user.role != 'patient') ...[
              TextField(
                key: const ValueKey('wd09b-clinic-id'),
                controller: _clinic,
                enabled: writable,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'Destination clinic ID',
                ),
              ),
              FilledButton(
                key: const ValueKey('wd09b-change-clinic'),
                onPressed: writable
                    ? () => unawaited(_submit(Wd09bChangeKind.clinic))
                    : null,
                child: const Text('Transfer clinic'),
              ),
            ],
          ],
        ],
      ],
    );
  }
}
