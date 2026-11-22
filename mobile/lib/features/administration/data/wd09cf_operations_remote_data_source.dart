import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'wd09cf_operation_contract.dart';

class Wd09cfUnavailable implements Exception {
  const Wd09cfUnavailable();
}

class Wd09cfAlreadyAttempted implements Exception {
  const Wd09cfAlreadyAttempted();
}

/// Retained by the router so a failed/uncertain response is never replayed
/// automatically when a screen is reconstructed. No new queue or storage.
class Wd09cfOneShotGate {
  final Set<String> _attempts = <String>{};
  bool claim(String key) => _attempts.add(key);
  bool contains(String key) => _attempts.contains(key);
}

class Wd09cfReceipt {
  const Wd09cfReceipt(this.recordId, this.clinicId, this.label);
  final int recordId;
  final int clinicId;
  final String label;
}

/// All 19 actions bind to pinned Flask paths and schemas. No user-provided
/// endpoint, actor/clinic ID or arbitrary JSON is accepted. Server is final
/// authorization/ownership authority. This adapter never retries mutations.
class Wd09cfOperationsRemoteDataSource {
  const Wd09cfOperationsRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
    required this.gate,
  });
  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  final Wd09cfOneShotGate gate;

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.clinicContextId == b.clinicContextId &&
      a.tenantId == b.tenantId &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> _authorized(
    Wd09cfAction action,
    int revision,
  ) async {
    if (!_current(revision)) {
      throw const Wd09cfUnavailable();
    }
    try {
      final state = await loadSession();
      final user = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          user == null ||
          user.userId <= 0 ||
          user.accessToken.isEmpty ||
          user.accessToken.trim() != user.accessToken ||
          !action.allowedFor(user.role, user.clinicContextId) ||
          !_current(revision)) {
        throw const Wd09cfUnavailable();
      }
      return user;
    } on Wd09cfUnavailable {
      rethrow;
    } on Object {
      throw const Wd09cfUnavailable();
    }
  }

  /// The operations screen uses this to discover allowed choices; values are
  /// returned only after verifying the actual persisted session, not a role hint.
  Future<AuthTokenResponse> authorizedSession() async {
    final revision = sessionNavigationGuard.revision;
    if (!_current(revision)) {
      throw const Wd09cfUnavailable();
    }
    final state = await loadSession();
    final user = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        user == null ||
        user.userId <= 0 ||
        user.clinicContextId == null ||
        user.clinicContextId! <= 0 ||
        user.accessToken.isEmpty ||
        user.accessToken.trim() != user.accessToken ||
        !Wd09cfAction.values.any(
          (op) => op.allowedFor(user.role, user.clinicContextId),
        ) ||
        !_current(revision)) {
      throw const Wd09cfUnavailable();
    }
    return user;
  }

  static Map<String, dynamic> _object(Object? obj) {
    if (obj is! Map<String, dynamic>) {
      throw const FormatException('Invalid server response object.');
    }
    return obj;
  }

  static int _id(Object? raw) {
    if (raw is! int || raw <= 0 || raw > 2147483647) {
      throw const FormatException('Invalid server record ID.');
    }
    return raw;
  }

  static Map<String, dynamic> _record(
    Map<String, dynamic> response,
    Wd09cfAction action, {
    bool isAssignment = false,
  }) {
    if (response['success'] == false) {
      throw const FormatException('The server rejected the mutation.');
    }
    final key = isAssignment
        ? 'assignment'
        : (action == Wd09cfAction.assetCreate ||
              action == Wd09cfAction.assetUpdate ||
              action == Wd09cfAction.assetRetire ||
              action == Wd09cfAction.assetDispose)
        ? 'asset'
        : 'data';
    return _object(response[key]);
  }

  static String _gateKey(
    AuthTokenResponse actor,
    Wd09cfAction action,
    int? id,
  ) =>
      '${actor.userId}|${actor.tenantId ?? ''}|'
      '${actor.clinicContextId}|${action.name}|${id ?? 'create'}';

  /// A fresh server read binds writes to the selected record and clinic.
  /// For settings, the caller must provide the backend's exact version.
  /// This is not a database compare-and-swap for endpoints without one.
  Future<void> _preRead({
    required Wd09cfAction action,
    required AuthTokenResponse actor,
    required int revision,
    required int? recordId,
    required Map<String, dynamic> body,
  }) async {
    final base = action.recordReadPrefix;
    if (base == null && action != Wd09cfAction.settingsUpdate) {
      return;
    }
    final uri = action == Wd09cfAction.settingsUpdate
        ? 'api/v1/settings/clinic'
        : '$base/$recordId';
    final raw = await apiClient.getJson(uri, bearerToken: actor.accessToken);
    final after = await _authorized(action, revision);
    if (!_current(revision) || !_same(actor, after)) {
      throw const Wd09cfUnavailable();
    }
    final Map<String, dynamic> row;
    if (action == Wd09cfAction.settingsUpdate) {
      row = _object(raw['data']);
    } else if (action == Wd09cfAction.assetAssign ||
        action == Wd09cfAction.assetReturn) {
      // GET /assets/{id} returns 'asset'; POST assign/return returns 'assignment'.
      row = _object(raw['asset']);
    } else {
      row = _record(raw, action);
    }
    if (row['clinic_id'] != actor.clinicContextId) {
      throw const FormatException('Server record belongs to another clinic.');
    }
    if (action == Wd09cfAction.settingsUpdate) {
      final version = row['version'];
      if (version is! int ||
          version <= 0 ||
          body['expected_version'] != version) {
        throw const FormatException('Settings version changed. Refresh first.');
      }
    } else if (row['id'] != recordId) {
      throw const FormatException('Record identity changed.');
    }
  }

  Future<Wd09cfReceipt> execute({
    required Wd09cfAction action,
    required Map<String, String> fields,
    int? recordId,
  }) async {
    // All caller data is validated BEFORE any network mutation is attempted.
    if (action.needsRecordId) {
      if (recordId == null || recordId <= 0 || recordId > 2147483647) {
        throw ArgumentError('A positive record ID is required.');
      }
    } else if (recordId != null) {
      throw ArgumentError('This action does not accept a record ID.');
    }
    final values = action.parseBody(fields);
    final revision = sessionNavigationGuard.revision;
    final before = await _authorized(action, revision);
    final key = _gateKey(before, action, recordId);
    if (gate.contains(key)) {
      throw const Wd09cfAlreadyAttempted();
    }
    await _preRead(
      action: action,
      actor: before,
      revision: revision,
      recordId: recordId,
      body: values,
    );
    final checked = await _authorized(action, revision);
    if (!_current(revision) || !_same(before, checked)) {
      throw const Wd09cfUnavailable();
    }
    if (gate.contains(key)) {
      throw const Wd09cfAlreadyAttempted();
    }
    final body = Map<String, dynamic>.from(values);
    var path = action.path(recordId);
    if (action == Wd09cfAction.settingsUpdate) {
      final version = body.remove('expected_version') as int;
      path = '$path?expected_version=$version';
    }
    if (!gate.claim(key)) {
      throw const Wd09cfAlreadyAttempted();
    }
    // A timeout may mean the server persisted the change. No auto-retry.
    final response = action.verb == 'POST'
        ? await apiClient.postJson(
            path,
            body: body,
            bearerToken: before.accessToken,
          )
        : await apiClient.patchJson(
            path,
            body: body,
            bearerToken: before.accessToken,
          );
    final after = await _authorized(action, revision);
    if (!_current(revision) || !_same(before, after)) {
      throw const Wd09cfUnavailable();
    }
    final assignment =
        action == Wd09cfAction.assetAssign ||
        action == Wd09cfAction.assetReturn;
    final row = _record(response, action, isAssignment: assignment);
    final id = _id(row['id']);
    if (row['clinic_id'] != before.clinicContextId) {
      throw const FormatException('Mutation receipt clinic mismatch.');
    }
    if (action.needsRecordId && !assignment && id != recordId) {
      throw const FormatException('Mutation receipt record mismatch.');
    }
    if (assignment && row['asset_id'] != recordId) {
      throw const FormatException('Assignment asset mismatch.');
    }
    if (action == Wd09cfAction.settingsUpdate &&
        (row['version'] is! int ||
            row['version'] != (values['expected_version'] as int) + 1)) {
      throw const FormatException('Settings version receipt mismatch.');
    }
    return Wd09cfReceipt(id, before.clinicContextId!, action.title);
  }
}
