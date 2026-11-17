import 'dart:typed_data';

import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';

import 'rad7_print_contract.dart';

/// Always generic: no patient detail or PDF contents in errors or logs.
class Rad7PrintUnavailable implements Exception {
  const Rad7PrintUnavailable();

  @override
  String toString() => 'Authorized document copy unavailable.';
}

/// RAD7 -> existing Flask printing route -> Rust PDF renderer.
/// This prepares the authenticated one-shot transport only. The UI action
/// stays disabled until clinical/device release approval; no persistence,
/// local printing, retry or offline replay occurs here.
class Rad7PrintRemoteDataSource {
  const Rad7PrintRemoteDataSource({
    required this.dashboard,
    required this.guard,
    required this.connectivity,
  });

  final DashboardRemoteDataSource dashboard;
  final SessionNavigationGuard guard;
  final DeviceConnectivitySource connectivity;

  bool _current(int revision) =>
      identical(dashboard.sessionNavigationGuard, guard) &&
      guard.canAccessProtectedRoutes &&
      guard.revision == revision;

  Future<AuthTokenResponse> _authorized(
    int revision,
    Rad7PrintKind kind,
  ) async {
    if (!_current(revision)) throw const Rad7PrintUnavailable();
    try {
      final state = await dashboard.loadSession();
      final actor = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          actor == null ||
          actor.userId <= 0 ||
          actor.clinicContextId == null ||
          actor.clinicContextId! <= 0 ||
          actor.accessToken.isEmpty ||
          actor.accessToken.trim() != actor.accessToken ||
          !kind.allowsRole(actor.role) ||
          !_current(revision)) {
        throw const Rad7PrintUnavailable();
      }
      return actor;
    } on Rad7PrintUnavailable {
      rethrow;
    } on Object {
      throw const Rad7PrintUnavailable();
    }
  }

  bool _same(AuthTokenResponse before, AuthTokenResponse after) =>
      before.userId == after.userId &&
      before.clinicContextId == after.clinicContextId &&
      before.role.toLowerCase() == after.role.toLowerCase() &&
      before.accessToken == after.accessToken &&
      before.refreshToken == after.refreshToken;

  /// For a future user-initiated, separately approved print-release action.
  /// Not currently callable from the locked RAD7 printing UI.
  Future<Uint8List> fetchAuthorizedCopy(
    Rad7PrintKind kind,
    int recordId,
  ) async {
    final path = Rad7PrintContract.plannedServerPath(kind, recordId);
    final revision = guard.revision;
    if (path == null || !_current(revision)) {
      throw const Rad7PrintUnavailable();
    }
    final before = await _authorized(revision, kind);
    try {
      if (await connectivity.checkStatus() != DeviceNetworkStatus.connected ||
          !_current(revision)) {
        throw const Rad7PrintUnavailable();
      }
      final verified = await _authorized(revision, kind);
      if (!_same(before, verified)) throw const Rad7PrintUnavailable();
      final data = await dashboard.apiClient.getPdf(
        path,
        bearerToken: before.accessToken,
      );
      final after = await _authorized(revision, kind);
      if (!_same(before, after) ||
          await connectivity.checkStatus() != DeviceNetworkStatus.connected ||
          !_current(revision)) {
        throw const Rad7PrintUnavailable();
      }
      return data;
    } on Object {
      // Drop any received bytes when transport, session or clinic is uncertain.
      throw const Rad7PrintUnavailable();
    }
  }
}
