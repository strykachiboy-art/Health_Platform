import 'package:flutter/foundation.dart';

/// App-wide authentication state used to guard sensitive navigation.
///
/// This controller is intentionally separate from token persistence and request
/// handling. `sessionAvailable` means only that the local coordinator has a
/// usable session record; it is not proof of token validity or authorization.
/// Callers must invalidate it immediately when the current session is rejected;
/// the lifecycle handler is responsible for safely clearing local session state.
/// It never deletes queue data or payload keys.
enum SessionNavigationStatus {
  checking,
  sessionAvailable,
  signedOut,
  invalidated,
}

class SessionNavigationGuard extends ChangeNotifier {
  SessionNavigationStatus _status = SessionNavigationStatus.checking;
  int _revision = 0;

  SessionNavigationStatus get status => _status;

  /// Monotonic revision used to reject stale asynchronous authentication results.
  int get revision => _revision;

  bool get canAccessProtectedRoutes =>
      _status == SessionNavigationStatus.sessionAvailable;

  bool get requiresLocalRecovery =>
      _status == SessionNavigationStatus.invalidated;

  void markSessionAvailable() =>
      _setStatus(SessionNavigationStatus.sessionAvailable);

  void markSignedOut() => _setStatus(SessionNavigationStatus.signedOut);

  /// Immediately blocks patient routes and notifies mounted authentication UI.
  void invalidate() => _setStatus(SessionNavigationStatus.invalidated);

  /// Redirect target for session-protected patient routes.
  String? redirect(String matchedLocation) {
    final isPatientRoute =
        matchedLocation == '/patients' ||
        matchedLocation.startsWith('/patients/');
    if (isPatientRoute && !canAccessProtectedRoutes) return '/login';
    return null;
  }

  void _setStatus(SessionNavigationStatus next) {
    if (_status == next) return;
    _status = next;
    _revision++;
    notifyListeners();
  }
}
