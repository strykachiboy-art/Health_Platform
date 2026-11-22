import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

/// Flask audit: admin assigned clinic only; super admin global. Access-control
/// users: super admin only. All permissions are checked again by Flask.
enum Wd09ReadKind { auditLogs, users }

class Wd09ReadUnavailable implements Exception {
  const Wd09ReadUnavailable();
}

class Wd09ReadItem {
  const Wd09ReadItem({
    required this.id,
    required this.title,
    required this.subtitle,
    required this.details,
  });

  final int id;
  final String title;
  final String subtitle;
  final String details;
}

class Wd09ReadPage {
  const Wd09ReadPage({
    required this.items,
    required this.page,
    required this.perPage,
    required this.total,
  });

  final List<Wd09ReadItem> items;
  final int page;
  final int perPage;
  final int total;
  bool get hasNext => page * perPage < total;
}

/// No result cache or local access grant. Session changes invalidate results.
/// All HTTP methods here are GET. No direct export or privileged mutation.
class Wd09PrivilegedReadRemoteDataSource {
  const Wd09PrivilegedReadRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  static bool allowed(Wd09ReadKind kind, String role, int? clinic) {
    final normalized = role.trim().toLowerCase();
    if (kind == Wd09ReadKind.users) return normalized == 'super_admin';
    return normalized == 'super_admin' ||
        (normalized == 'admin' && clinic != null && clinic > 0);
  }

  Future<AuthTokenResponse> _session(Wd09ReadKind kind, int revision) async {
    if (!_current(revision)) throw const Wd09ReadUnavailable();
    try {
      final state = await loadSession();
      final actor = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          actor == null ||
          actor.userId <= 0 ||
          actor.accessToken.isEmpty ||
          actor.accessToken.trim() != actor.accessToken ||
          !allowed(kind, actor.role, actor.clinicContextId) ||
          !_current(revision)) {
        throw const Wd09ReadUnavailable();
      }
      return actor;
    } on Wd09ReadUnavailable {
      rethrow;
    } on Object {
      throw const Wd09ReadUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.tenantId == b.tenantId &&
      a.clinicContextId == b.clinicContextId &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  static int _positive(Object? value) {
    if (value is! int || value < 1 || value > 2147483647) {
      throw const FormatException('Invalid privileged record identifier.');
    }
    return value;
  }

  static int? _optionalId(Object? value) =>
      value == null ? null : _positive(value);

  static String _label(Object? value, {int max = 250}) {
    if (value is! String ||
        value.trim().isEmpty ||
        value.trim() != value ||
        value.length > max) {
      throw const FormatException('Invalid privileged record label.');
    }
    return value;
  }

  static String? _optionalLabel(Object? value, {int max = 250}) =>
      value == null ? null : _label(value, max: max);

  static Map<String, dynamic> _object(Object? value) {
    if (value is! Map<String, dynamic>) {
      throw const FormatException('Invalid privileged response object.');
    }
    return value;
  }

  static Wd09ReadItem _audit(Map<String, dynamic> row, AuthTokenResponse user) {
    final id = _positive(row['id']);
    final clinic = _optionalId(row['clinic_id']);
    if (user.role.trim().toLowerCase() == 'admin' &&
        clinic != user.clinicContextId) {
      throw const FormatException('Audit record clinic scope mismatch.');
    }
    final actor = _optionalId(row['user_id']);
    final patient = _optionalId(row['patient_id']);
    final entity = _label(row['entity_type'], max: 80);
    final entityId = _positive(row['entity_id']);
    final action = _label(row['action'], max: 50);
    final created = _label(row['created_at'], max: 80);
    if (DateTime.tryParse(created) == null ||
        !RegExp(r'(Z|[+-]\d{2}:\d{2})$').hasMatch(created)) {
      throw const FormatException('Audit timestamp lacks timezone.');
    }
    final description = _optionalLabel(row['description'], max: 255);
    final reason = _optionalLabel(row['reason'], max: 1000);
    final operationId = _optionalLabel(row['operation_id'], max: 255);
    // Do not render unstructured old_value/new_value or potentially sensitive IP.
    return Wd09ReadItem(
      id: id,
      title: '$action · $entity #$entityId',
      subtitle: '$created · Clinic ${clinic ?? 'system'}',
      details:
          'Audit #$id\nAction: $action\nEntity: $entity #$entityId\n'
          'Clinic: ${clinic ?? 'system'}\nUser: ${actor ?? 'system'}\n'
          'Patient: ${patient ?? 'not recorded'}\nCreated: $created\n'
          'Description: ${description ?? 'not provided'}\n'
          'Reason: ${reason ?? 'not provided'}\n'
          'Operation: ${operationId ?? 'not provided'}',
    );
  }

  static Wd09ReadItem _user(Map<String, dynamic> row) {
    final id = _positive(row['id']);
    final email = _label(row['email'], max: 120);
    final role = _label(row['role'], max: 60);
    final active = row['is_active'];
    if (active is! bool) {
      throw const FormatException('Invalid user status.');
    }
    final clinic = _optionalId(row['clinic_id']);
    return Wd09ReadItem(
      id: id,
      title: email,
      subtitle: '$role · ${active ? 'Active' : 'Inactive'}',
      details:
          'User #$id\nEmail: $email\nRole: $role\n'
          'Account: ${active ? 'Active' : 'Inactive'}\n'
          'Assigned clinic: ${clinic ?? 'none'}',
    );
  }

  static Wd09ReadItem _parseRecord(
    Object? value,
    Wd09ReadKind kind,
    AuthTokenResponse actor,
  ) {
    final row = _object(value);
    return kind == Wd09ReadKind.auditLogs ? _audit(row, actor) : _user(row);
  }

  Future<Wd09ReadPage> loadPage({
    required Wd09ReadKind kind,
    int page = 1,
    int perPage = 20,
  }) async {
    if (page < 1 || page > 100000 || perPage < 1 || perPage > 100) {
      throw ArgumentError('Invalid privileged pagination.');
    }
    final revision = sessionNavigationGuard.revision;
    final before = await _session(kind, revision);
    final path = kind == Wd09ReadKind.auditLogs
        ? 'api/v1/audit-logs'
        : 'api/v1/access-control/users';
    final response = await apiClient.getJson(
      '$path?page=$page&per_page=$perPage',
      bearerToken: before.accessToken,
    );
    final after = await _session(kind, revision);
    if (!_same(before, after) || !_current(revision)) {
      throw const Wd09ReadUnavailable();
    }
    if (response['success'] != true) {
      throw const FormatException('Privileged response rejected.');
    }
    final data = _object(response['data']);
    final rawItems = data['items'];
    final metadata = kind == Wd09ReadKind.users
        ? _object(data['pagination'])
        : data;
    if (rawItems is! List ||
        rawItems.length > perPage ||
        metadata['page'] != page ||
        metadata['per_page'] != perPage ||
        metadata['total'] is! int ||
        metadata['total'] < 0 ||
        (rawItems.length > (metadata['total'] as int))) {
      throw const FormatException('Invalid privileged pagination contract.');
    }
    final items = <Wd09ReadItem>[];
    final ids = <int>{};
    for (final value in rawItems) {
      final parsed = _parseRecord(value, kind, before);
      if (!ids.add(parsed.id)) {
        throw const FormatException('Duplicate privileged record.');
      }
      items.add(parsed);
    }
    return Wd09ReadPage(
      items: List<Wd09ReadItem>.unmodifiable(items),
      page: page,
      perPage: perPage,
      total: metadata['total'] as int,
    );
  }

  Future<Wd09ReadItem> loadDetail({
    required Wd09ReadKind kind,
    required int id,
  }) async {
    _positive(id);
    final revision = sessionNavigationGuard.revision;
    final before = await _session(kind, revision);
    final path = kind == Wd09ReadKind.auditLogs
        ? 'api/v1/audit-logs/$id'
        : 'api/v1/access-control/users/$id';
    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    final after = await _session(kind, revision);
    if (!_same(before, after) || !_current(revision)) {
      throw const Wd09ReadUnavailable();
    }
    if (response['success'] != true) {
      throw const FormatException('Privileged response rejected.');
    }
    final item = _parseRecord(response['data'], kind, before);
    if (item.id != id) {
      throw const FormatException('Privileged detail ID mismatch.');
    }
    return item;
  }
}
