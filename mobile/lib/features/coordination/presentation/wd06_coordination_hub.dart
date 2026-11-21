import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

/// Navigation suggestions, never a permission grant or a second workflow.
class Wd06CoordinationLink {
  const Wd06CoordinationLink({
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

/// Reuses already registered Flutter routes. All backend routes recheck roles,
/// owner and clinic. There is no Windows-only push registration or local replay.
List<Wd06CoordinationLink> wd06CoordinationLinks({
  required String role,
  required int? clinicContextId,
}) {
  if (clinicContextId == null || clinicContextId <= 0) {
    return const <Wd06CoordinationLink>[];
  }
  final normalized = role.trim().toLowerCase();
  if (!(const <String>{
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
  }).contains(normalized)) {
    return const <Wd06CoordinationLink>[];
  }

  final links = <Wd06CoordinationLink>[
    const Wd06CoordinationLink(
      id: 'notifications',
      title: 'Notification inbox',
      path: '/notifications',
      description:
          'Verified online inbox. Push delivery is not promised on Windows.',
      icon: Icons.notifications_outlined,
    ),
    const Wd06CoordinationLink(
      id: 'feedback',
      title: 'Feedback and support',
      path: '/feedback',
      description: 'Existing backend-authorized feedback workspace.',
      icon: Icons.feedback_outlined,
    ),
    const Wd06CoordinationLink(
      id: 'backend_status',
      title: 'Backend status',
      path: '/health',
      description:
          'Check Flask health separately from device network connectivity.',
      icon: Icons.monitor_heart_outlined,
    ),
  ];

  if (normalized != 'super_admin') {
    links.addAll(const <Wd06CoordinationLink>[
      Wd06CoordinationLink(
        id: 'devices',
        title: 'My devices',
        path: '/devices',
        description: 'Existing account-owned device and revocation controls.',
        icon: Icons.devices_outlined,
      ),
      Wd06CoordinationLink(
        id: 'session_security',
        title: 'Session security',
        path: '/account/security',
        description: 'Existing session/security review; not a new authentication system.',
        icon: Icons.shield_outlined,
      ),
    ]);
  }

  if (const <String>{'admin', 'doctor', 'nurse'}.contains(normalized)) {
    links.add(
      const Wd06CoordinationLink(
        id: 'chat',
        title: 'Clinic messages',
        path: '/chat',
        description:
            'Existing authenticated chat. No automatic offline message replay.',
        icon: Icons.forum_outlined,
      ),
    );
  }
  if (const <String>{
    'admin',
    'doctor',
    'nurse',
    'receptionist',
  }.contains(normalized)) {
    links.add(
      const Wd06CoordinationLink(
        id: 'pending_work',
        title: 'Pending Work',
        path: '/pending-work',
        description:
            'Existing appointment-only safe queue and reconciliation review.',
        icon: Icons.pending_actions_outlined,
      ),
    );
  }
  if (const <String>{
    'admin',
    'doctor',
    'nurse',
    'pharmacist',
    'lab_technician',
  }.contains(normalized)) {
    links.add(
      const Wd06CoordinationLink(
        id: 'clinical_ai',
        title: 'Clinical AI assistance',
        path: '/clinical-ai',
        description: 'Online-only advisory support; no queued or autonomous clinical actions.',
        icon: Icons.psychology_alt_outlined,
      ),
    );
  }
  return List<Wd06CoordinationLink>.unmodifiable(links);
}

/// Device-link availability is NOT proof that Flask is reachable.
String wd06DeviceNetworkLabel(DeviceNetworkStatus status) => switch (status) {
  DeviceNetworkStatus.connected =>
    'Network link detected. Flask reachability is not verified.',
  DeviceNetworkStatus.disconnected =>
    'Device network disconnected. Clinical writes are not saved offline here.',
  DeviceNetworkStatus.unknown =>
    'Device network status unknown. Verify access on the affected screen.',
};

/// An observed HTTP result is NOT clinical acknowledgement or readback.
String wd06HttpResponseLabel(ApiCommunicationStatus status) => switch (status) {
  ApiCommunicationStatus.notChecked =>
    'No HTTP response observed in this view.',
  ApiCommunicationStatus.responseReceived => 'An HTTP response was received. Clinical/business outcomes still require server readback.',
  ApiCommunicationStatus.responseNotReceived => 'Latest observed HTTP request received no response. Any write outcome may be uncertain; verify before retrying.',
};

/// Guarded desktop coordination panel. Reuses the real auth session, router,
/// network-link source, HTTP transport observations and existing feature UIs.
/// Does not queue, retry, reconnect, generate AI output, or mutate clinical data.
class Wd06CoordinationHub extends StatefulWidget {
  const Wd06CoordinationHub({
    super.key,
    required this.loadSession,
    required this.sessionNavigationGuard,
    required this.onOpen,
    this.connectivity,
    this.apiClient,
  });

  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  final ValueChanged<String> onOpen;
  final DeviceConnectivitySource? connectivity;
  final ApiClient? apiClient;

  @override
  State<Wd06CoordinationHub> createState() => _Wd06CoordinationHubState();
}

class _Wd06CoordinationHubState extends State<Wd06CoordinationHub> {
  AuthTokenResponse? _session;
  int? _verifiedRevision;
  int _generation = 0;
  int _networkGeneration = 0;
  bool _loading = true;
  bool _opening = false;
  DeviceNetworkStatus _deviceStatus = DeviceNetworkStatus.unknown;
  ApiCommunicationStatus _httpStatus = ApiCommunicationStatus.notChecked;
  StreamSubscription<DeviceNetworkStatus>? _networkSubscription;
  StreamSubscription<ApiCommunicationStatus>? _httpSubscription;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard.addListener(_onSessionChanged);
    _attachObservations();
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant Wd06CoordinationHub oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
      oldWidget.sessionNavigationGuard,
      widget.sessionNavigationGuard,
    )) {
      oldWidget.sessionNavigationGuard.removeListener(_onSessionChanged);
      widget.sessionNavigationGuard.addListener(_onSessionChanged);
    }
    if (!identical(oldWidget.connectivity, widget.connectivity) ||
        !identical(oldWidget.apiClient, widget.apiClient)) {
      _detachObservations();
      _deviceStatus = DeviceNetworkStatus.unknown;
      _httpStatus = ApiCommunicationStatus.notChecked;
      _attachObservations();
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
    _generation++;
    _networkGeneration++;
    widget.sessionNavigationGuard.removeListener(_onSessionChanged);
    _detachObservations();
    super.dispose();
  }

  void _attachObservations() {
    _networkSubscription = widget.connectivity?.statusChanges.listen(
      (value) {
        if (!mounted ||
            !widget.sessionNavigationGuard.canAccessProtectedRoutes) {
          return;
        }
        setState(() => _deviceStatus = value);
      },
      onError: (Object _) {
        if (mounted) {
          setState(() => _deviceStatus = DeviceNetworkStatus.unknown);
        }
      },
    );
    _httpSubscription = widget.apiClient?.communicationStatusChanges.listen(
      (value) {
        if (!mounted ||
            !widget.sessionNavigationGuard.canAccessProtectedRoutes) {
          return;
        }
        setState(() => _httpStatus = value);
      },
      onError: (Object _) {
        if (mounted) {
          setState(() => _httpStatus = ApiCommunicationStatus.notChecked);
        }
      },
    );
    unawaited(_checkNetwork());
  }

  void _detachObservations() {
    _networkGeneration++;
    final network = _networkSubscription;
    final http = _httpSubscription;
    _networkSubscription = null;
    _httpSubscription = null;
    if (network != null) unawaited(network.cancel());
    if (http != null) unawaited(http.cancel());
  }

  Future<void> _checkNetwork() async {
    final source = widget.connectivity;
    final observation = ++_networkGeneration;
    if (source == null) return;
    try {
      final status = await source.checkStatus();
      if (!mounted ||
          !identical(source, widget.connectivity) ||
          observation != _networkGeneration ||
          !widget.sessionNavigationGuard.canAccessProtectedRoutes) {
        return;
      }
      setState(() => _deviceStatus = status);
    } on Object {
      if (mounted && observation == _networkGeneration) {
        setState(() => _deviceStatus = DeviceNetworkStatus.unknown);
      }
    }
  }

  void _onSessionChanged() {
    if (!mounted) return;
    _networkGeneration++;
    setState(() {
      _session = null;
      _verifiedRevision = null;
      _httpStatus = ApiCommunicationStatus.notChecked;
      _deviceStatus = DeviceNetworkStatus.unknown;
      _opening = false;
    });
    if (!widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      _generation++;
      setState(() => _loading = false);
    } else {
      unawaited(_load());
      unawaited(_checkNetwork());
    }
  }

  static AuthTokenResponse? _valid(AuthSessionState state) {
    final session = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.userId <= 0 ||
        session.clinicContextId == null ||
        session.clinicContextId! <= 0 ||
        session.accessToken.isEmpty ||
        session.accessToken.trim() != session.accessToken ||
        wd06CoordinationLinks(
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
    if (!mounted) return;
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

  Future<void> _open(Wd06CoordinationLink link) async {
    final current = _session;
    final revision = _verifiedRevision;
    if (_opening ||
        _loading ||
        current == null ||
        revision == null ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes ||
        widget.sessionNavigationGuard.revision != revision ||
        !wd06CoordinationLinks(
          role: current.role,
          clinicContextId: current.clinicContextId,
        ).any((item) => item.id == link.id && item.path == link.path)) {
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
      // A failed session verification cannot grant navigation.
    } finally {
      if (mounted && generation == _generation) {
        setState(() => _opening = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(
        child: CircularProgressIndicator(
          key: ValueKey('wd06-coordination-loading'),
        ),
      );
    }
    final session = _session;
    if (session == null ||
        _verifiedRevision != widget.sessionNavigationGuard.revision ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      return const Center(
        child: Text(
          'Coordination unavailable. Sign in with an authorized clinic role.',
          key: ValueKey('wd06-coordination-unavailable'),
        ),
      );
    }
    final links = wd06CoordinationLinks(
      role: session.role,
      clinicContextId: session.clinicContextId,
    );
    return ListView(
      key: const ValueKey('wd06-coordination-workspace'),
      padding: const EdgeInsets.all(20),
      children: [
        Text(
          'Coordination and resilience',
          style: Theme.of(context).textTheme.headlineSmall,
        ),
        const SizedBox(height: 8),
        const Text(
          'Live session navigation. Existing Flask, session and queue authorities '
          'remain unchanged. Printing is disabled; Windows push delivery is '
          'not claimed. Clinical AI is advisory and online-only.',
          key: ValueKey('wd06-authority-notice'),
        ),
        const SizedBox(height: 16),
        Text(
          'Network and request evidence',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        Text(
          wd06DeviceNetworkLabel(_deviceStatus),
          key: const ValueKey('wd06-network-label'),
        ),
        Text(
          wd06HttpResponseLabel(_httpStatus),
          key: const ValueKey('wd06-http-label'),
        ),
        const Text(
          'Network presence or an HTTP response does not prove a write succeeded. '
          'Do not repeat uncertain payments, dispense, medication, emergency or '
          'approval actions; use authoritative readback or review.',
          key: ValueKey('wd06-uncertain-outcome-warning'),
        ),
        if (widget.connectivity != null)
          TextButton(
            key: const ValueKey('wd06-check-network'),
            onPressed: () => unawaited(_checkNetwork()),
            child: const Text('Recheck device network link'),
          ),
        const Divider(),
        for (final link in links)
          Card(
            child: ListTile(
              key: ValueKey('wd06-module-${link.id}'),
              leading: Icon(link.icon),
              title: Text(link.title),
              subtitle: Text(link.description),
              onTap: _opening ? null : () => unawaited(_open(link)),
            ),
          ),
        const ListTile(
          enabled: false,
          leading: Icon(Icons.print_disabled_outlined),
          title: Text(
            'Clinical printing disabled pending separate release gates',
          ),
        ),
      ],
    );
  }
}
