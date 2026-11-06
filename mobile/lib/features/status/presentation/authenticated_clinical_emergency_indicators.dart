import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';

typedef ClinicalOpenAlertCountLoader = Future<int> Function();
typedef EmergencyStatusRequestLoader = Future<EmergencyAccessGrant> Function({
  required int emergencyAccessId,
});

typedef _StatusSessionStamp = ({
  int userId,
  int clinicId,
  String accessToken,
  String refreshToken,
  String role,
});

const _clinicalAlertRoles = <String>{
  'admin',
  'super_admin',
  'doctor',
  'nurse',
  'pharmacist',
  'lab_technician',
  'paramedic',
  'emt',
};
const _emergencyRequesterRoles = <String>{
  'doctor',
  'nurse',
  'pharmacist',
  'lab_technician',
  'paramedic',
  'emt',
};

Future<_StatusSessionStamp?> _currentSession(
  Future<AuthSessionState> Function() loader,
  SessionNavigationGuard? guard,
) async {
  if (guard != null && !guard.canAccessProtectedRoutes) return null;
  try {
    final state = await loader();
    final session = state.session;
    if (guard != null && !guard.canAccessProtectedRoutes) return null;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.userId <= 0 ||
        session.clinicContextId == null ||
        session.clinicContextId! <= 0 ||
        session.accessToken.isEmpty ||
        session.accessToken.trim() != session.accessToken) {
      return null;
    }
    return (
      userId: session.userId,
      clinicId: session.clinicContextId!,
      accessToken: session.accessToken,
      refreshToken: session.refreshToken,
      role: session.role,
    );
  } on Object {
    return null;
  }
}

/// A separate presentation surface from S04.1 transport and local queue state.
/// A banner is not an authorization grant or proof that clinical care is safe.
class AuthenticatedClinicalEmergencyIndicators extends StatelessWidget {
  const AuthenticatedClinicalEmergencyIndicators({
    super.key,
    required this.loadSession,
    required this.loadOpenAlertCount,
    required this.loadEmergencyRequest,
    required this.emergencyAccessContext,
    this.sessionNavigationGuard,
  });

  final Future<AuthSessionState> Function() loadSession;
  final ClinicalOpenAlertCountLoader loadOpenAlertCount;
  final EmergencyStatusRequestLoader loadEmergencyRequest;
  final EmergencyAccessContext emergencyAccessContext;
  final SessionNavigationGuard? sessionNavigationGuard;

  @override
  Widget build(BuildContext context) {
    return Material(
      key: const ValueKey('p22-safety-indicators'),
      color: Theme.of(context).colorScheme.surfaceContainer,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
        child: Wrap(
          spacing: 18,
          runSpacing: 4,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            _ClinicalIndicator(
              loadSession: loadSession,
              loadOpenAlertCount: loadOpenAlertCount,
              guard: sessionNavigationGuard,
            ),
            _EmergencyIndicator(
              loadSession: loadSession,
              loadRequest: loadEmergencyRequest,
              contextState: emergencyAccessContext,
              guard: sessionNavigationGuard,
            ),
          ],
        ),
      ),
    );
  }
}

class _ClinicalIndicator extends StatefulWidget {
  const _ClinicalIndicator({
    required this.loadSession,
    required this.loadOpenAlertCount,
    required this.guard,
  });

  final Future<AuthSessionState> Function() loadSession;
  final ClinicalOpenAlertCountLoader loadOpenAlertCount;
  final SessionNavigationGuard? guard;

  @override
  State<_ClinicalIndicator> createState() => _ClinicalIndicatorState();
}

class _ClinicalIndicatorState extends State<_ClinicalIndicator>
    with WidgetsBindingObserver {
  int _revision = 0;
  String _label = 'Checking';
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    widget.guard?.addListener(_sessionChanged);
    _refreshTimer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => unawaited(_refresh()),
    );
    unawaited(_refresh());
  }

  @override
  void didUpdateWidget(covariant _ClinicalIndicator oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(widget.guard, oldWidget.guard)) {
      oldWidget.guard?.removeListener(_sessionChanged);
      widget.guard?.addListener(_sessionChanged);
    }
    if (!identical(widget.guard, oldWidget.guard) ||
        !identical(widget.loadSession, oldWidget.loadSession) ||
        !identical(widget.loadOpenAlertCount, oldWidget.loadOpenAlertCount)) {
      unawaited(_refresh());
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) unawaited(_refresh());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    widget.guard?.removeListener(_sessionChanged);
    _refreshTimer?.cancel();
    _revision++;
    super.dispose();
  }

  void _sessionChanged() {
    _revision++;
    if (!mounted) return;
    setState(() => _label = 'Unavailable');
    if (widget.guard?.canAccessProtectedRoutes == true) {
      unawaited(_refresh());
    }
  }

  bool _current(int revision, int? guardRevision) {
    final guard = widget.guard;
    return mounted &&
        revision == _revision &&
        (guard == null ||
            (guard.canAccessProtectedRoutes &&
                guard.revision == guardRevision));
  }

  void _finish(int revision, int? guardRevision, String value) {
    if (!_current(revision, guardRevision)) return;
    setState(() => _label = value);
  }

  Future<void> _refresh() async {
    final revision = ++_revision;
    final guardRevision = widget.guard?.revision;
    if (!mounted) return;
    setState(() => _label = 'Checking');
    try {
      final before = await _currentSession(widget.loadSession, widget.guard);
      if (!_current(revision, guardRevision)) return;
      if (before == null ||
          !_clinicalAlertRoles.contains(before.role.trim().toLowerCase())) {
        _finish(revision, guardRevision, 'Unavailable');
        return;
      }
      final count = await widget.loadOpenAlertCount();
      if (count < 0) throw const FormatException('Negative alert total.');
      if (!_current(revision, guardRevision)) return;
      final after = await _currentSession(widget.loadSession, widget.guard);
      if (!_current(revision, guardRevision)) return;
      _finish(
        revision,
        guardRevision,
        before == after ? '$count open' : 'Unavailable',
      );
    } on Object {
      _finish(revision, guardRevision, 'Unknown');
    }
  }

  @override
  Widget build(BuildContext context) => Text(
    'Clinical alerts: $_label',
    key: const ValueKey('p22-safety-clinical'),
  );
}

class _EmergencyIndicator extends StatefulWidget {
  const _EmergencyIndicator({
    required this.loadSession,
    required this.loadRequest,
    required this.contextState,
    required this.guard,
  });

  final Future<AuthSessionState> Function() loadSession;
  final EmergencyStatusRequestLoader loadRequest;
  final EmergencyAccessContext contextState;
  final SessionNavigationGuard? guard;

  @override
  State<_EmergencyIndicator> createState() => _EmergencyIndicatorState();
}

class _EmergencyIndicatorState extends State<_EmergencyIndicator>
    with WidgetsBindingObserver {
  int _revision = 0;
  String _label = 'Checking';
  Timer? _refreshTimer;
  Timer? _expiryTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    widget.guard?.addListener(_sessionChanged);
    widget.contextState.addListener(_candidateChanged);
    _refreshTimer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => unawaited(_refresh()),
    );
    unawaited(_refresh());
  }

  @override
  void didUpdateWidget(covariant _EmergencyIndicator oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(widget.guard, oldWidget.guard)) {
      oldWidget.guard?.removeListener(_sessionChanged);
      widget.guard?.addListener(_sessionChanged);
    }
    if (!identical(widget.contextState, oldWidget.contextState)) {
      oldWidget.contextState.removeListener(_candidateChanged);
      widget.contextState.addListener(_candidateChanged);
    }
    if (!identical(widget.guard, oldWidget.guard) ||
        !identical(widget.contextState, oldWidget.contextState) ||
        !identical(widget.loadSession, oldWidget.loadSession) ||
        !identical(widget.loadRequest, oldWidget.loadRequest)) {
      unawaited(_refresh());
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) unawaited(_refresh());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    widget.guard?.removeListener(_sessionChanged);
    widget.contextState.removeListener(_candidateChanged);
    _refreshTimer?.cancel();
    _expiryTimer?.cancel();
    _revision++;
    super.dispose();
  }

  void _sessionChanged() {
    _revision++;
    _expiryTimer?.cancel();
    if (!mounted) return;
    setState(() => _label = 'Unavailable');
    if (widget.guard?.canAccessProtectedRoutes == true) {
      unawaited(_refresh());
    }
  }

  void _candidateChanged() => unawaited(_refresh());

  bool _current(int revision, int? guardRevision) {
    final guard = widget.guard;
    return mounted &&
        revision == _revision &&
        (guard == null ||
            (guard.canAccessProtectedRoutes &&
                guard.revision == guardRevision));
  }

  void _finish(int revision, int? guardRevision, String value) {
    if (!_current(revision, guardRevision)) return;
    setState(() => _label = value);
  }

  Future<void> _refresh() async {
    final revision = ++_revision;
    final guardRevision = widget.guard?.revision;
    _expiryTimer?.cancel();
    if (!mounted) return;
    setState(() => _label = 'Checking');
    try {
      final before = await _currentSession(widget.loadSession, widget.guard);
      if (!_current(revision, guardRevision)) return;
      if (before == null ||
          !_emergencyRequesterRoles.contains(
            before.role.trim().toLowerCase(),
          )) {
        _finish(revision, guardRevision, 'Unavailable');
        return;
      }

      // A local candidate is not evidence of a currently active server grant.
      final candidate = widget.contextState.activeGrant;
      if (candidate == null) {
        _finish(revision, guardRevision, 'Unknown');
        return;
      }
      if (candidate.clinicId != before.clinicId ||
          candidate.requesterUserId != before.userId) {
        _finish(revision, guardRevision, 'Unknown');
        return;
      }

      final grant = await widget.loadRequest(emergencyAccessId: candidate.id);
      if (!_current(revision, guardRevision)) return;
      final after = await _currentSession(widget.loadSession, widget.guard);
      if (!_current(revision, guardRevision)) return;
      if (before != after ||
          widget.contextState.activeEmergencyAccessId != candidate.id) {
        _finish(revision, guardRevision, 'Unavailable');
        return;
      }

      // Refuse unexpected cross-clinic/user/patient/scope data, even if the
      // backend responds with a syntactically valid "active" status.
      if (grant.id != candidate.id ||
          grant.clinicId != before.clinicId ||
          grant.requesterUserId != before.userId ||
          grant.patientId != candidate.patientId ||
          grant.scope.toSet().length != candidate.scope.toSet().length ||
          !grant.scope.toSet().containsAll(candidate.scope)) {
        _finish(revision, guardRevision, 'Unknown');
        return;
      }

      final now = DateTime.now().toUtc();
      final expiration = grant.expiresAt;
      final verifiedActive =
          grant.status == EmergencyAccessStatus.active &&
          grant.grantedAt != null &&
          grant.revokedAt == null &&
          expiration != null &&
          expiration.isAfter(now);
      if (!verifiedActive) {
        _finish(revision, guardRevision, 'Not active');
        return;
      }
      _finish(revision, guardRevision, 'Active (server confirmed)');
      _expiryTimer = Timer(expiration.difference(now), () {
        _finish(revision, guardRevision, 'Not active');
      });
    } on Object {
      _finish(revision, guardRevision, 'Unknown');
    }
  }

  @override
  Widget build(BuildContext context) => Text(
    'Emergency access: $_label',
    key: const ValueKey('p22-safety-emergency'),
  );
}
