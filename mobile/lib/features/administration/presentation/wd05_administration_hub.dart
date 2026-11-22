import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

/// A link is a suggestion only. It never grants backend access.
class Wd05AdministrationLink {
  const Wd05AdministrationLink({
    required this.id,
    required this.title,
    required this.path,
    required this.description,
    required this.icon,
  });

  final String id;
  final String title;
  final String path;
  final String description;
  final IconData icon;
}

/// Only existing authenticated Flutter routes are linked. Pending staff,
/// department, asset, accounting and export interfaces are NOT invented.
List<Wd05AdministrationLink> wd05AdministrationLinks({
  required String role,
  required int? clinicContextId,
}) {
  final normalized = role.trim().toLowerCase();
  // WD09A: super-admin system authority never requires a selected clinic.
  // Clinic-scoped links remain restricted to their actual backend allowlists.
  if (normalized == 'super_admin') {
    return const <Wd05AdministrationLink>[
      Wd05AdministrationLink(
        id: 'dashboard',
        title: 'System dashboard',
        path: '/home',
        description: 'Backend system overview',
        icon: Icons.dashboard_outlined,
      ),
      Wd05AdministrationLink(
        id: 'account',
        title: 'My account and clinic context',
        path: '/account',
        description: 'Authenticated profile and clinic selection',
        icon: Icons.manage_accounts_outlined,
      ),
      Wd05AdministrationLink(
        id: 'audit',
        title: 'System audit logs',
        path: '/administration/audit-logs',
        description: 'Flask-authorized system audit trail',
        icon: Icons.history_outlined,
      ),
      Wd05AdministrationLink(
        id: 'users',
        title: 'User access directory',
        path: '/administration/users',
        description: 'Read server-authorized users; write controls require separate verification',
        icon: Icons.admin_panel_settings_outlined,
      ),
    ];
  }
  if (clinicContextId == null || clinicContextId <= 0) {
    return const <Wd05AdministrationLink>[];
  }
  if (!(const <String>{
    'admin',
    'accountant',
    'pharmacist',
  }).contains(normalized)) {
    return const <Wd05AdministrationLink>[];
  }
  final links = <Wd05AdministrationLink>[
    const Wd05AdministrationLink(
      id: 'dashboard',
      title: 'My role dashboard',
      path: '/home',
      description: 'Existing Flask-backed dashboard. No new or estimated KPIs.',
      icon: Icons.dashboard_outlined,
    ),
    const Wd05AdministrationLink(
      id: 'account',
      title: 'My account and clinic context',
      path: '/account',
      description:
          'Existing account/clinic screen; not a clinic-management grant.',
      icon: Icons.manage_accounts_outlined,
    ),
  ];
  if (normalized == 'admin') {
    links.add(
      const Wd05AdministrationLink(
        id: 'billing',
        title: 'Billing and outstanding invoices',
        path: '/billing',
        description: 'Existing backend-authorized billing worklist and one-shot actions.',
        icon: Icons.receipt_long_outlined,
      ),
    );
  }
  if (normalized == 'admin' || normalized == 'pharmacist') {
    links.add(
      const Wd05AdministrationLink(
        id: 'pharmacy_stock',
        title: 'Pharmacy stock',
        path: '/pharmacy/stock',
        description: 'Read server-verified stock; no new inventory mutations.',
        icon: Icons.inventory_2_outlined,
      ),
    );
  }
  if (normalized == 'admin' || normalized == 'accountant') {
    links.add(
      const Wd05AdministrationLink(
        id: 'reports',
        title: 'Generated reports (read-only)',
        path: '/reports',
        description:
            'Clinic-scoped report metadata; exports and generation disabled.',
        icon: Icons.assessment_outlined,
      ),
    );
  }
  if (normalized == 'admin') {
    links.addAll(const <Wd05AdministrationLink>[
      Wd05AdministrationLink(
        id: 'staff',
        title: 'Staff directory',
        path: '/administration/staff',
        description: 'Read current-clinic staff only. Personnel writes are disabled here.',
        icon: Icons.people_outline,
      ),
      Wd05AdministrationLink(
        id: 'departments',
        title: 'Departments',
        path: '/administration/departments',
        description: 'Read current-clinic departments. No changes are sent.',
        icon: Icons.apartment_outlined,
      ),
      Wd05AdministrationLink(
        id: 'inventory',
        title: 'Hospital supplies inventory',
        path: '/administration/inventory',
        description: 'Server quantities only; no stock movements or changes.',
        icon: Icons.inventory_outlined,
      ),
    ]);
  } else if (normalized == 'pharmacist') {
    links.add(
      const Wd05AdministrationLink(
        id: 'inventory',
        title: 'Hospital supplies inventory',
        path: '/administration/inventory',
        description: 'Server quantities only; no stock mutations.',
        icon: Icons.inventory_outlined,
      ),
    );
  }
  if (normalized == 'admin' || normalized == 'accountant') {
    links.addAll(const <Wd05AdministrationLink>[
      Wd05AdministrationLink(
        id: 'assets',
        title: 'Asset register',
        path: '/administration/assets',
        description: 'Server asset records; assignment and disposal disabled.',
        icon: Icons.precision_manufacturing_outlined,
      ),
      Wd05AdministrationLink(
        id: 'settings',
        title: 'Clinic display settings',
        path: '/administration/settings',
        description:
            'Approved display fields only; credentials and writes hidden.',
        icon: Icons.settings_outlined,
      ),
    ]);
  }
  if (normalized == 'admin') {
    links.add(
      const Wd05AdministrationLink(
        id: 'audit',
        title: 'Clinic audit logs',
        path: '/administration/audit-logs',
        description: 'Read assigned-clinic audit events authorized by Flask',
        icon: Icons.history_outlined,
      ),
    );
  }
  return List<Wd05AdministrationLink>.unmodifiable(links);
}

const wd05PendingAdministrationCapabilities = <String>[
  'Staff/department creation, edits and status changes - not enabled',
  'Asset lifecycle, assignment and maintenance mutations - not enabled',
  'Clinic settings and integration credentials editing - not enabled',
  'Report generation and protected exports - separate approval pending',
  'Accounting summaries and payments by role - further verification pending',
];

/// Pure router entry which checks the real session before navigating. No
/// additional token store, backend, queue or privileged mode is introduced.
class Wd05AdministrationHub extends StatefulWidget {
  const Wd05AdministrationHub({
    super.key,
    required this.loadSession,
    required this.sessionNavigationGuard,
    required this.onOpen,
  });

  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  final ValueChanged<String> onOpen;

  @override
  State<Wd05AdministrationHub> createState() => _Wd05AdministrationHubState();
}

class _Wd05AdministrationHubState extends State<Wd05AdministrationHub> {
  AuthTokenResponse? _session;
  int? _verifiedRevision;
  bool _loading = true;
  bool _opening = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard.addListener(_onGuardChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant Wd05AdministrationHub oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
      oldWidget.sessionNavigationGuard,
      widget.sessionNavigationGuard,
    )) {
      oldWidget.sessionNavigationGuard.removeListener(_onGuardChanged);
      widget.sessionNavigationGuard.addListener(_onGuardChanged);
    }
    if (!identical(
          oldWidget.sessionNavigationGuard,
          widget.sessionNavigationGuard,
        ) ||
        !identical(oldWidget.loadSession, widget.loadSession)) {
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    widget.sessionNavigationGuard.removeListener(_onGuardChanged);
    _generation++;
    super.dispose();
  }

  void _onGuardChanged() {
    if (!mounted) return;
    if (!widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      _generation++;
      setState(() {
        _session = null;
        _verifiedRevision = null;
        _loading = false;
        _opening = false;
      });
    } else {
      unawaited(_load());
    }
  }

  static AuthTokenResponse? _valid(AuthSessionState state) {
    final session = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.userId <= 0 ||
        session.accessToken.isEmpty ||
        session.accessToken.trim() != session.accessToken ||
        wd05AdministrationLinks(
          role: session.role,
          clinicContextId: session.clinicContextId,
        ).isEmpty) {
      return null;
    }
    return session;
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.tenantId == b.tenantId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.toLowerCase() == b.role.toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<void> _load() async {
    final generation = ++_generation;
    final revision = widget.sessionNavigationGuard.revision;
    setState(() {
      _session = null;
      _verifiedRevision = null;
      _loading = true;
      _opening = false;
    });
    if (!widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
      return;
    }
    AuthTokenResponse? session;
    try {
      session = _valid(await widget.loadSession());
    } on Object {
      session = null;
    }
    if (!mounted ||
        generation != _generation ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes ||
        widget.sessionNavigationGuard.revision != revision) {
      return;
    }
    setState(() {
      _session = session;
      _verifiedRevision = session == null ? null : revision;
      _loading = false;
    });
  }

  Future<void> _open(Wd05AdministrationLink link) async {
    final current = _session;
    final revision = _verifiedRevision;
    if (_opening ||
        _loading ||
        current == null ||
        revision == null ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes ||
        widget.sessionNavigationGuard.revision != revision ||
        !wd05AdministrationLinks(
          role: current.role,
          clinicContextId: current.clinicContextId,
        ).any(
          (allowed) => allowed.id == link.id && allowed.path == link.path,
        )) {
      return;
    }
    final generation = _generation;
    setState(() => _opening = true);
    try {
      final verified = _valid(await widget.loadSession());
      if (verified == null ||
          !_same(current, verified) ||
          !mounted ||
          generation != _generation ||
          widget.sessionNavigationGuard.revision != revision ||
          !widget.sessionNavigationGuard.canAccessProtectedRoutes) {
        return;
      }
      widget.onOpen(link.path);
    } on Object {
      // A failed session restore is never permission to open an admin screen.
    } finally {
      if (mounted && generation == _generation) {
        setState(() => _opening = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = _session;
    if (_loading) {
      return const Center(
        child: CircularProgressIndicator(key: ValueKey('wd05-admin-loading')),
      );
    }
    if (session == null ||
        _verifiedRevision != widget.sessionNavigationGuard.revision ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      return const Center(
        child: Text(
          'Administration session unavailable. Sign in with an authorized clinic role.',
          key: ValueKey('wd05-admin-unavailable'),
        ),
      );
    }
    final links = wd05AdministrationLinks(
      role: session.role,
      clinicContextId: session.clinicContextId,
    );
    return ListView(
      key: const ValueKey('wd05-admin-workspace'),
      padding: const EdgeInsets.all(20),
      children: [
        Text(
          'Hospital administration',
          style: Theme.of(context).textTheme.headlineSmall,
        ),
        const SizedBox(height: 8),
        const Text(
          'Authenticated online navigation only. Each action and report is '
          'authorized again by Flask. No automatic export, payment, asset '
          'mutation or organization-wide grant.',
          key: ValueKey('wd05-admin-authority'),
        ),
        for (final link in links)
          Card(
            child: ListTile(
              key: ValueKey('wd05-admin-${link.id}'),
              leading: Icon(link.icon),
              title: Text(link.title),
              subtitle: Text(link.description),
              trailing: const Icon(Icons.chevron_right),
              onTap: _opening ? null : () => unawaited(_open(link)),
            ),
          ),
        const Divider(),
        Text(
          'Windows workflows pending verification',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        for (final item in wd05PendingAdministrationCapabilities)
          ListTile(
            leading: const Icon(Icons.lock_outline),
            title: Text(item),
            enabled: false,
          ),
      ],
    );
  }
}
