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
  SessionNavigationGuard({
    Iterable<String> protectedRoutePrefixes = const <String>['/patients'],
  }) : _protectedRoutePrefixes = List<String>.unmodifiable(
         _validateProtectedRoutePrefixes(protectedRoutePrefixes),
       );

  final List<String> _protectedRoutePrefixes;

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

  /// Immediately blocks protected routes and notifies mounted authentication UI.
  void invalidate() => _setStatus(SessionNavigationStatus.invalidated);

  /// Redirect target for any configured session-protected route family.
  String? redirect(String matchedLocation) {
    if (_isProtectedRoute(matchedLocation) && !canAccessProtectedRoutes) {
      return '/login';
    }
    return null;
  }

  bool _isProtectedRoute(String matchedLocation) {
    return _protectedRoutePrefixes.any(
      (prefix) =>
          matchedLocation == prefix || matchedLocation.startsWith('$prefix/'),
    );
  }

  static List<String> _validateProtectedRoutePrefixes(
    Iterable<String> prefixes,
  ) {
    final validated = <String>[];

    for (final prefix in prefixes) {
      if (prefix.isEmpty ||
          prefix != prefix.trim() ||
          !prefix.startsWith('/') ||
          prefix == '/' ||
          prefix.endsWith('/')) {
        throw ArgumentError.value(
          prefix,
          'protectedRoutePrefixes',
          'Each prefix must be an absolute non-root route without a trailing slash.',
        );
      }

      if (!validated.contains(prefix)) {
        validated.add(prefix);
      }
    }

    if (validated.isEmpty) {
      throw ArgumentError.value(
        prefixes,
        'protectedRoutePrefixes',
        'At least one protected route prefix is required.',
      );
    }

    return validated;
  }

  void _setStatus(SessionNavigationStatus next) {
    if (_status == next) return;
    _status = next;
    _revision++;
    notifyListeners();
  }
}
