import 'package:flutter/foundation.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';

typedef EmergencyAccessNowUtc = DateTime Function();

class EmergencyAccessContext extends ChangeNotifier {
  factory EmergencyAccessContext({
    SessionNavigationGuard? sessionNavigationGuard,
    EmergencyAccessNowUtc? nowUtc,
  }) {
    return EmergencyAccessContext._(
      sessionNavigationGuard,
      nowUtc ?? _defaultNowUtc,
    );
  }

  EmergencyAccessContext._(this._sessionNavigationGuard, this._nowUtc) {
    _sessionNavigationGuard?.addListener(_handleSessionNavigationChanged);
  }

  final SessionNavigationGuard? _sessionNavigationGuard;
  final EmergencyAccessNowUtc _nowUtc;

  EmergencyAccessGrant? _activeGrant;

  EmergencyAccessGrant? get activeGrant {
    final grant = _activeGrant;
    if (grant == null || !_isUsable(grant)) {
      return null;
    }
    return grant;
  }

  int? get activeEmergencyAccessId => activeGrant?.id;

  int? get activePatientId => activeGrant?.patientId;

  bool get hasUsableActiveGrant => activeGrant != null;

  int? emergencyAccessIdForPatient(int patientId) {
    if (patientId <= 0) {
      throw ArgumentError.value(patientId, 'patientId', 'Must be positive.');
    }

    final grant = activeGrant;

    if (grant == null || grant.patientId != patientId) {
      return null;
    }

    return grant.id;
  }

  void activate(EmergencyAccessGrant grant) {
    final guard = _sessionNavigationGuard;

    if (guard != null && !guard.canAccessProtectedRoutes) {
      throw StateError(
        'An authenticated session is required before emergency access can be activated.',
      );
    }

    if (!_isUsable(grant)) {
      throw ArgumentError.value(
        grant.id,
        'grant',
        'Only active, unexpired emergency access can be used.',
      );
    }

    _activeGrant = grant;
    notifyListeners();
  }

  void reconcile(EmergencyAccessGrant grant) {
    if (_activeGrant?.id != grant.id) {
      return;
    }

    if (_isUsable(grant)) {
      _activeGrant = grant;
      notifyListeners();
      return;
    }

    clear();
  }

  void clearIfGrantId(int emergencyAccessId) {
    if (_activeGrant?.id == emergencyAccessId) {
      clear();
    }
  }

  void clear() {
    if (_activeGrant == null) {
      return;
    }

    _activeGrant = null;
    notifyListeners();
  }

  bool _isUsable(EmergencyAccessGrant grant) {
    final expiresAt = grant.expiresAt;

    return grant.status == EmergencyAccessStatus.active &&
        expiresAt != null &&
        expiresAt.isAfter(_nowUtc());
  }

  void _handleSessionNavigationChanged() {
    if (!(_sessionNavigationGuard?.canAccessProtectedRoutes ?? true)) {
      clear();
    }
  }

  static DateTime _defaultNowUtc() => DateTime.now().toUtc();

  @override
  void dispose() {
    _sessionNavigationGuard?.removeListener(_handleSessionNavigationChanged);
    super.dispose();
  }
}
