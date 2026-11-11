import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/dashboard/presentation/dashboard_overview_panel.dart';

/// Navigation suggestions, never authorization grants. The backend enforces
/// identity, clinic and role for every requested resource.
class HomeDestination {
  const HomeDestination(this.id, this.label, this.path, this.icon);

  final String id;
  final String label;
  final String path;
  final IconData icon;
}

const _clinicalRoles = <String>{
  'doctor',
  'nurse',
  'pharmacist',
  'lab_technician',
  'paramedic',
  'emt',
};

const _notificationRoles = <String>{
  'super_admin',
  'admin',
  'doctor',
  'nurse',
  'patient',
  'pharmacist',
  'lab_technician',
  'receptionist',
  'accountant',
  'paramedic',
  'emt',
  'driver',
  'ambulance_dispatcher',
  'ambulance_coordinator',
  'other',
};

const _patientDirectoryRoles = <String>{
  'admin',
  'doctor',
  'nurse',
  'receptionist',
  'pharmacist',
  'lab_technician',
  'paramedic',
  'emt',
};

/// Only routes already available in the Flutter app are advertised here.
/// A super admin must select a clinic before using clinic-scoped alerts.
List<HomeDestination> homeDestinationsFor({
  required String role,
  required int? clinicContextId,
  bool deviceRegistrationConfigured = false,
}) {
  final normalizedRole = role.trim().toLowerCase();
  final destinations = <HomeDestination>[
    const HomeDestination(
      'health',
      'Backend status',
      '/health',
      Icons.monitor_heart_outlined,
    ),
  ];

  // P24E: account-owned device management works without a push SDK or clinic.
  // This is navigation only; the authenticated backend remains authoritative.
  if (normalizedRole != 'super_admin' &&
      _notificationRoles.contains(normalizedRole)) {
    destinations.insert(
      0,
      const HomeDestination(
        'device_management',
        'My devices',
        '/devices',
        Icons.devices_outlined,
      ),
    );
  }
  // P24D: Android-only opt-in registration. Super admin is not authorized
  // by the existing backend device routes. This is navigation, not a grant.
  if (deviceRegistrationConfigured &&
      clinicContextId != null &&
      clinicContextId > 0 &&
      normalizedRole != 'super_admin' &&
      _notificationRoles.contains(normalizedRole)) {
    destinations.insert(
      0,
      const HomeDestination(
        'device_registration',
        'Register push notifications',
        '/devices/register',
        Icons.phonelink_setup_outlined,
      ),
    );
  }
  // P24A: a selected clinic is mandatory, including for super admin.
  // Home navigation is not a backend authorization grant.
  if (clinicContextId != null &&
      clinicContextId > 0 &&
      _notificationRoles.contains(normalizedRole)) {
    destinations.insert(
      0,
      const HomeDestination(
        'notifications',
        'Notifications',
        '/notifications',
        Icons.notifications_outlined,
      ),
    );
  }
  // P23I.S09 exposes navigation only; all billing/transport authority
  // is rechecked by the backend and source-bound session guards.
  if (clinicContextId != null &&
      clinicContextId > 0 &&
      normalizedRole == 'admin') {
    destinations.insert(
      0,
      const HomeDestination(
        'billing',
        'Billing & invoices',
        '/billing',
        Icons.receipt_long_outlined,
      ),
    );
  }
  if (clinicContextId != null &&
      clinicContextId > 0 &&
      const <String>{
        'admin',
        'ambulance_coordinator',
        'ambulance_dispatcher',
        'driver',
        'paramedic',
        'emt',
      }.contains(normalizedRole)) {
    destinations.insert(
      0,
      const HomeDestination(
        'ambulance',
        'Ambulance & trips',
        '/ambulance',
        Icons.emergency_outlined,
      ),
    );
  }
  // P23H.S06A inventory entry is visible only to authorized pharmacy roles
  // with a selected clinic. Backend RBAC and session guards remain binding.
  if (clinicContextId != null &&
      clinicContextId > 0 &&
      const <String>{'admin', 'pharmacist'}.contains(normalizedRole)) {
    destinations.insert(
      0,
      const HomeDestination(
        'pharmacy_inventory',
        'Pharmacy stock',
        '/pharmacy/stock',
        Icons.inventory_2_outlined,
      ),
    );
  }
  // The backend exposes patient/staff-scoped appointments, not a global list.
  // Only the four appointment roles with an active clinic see this entry.
  if (clinicContextId != null &&
      clinicContextId > 0 &&
      const <String>{
        'admin',
        'doctor',
        'nurse',
        'receptionist',
      }.contains(normalizedRole)) {
    destinations.insert(
      0,
      const HomeDestination(
        'appointments',
        'Appointments',
        '/appointments',
        Icons.event_note_outlined,
      ),
    );
  }
  if (clinicContextId != null &&
      clinicContextId > 0 &&
      const <String>{'admin', 'doctor', 'nurse'}.contains(normalizedRole)) {
    destinations.insert(
      0,
      const HomeDestination(
        'consultations',
        'Consultations',
        '/consultations',
        Icons.medical_information_outlined,
      ),
    );
  }
  if (clinicContextId != null &&
      clinicContextId > 0 &&
      const <String>{'admin', 'doctor', 'nurse'}.contains(normalizedRole)) {
    destinations.insert(
      0,
      const HomeDestination(
        'clinical_orders',
        'Clinical orders',
        '/clinical-orders',
        Icons.medical_services_outlined,
      ),
    );
  }
  // Server-authorized ward status is read-only in P23E.B.
  if (clinicContextId != null &&
      clinicContextId > 0 &&
      const <String>{
        'admin',
        'doctor',
        'nurse',
        'receptionist',
      }.contains(normalizedRole)) {
    destinations.insert(
      0,
      const HomeDestination(
        'wards',
        'Wards & beds',
        '/wards',
        Icons.bed_outlined,
      ),
    );
  }
  // P25: only appointment-create staff roles have a verified durable queue.
  // This navigation is not backend authorization.
  if (clinicContextId != null &&
      clinicContextId > 0 &&
      const <String>{
        'admin',
        'doctor',
        'nurse',
        'receptionist',
      }.contains(normalizedRole)) {
    destinations.insert(
      0,
      const HomeDestination(
        'pending_work',
        'Pending Work',
        '/pending-work',
        Icons.pending_actions_outlined,
      ),
    );
  }
  // P24H: a selected clinic is required to display feedback navigation.
  // This link never grants server feedback visibility or management roles.
  if (clinicContextId != null && clinicContextId > 0) {
    destinations.insert(
      0,
      const HomeDestination(
        'feedback',
        'Feedback',
        '/feedback',
        Icons.rate_review_outlined,
      ),
    );
  }
  // CHAT-F: navigation suggestion only; the backend authorizes every read.
  if (clinicContextId != null &&
      clinicContextId > 0 &&
      const <String>{'admin', 'doctor', 'nurse'}.contains(normalizedRole)) {
    destinations.insert(
      0,
      const HomeDestination(
        'chat',
        'Clinic messages',
        '/chat',
        Icons.forum_outlined,
      ),
    );
  }
  // Lab technicians cannot access the unified CPOE endpoint. Give them the
  // dedicated backend-authorized blood-product order-ID lookup instead.
  if (clinicContextId != null &&
      clinicContextId > 0 &&
      normalizedRole == 'lab_technician') {
    destinations.insert(
      0,
      const HomeDestination(
        'blood_products',
        'Blood-product verification',
        '/clinical-orders/blood-products',
        Icons.bloodtype_outlined,
      ),
    );
  }
  if (_patientDirectoryRoles.contains(normalizedRole)) {
    destinations.insert(
      0,
      const HomeDestination(
        'patients',
        'Patient directory',
        '/patients',
        Icons.people_outline,
      ),
    );
  }

  if (normalizedRole == 'admin' ||
      _clinicalRoles.contains(normalizedRole) ||
      (normalizedRole == 'super_admin' &&
          clinicContextId != null &&
          clinicContextId > 0)) {
    destinations.insert(
      0,
      const HomeDestination(
        'clinical_safety',
        'Clinical alerts',
        '/clinical-safety',
        Icons.notification_important_outlined,
      ),
    );
  }

  if (_clinicalRoles.contains(normalizedRole)) {
    destinations.add(
      const HomeDestination(
        'emergency_request',
        'Request emergency access',
        '/emergency-access/request',
        Icons.emergency_outlined,
      ),
    );
  }

  return List<HomeDestination>.unmodifiable(destinations);
}

String _dashboardFamily(String role) => switch (role.trim().toLowerCase()) {
  'super_admin' => 'System administration',
  'admin' => 'Clinic management',
  'doctor' ||
  'nurse' ||
  'pharmacist' ||
  'lab_technician' ||
  'paramedic' ||
  'emt' => 'Clinical workspace',
  'receptionist' ||
  'driver' ||
  'ambulance_dispatcher' ||
  'ambulance_coordinator' ||
  'other' => 'Operations workspace',
  'accountant' => 'Finance workspace',
  'patient' => 'Patient workspace',
  _ => 'Unrecognized role',
};

/// An authenticated landing screen. It intentionally holds no bearer token or
/// long-lived patient data. It re-reads the saved session on navigation changes.
class AuthenticatedHomeScreen extends StatefulWidget {
  const AuthenticatedHomeScreen({
    super.key,
    required this.loadSession,
    this.dashboardDataSource,
    this.statusStripBuilder,
    this.sessionNavigationGuard,
    this.deviceRegistrationConfigured = false,
  });

  final Future<AuthSessionState> Function() loadSession;
  final DashboardRemoteDataSource? dashboardDataSource;
  final Widget Function()? statusStripBuilder;
  final SessionNavigationGuard? sessionNavigationGuard;
  final bool deviceRegistrationConfigured;

  @override
  State<AuthenticatedHomeScreen> createState() =>
      _AuthenticatedHomeScreenState();
}

class _AuthenticatedHomeScreenState extends State<AuthenticatedHomeScreen> {
  String? _role;
  int? _clinicId;
  bool _loading = true;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard?.addListener(_onGuardChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant AuthenticatedHomeScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
      oldWidget.sessionNavigationGuard,
      widget.sessionNavigationGuard,
    )) {
      oldWidget.sessionNavigationGuard?.removeListener(_onGuardChanged);
      widget.sessionNavigationGuard?.addListener(_onGuardChanged);
    }
    if (!identical(oldWidget.loadSession, widget.loadSession) ||
        !identical(
          oldWidget.sessionNavigationGuard,
          widget.sessionNavigationGuard,
        )) {
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    widget.sessionNavigationGuard?.removeListener(_onGuardChanged);
    _generation++;
    _role = null;
    _clinicId = null;
    super.dispose();
  }

  void _onGuardChanged() {
    _generation++;
    final guard = widget.sessionNavigationGuard;
    if (!mounted) return;
    if (guard?.canAccessProtectedRoutes == true) {
      unawaited(_load());
      return;
    }
    setState(() {
      _role = null;
      _clinicId = null;
      _loading = false;
    });
  }

  Future<void> _load() async {
    final generation = ++_generation;
    final guard = widget.sessionNavigationGuard;
    final revision = guard?.revision;
    if (!mounted) return;
    setState(() {
      _role = null;
      _clinicId = null;
      _loading = true;
    });

    if (guard != null && !guard.canAccessProtectedRoutes) {
      if (!mounted || generation != _generation) return;
      setState(() => _loading = false);
      return;
    }

    try {
      final state = await widget.loadSession();
      if (!mounted ||
          generation != _generation ||
          guard?.revision != revision ||
          (guard != null && !guard.canAccessProtectedRoutes)) {
        return;
      }
      if (state.disposition != AuthSessionDisposition.ready ||
          state.session == null ||
          state.session!.userId <= 0) {
        setState(() => _loading = false);
        return;
      }
      setState(() {
        _role = state.session!.role;
        _clinicId = state.session!.clinicContextId;
        _loading = false;
      });
    } on Object {
      if (!mounted ||
          generation != _generation ||
          guard?.revision != revision) {
        return;
      }
      setState(() => _loading = false);
    }
  }

  void _openDestination(HomeDestination destination) {
    if (_loading ||
        _role == null ||
        widget.sessionNavigationGuard?.canAccessProtectedRoutes == false) {
      return;
    }
    context.push<void>(destination.path);
  }

  @override
  Widget build(BuildContext context) {
    final role = _role;
    final destinations = role == null
        ? const <HomeDestination>[]
        : homeDestinationsFor(
            role: role,
            clinicContextId: _clinicId,
            deviceRegistrationConfigured: widget.deviceRegistrationConfigured,
          );
    final wideScreen = MediaQuery.sizeOf(context).width >= 850;
    return Scaffold(
      key: const ValueKey('p22-home-shell'),
      appBar: AppBar(
        title: const Text('Clinic System Pro'),
        actions: [
          IconButton(
            key: const ValueKey('p22-s05-open-account-home'),
            tooltip: 'Profile and clinic',
            onPressed:
                role == null ||
                    widget.sessionNavigationGuard?.canAccessProtectedRoutes ==
                        false
                ? null
                : () => context.push<void>('/account'),
            icon: const Icon(Icons.person_outline),
          ),
          IconButton(
            key: const ValueKey('p22-home-session-controls'),
            tooltip: 'Session and sign out',
            onPressed: () => context.go('/login?session=1'),
            icon: const Icon(Icons.account_circle_outlined),
          ),
        ],
      ),
      drawer: wideScreen || destinations.isEmpty
          ? null
          : Drawer(
              child: SafeArea(
                child: ListView(
                  children: [
                    const ListTile(title: Text('Navigate')),
                    for (final destination in destinations)
                      ListTile(
                        key: ValueKey('p22-drawer-${destination.id}'),
                        leading: Icon(destination.icon),
                        title: Text(destination.label),
                        onTap: () {
                          Navigator.of(context).pop();
                          _openDestination(destination);
                        },
                      ),
                  ],
                ),
              ),
            ),
      body: Column(
        children: [
          if (widget.statusStripBuilder != null) widget.statusStripBuilder!(),
          Expanded(
            child: LayoutBuilder(
              builder: (context, constraints) {
                final wide =
                    constraints.maxWidth >= 850 && destinations.isNotEmpty;
                return Row(
                  children: [
                    if (wide) ...[
                      NavigationRail(
                        scrollable: true,
                        selectedIndex: 0,
                        labelType: NavigationRailLabelType.all,
                        destinations: [
                          const NavigationRailDestination(
                            icon: Icon(Icons.home_outlined),
                            label: Text('Home'),
                          ),
                          for (final destination in destinations)
                            NavigationRailDestination(
                              icon: Icon(destination.icon),
                              label: Text(destination.label),
                            ),
                        ],
                        onDestinationSelected: (index) {
                          if (index > 0) {
                            _openDestination(destinations[index - 1]);
                          }
                        },
                      ),
                      const VerticalDivider(width: 1),
                    ],
                    Expanded(
                      child: _loading
                          ? const Center(
                              child: CircularProgressIndicator(
                                key: ValueKey('p22-home-loading'),
                              ),
                            )
                          : role == null
                          ? const Center(
                              child: Padding(
                                padding: EdgeInsets.all(24),
                                child: Text(
                                  'Authenticated session unavailable. Return to sign in.',
                                  key: ValueKey('p22-home-session-unavailable'),
                                ),
                              ),
                            )
                          : _overview(role, destinations),
                    ),
                  ],
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _overview(String role, List<HomeDestination> destinations) {
    final dashboard = widget.dashboardDataSource;
    return ListView(
      key: const ValueKey('p22-home-overview'),
      padding: const EdgeInsets.all(20),
      children: [
        Text(
          _dashboardFamily(role),
          key: const ValueKey('p22-home-role-family'),
          style: Theme.of(context).textTheme.headlineSmall,
        ),
        const SizedBox(height: 10),
        if (dashboard == null)
          const Text(
            'Dashboard metrics will be loaded from the backend in P22.S03. '
            'This home screen does not invent counts or clinical permissions.',
          )
        else
          DashboardOverviewPanel(
            key: ValueKey(
              'p22-dashboard-${role.toLowerCase()}-${_clinicId ?? 'none'}',
            ),
            dataSource: dashboard,
            expectedRole: role,
            expectedClinicId: _clinicId,
            sessionNavigationGuard: widget.sessionNavigationGuard,
          ),
        if (role.trim().toLowerCase() == 'super_admin' &&
            (_clinicId == null || _clinicId! <= 0)) ...[
          const SizedBox(height: 12),
          const Text(
            'Select a clinic context before accessing clinic-scoped alerts.',
            key: ValueKey('p22-home-clinic-required'),
          ),
        ],
        const SizedBox(height: 24),
        Text(
          'Available navigation',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 8),
        for (final destination in destinations) ...[
          Card(
            child: ListTile(
              key: ValueKey('p22-home-${destination.id}'),
              leading: Icon(destination.icon),
              title: Text(destination.label),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _openDestination(destination),
            ),
          ),
        ],
        const SizedBox(height: 12),
        const Text(
          'Navigation suggestions are not access grants. The backend '
          'verifies every request and can deny it.',
          key: ValueKey('p22-home-server-authority'),
        ),
      ],
    );
  }
}
