import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';

/// WD04 only advertises existing authenticated routes. This catalog is never
/// an access grant: the server must authorize every role/clinic/patient read
/// and every clinical mutation. No scanner, print, replay or PHI cache here.
class Wd04ClinicalWorkflowLink {
  const Wd04ClinicalWorkflowLink({
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

/// Conservative client-side suggestions matching existing top-level routes.
/// No patient identifiers are synthesized: patient-scoped workflows must begin
/// from a backend-verified patient directory/record.
List<Wd04ClinicalWorkflowLink> wd04ClinicalWorkflowLinks({
  required String role,
  required int? clinicContextId,
}) {
  if (clinicContextId == null || clinicContextId <= 0) {
    return const <Wd04ClinicalWorkflowLink>[];
  }

  final normalizedRole = role.trim().toLowerCase();
  // Super-admin surgery access already has its own dedicated entry; WD04 is
  // a clinical staff launchpad, not an organization-wide access grant.
  if (normalizedRole == 'super_admin') {
    return const <Wd04ClinicalWorkflowLink>[];
  }

  final links = <Wd04ClinicalWorkflowLink>[];

  if (const <String>{
    'admin',
    'doctor',
    'nurse',
    'receptionist',
  }.contains(normalizedRole)) {
    links.add(
      const Wd04ClinicalWorkflowLink(
        id: 'wards',
        title: 'Wards, admissions and nursing handoff',
        path: '/wards',
        description:
            'Existing ward worklist; the server checks each nursing action.',
        icon: Icons.bed_outlined,
      ),
    );
  }
  if (const <String>{'doctor', 'nurse'}.contains(normalizedRole)) {
    links.add(
      const Wd04ClinicalWorkflowLink(
        id: 'clinical_orders',
        title: 'Clinical orders',
        path: '/clinical-orders',
        description:
            'Open the server-scoped order worklist; no direct patient ID.',
        icon: Icons.medical_services_outlined,
      ),
    );
    links.add(
      const Wd04ClinicalWorkflowLink(
        id: 'emar',
        title: 'eMAR and barcode verification',
        path: '/patients',
        description: 'Select a verified patient chart first. No unattended scanner or dose action.',
        icon: Icons.medication_outlined,
      ),
    );
    links.add(
      const Wd04ClinicalWorkflowLink(
        id: 'lab',
        title: 'Patient-scoped laboratory workflows',
        path: '/patients',
        description: 'Open a verified patient chart to inspect available laboratory actions.',
        icon: Icons.science_outlined,
      ),
    );
  }
  if (normalizedRole == 'lab_technician') {
    links.add(
      const Wd04ClinicalWorkflowLink(
        id: 'lab',
        title: 'Blood-product order verification',
        path: '/clinical-orders/blood-products',
        description: 'Existing order-ID verification route; not the unified CPOE worklist.',
        icon: Icons.bloodtype_outlined,
      ),
    );
  }
  if (const <String>{'admin', 'pharmacist'}.contains(normalizedRole)) {
    links.add(
      const Wd04ClinicalWorkflowLink(
        id: 'pharmacy',
        title: 'Pharmacy inventory',
        path: '/pharmacy/stock',
        description:
            'The backend determines stock access and dispense authority.',
        icon: Icons.inventory_2_outlined,
      ),
    );
  }
  if (normalizedRole == 'pharmacist') {
    links.add(
      const Wd04ClinicalWorkflowLink(
        id: 'dispensing',
        title: 'Patient prescription and dispense review',
        path: '/patients',
        description:
            'Choose a server-confirmed patient before any dispense workflow.',
        icon: Icons.medication_outlined,
      ),
    );
  }
  if (normalizedRole == 'doctor') {
    links.add(
      const Wd04ClinicalWorkflowLink(
        id: 'consultation_templates',
        title: 'Consultation templates',
        path: '/consultations/templates',
        description: 'List and create reusable documentation templates through the backend.',
        icon: Icons.description_outlined,
      ),
    );
  }
  if (normalizedRole == 'doctor') {
    links.add(
      const Wd04ClinicalWorkflowLink(
        id: 'radiology',
        title: 'Radiology RIS planning',
        path: '/radiology',
        description: 'Existing role-scoped RIS; external DICOM/OHIF readiness is separate.',
        icon: Icons.image_search_outlined,
      ),
    );
  }
  if (const <String>{'admin', 'doctor', 'nurse'}.contains(normalizedRole)) {
    links.add(
      const Wd04ClinicalWorkflowLink(
        id: 'surgery',
        title: 'Surgery worklist',
        path: '/surgery',
        description: 'The server checks case assignment before exposing a case or action.',
        icon: Icons.healing_outlined,
      ),
    );
  }
  if (const <String>{
    'admin',
    'ambulance_coordinator',
    'ambulance_dispatcher',
    'driver',
    'paramedic',
    'emt',
  }.contains(normalizedRole)) {
    links.add(
      const Wd04ClinicalWorkflowLink(
        id: 'ambulance',
        title: 'Ambulance and emergency transport',
        path: '/ambulance',
        description:
            'Dispatch and trip changes remain server-authorized and one-shot.',
        icon: Icons.emergency_outlined,
      ),
    );
  }
  if (const <String>{
    'doctor',
    'nurse',
    'pharmacist',
    'lab_technician',
    'paramedic',
    'emt',
  }.contains(normalizedRole)) {
    links.add(
      const Wd04ClinicalWorkflowLink(
        id: 'emergency',
        title: 'Emergency access request',
        path: '/emergency-access/request',
        description: 'Request audited emergency access; this link does not grant access.',
        icon: Icons.health_and_safety_outlined,
      ),
    );
  }

  return List<Wd04ClinicalWorkflowLink>.unmodifiable(links);
}

/// This screen has no independent auth store, backend, replay system or
/// permission cache. A guard revision fences asynchronous session restoration.
class Wd04ClinicalWorkflowHub extends StatefulWidget {
  const Wd04ClinicalWorkflowHub({
    super.key,
    required this.loadSession,
    required this.sessionNavigationGuard,
    required this.onOpen,
  });

  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  final ValueChanged<String> onOpen;

  @override
  State<Wd04ClinicalWorkflowHub> createState() =>
      _Wd04ClinicalWorkflowHubState();
}

class _Wd04ClinicalWorkflowHubState extends State<Wd04ClinicalWorkflowHub> {
  String? _role;
  int? _clinicId;
  bool _loading = true;
  int _generation = 0;
  int? _verifiedGuardRevision;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard.addListener(_onSessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant Wd04ClinicalWorkflowHub oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
      oldWidget.sessionNavigationGuard,
      widget.sessionNavigationGuard,
    )) {
      oldWidget.sessionNavigationGuard.removeListener(_onSessionChanged);
      widget.sessionNavigationGuard.addListener(_onSessionChanged);
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
    widget.sessionNavigationGuard.removeListener(_onSessionChanged);
    _generation++;
    super.dispose();
  }

  void _onSessionChanged() {
    if (!mounted) return;
    if (!widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      _generation++;
      setState(() {
        _role = null;
        _clinicId = null;
        _verifiedGuardRevision = null;
        _loading = false;
      });
      return;
    }
    unawaited(_load());
  }

  Future<void> _load() async {
    final generation = ++_generation;
    final guard = widget.sessionNavigationGuard;
    final revision = guard.revision;
    if (!mounted) return;
    setState(() {
      _loading = true;
      _role = null;
      _clinicId = null;
      _verifiedGuardRevision = null;
    });
    if (!guard.canAccessProtectedRoutes) {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
      return;
    }
    try {
      final state = await widget.loadSession();
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0) {
        setState(() => _loading = false);
        return;
      }
      setState(() {
        _role = session.role;
        _clinicId = session.clinicContextId;
        _verifiedGuardRevision = revision;
        _loading = false;
      });
    } on Object {
      if (!mounted || generation != _generation || guard.revision != revision) {
        return;
      }
      setState(() => _loading = false);
    }
  }

  void _open(Wd04ClinicalWorkflowLink link) {
    if (_loading ||
        _role == null ||
        _clinicId == null ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes ||
        widget.sessionNavigationGuard.revision != _verifiedGuardRevision ||
        !wd04ClinicalWorkflowLinks(
          role: _role!,
          clinicContextId: _clinicId,
        ).any(
          (allowed) => allowed.id == link.id && allowed.path == link.path,
        )) {
      return;
    }
    widget.onOpen(link.path);
  }

  @override
  Widget build(BuildContext context) {
    final links = _role == null
        ? const <Wd04ClinicalWorkflowLink>[]
        : wd04ClinicalWorkflowLinks(role: _role!, clinicContextId: _clinicId);
    if (_loading) {
      return const Center(
        child: CircularProgressIndicator(key: ValueKey('wd04-hub-loading')),
      );
    }
    if (_role == null ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes ||
        widget.sessionNavigationGuard.revision != _verifiedGuardRevision) {
      return const Center(
        child: Text(
          'Clinical workflow session unavailable. Sign in again.',
          key: ValueKey('wd04-hub-session-unavailable'),
        ),
      );
    }
    return ListView(
      key: const ValueKey('wd04-clinical-workflows-hub'),
      padding: const EdgeInsets.all(20),
      children: [
        Text(
          'Clinical workflows',
          style: Theme.of(context).textTheme.headlineSmall,
        ),
        const SizedBox(height: 8),
        const Text(
          'Online only. Navigation does not grant access: Flask verifies every '
          'role, clinic, patient and mutation. Select patients through verified '
          'worklists. Device capture and printing require separate approvals.',
          key: ValueKey('wd04-backend-authority-notice'),
        ),
        const SizedBox(height: 12),
        if (links.isEmpty)
          const Text(
            'No clinical workflow links for the current role and clinic.',
            key: ValueKey('wd04-no-authorized-links'),
          ),
        for (final link in links)
          Card(
            child: ListTile(
              key: ValueKey('wd04-module-${link.id}'),
              leading: Icon(link.icon),
              title: Text(link.title),
              subtitle: Text(link.description),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _open(link),
            ),
          ),
      ],
    );
  }
}
