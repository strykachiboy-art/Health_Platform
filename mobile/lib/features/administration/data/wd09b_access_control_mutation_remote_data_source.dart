import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

/// One-shot online PATCH actions, never queued, retried or replayed offline.
enum Wd09bChangeKind { role, status, clinic }

/// Matches the Flask Role enum. Super Admin is deliberately not assignable.
const wd09bAssignableRoles = <String>[
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
];

class Wd09bAccessUnavailable implements Exception {
  const Wd09bAccessUnavailable();
}

class Wd09bMutationRejected implements Exception {
  const Wd09bMutationRejected();
}

class Wd09bUser {
  const Wd09bUser({
    required this.id,
    required this.email,
    required this.role,
    required this.isActive,
    required this.clinicId,
  });

  final int id;
  final String email;
  final String role;
  final bool isActive;
  final int? clinicId;

  bool sameAs(Wd09bUser other) =>
      id == other.id &&
      email == other.email &&
      role == other.role &&
      isActive == other.isActive &&
      clinicId == other.clinicId;

  factory Wd09bUser.fromJson(Object? raw) {
    if (raw is! Map<String, dynamic>) {
      throw const FormatException('Invalid access-control user object.');
    }
    final id = raw['id'];
    final email = raw['email'];
    final role = raw['role'];
    final active = raw['is_active'];
    final clinic = raw['clinic_id'];
    if (id is! int ||
        id <= 0 ||
        id > 2147483647 ||
        email is! String ||
        email.trim().isEmpty ||
        email.trim() != email ||
        email.length > 254 ||
        role is! String ||
        !const <String>{
          'super_admin',
          ...wd09bAssignableRoles,
        }.contains(role) ||
        active is! bool ||
        (clinic != null &&
            (clinic is! int || clinic <= 0 || clinic > 2147483647))) {
      throw const FormatException('Invalid access-control user fields.');
    }
    return Wd09bUser(
      id: id,
      email: email,
      role: role,
      isActive: active,
      clinicId: clinic as int?,
    );
  }
}

/// This gate lives for the router lifetime, not the screen lifetime.
/// Claim BEFORE sending PATCH, including if transport later times out.
/// New navigations cannot silently replay the same action for that user.
class Wd09bOneShotGate {
  final Set<String> _claimed = <String>{};

  bool claim({
    required int actorId,
    required String tenant,
    required int userId,
    required Wd09bChangeKind kind,
  }) => _claimed.add('$actorId|$tenant|$userId|${kind.name}');

  bool wasClaimed({
    required int actorId,
    required String tenant,
    required int userId,
    required Wd09bChangeKind kind,
  }) => _claimed.contains('$actorId|$tenant|$userId|${kind.name}');
}

/// Reuses the authenticated client, token and session-revision guard.
/// Backend remains authoritative for actor, target and clinic membership.
class Wd09bAccessControlMutationRemoteDataSource {
  const Wd09bAccessControlMutationRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
    required this.oneShotGate,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  final Wd09bOneShotGate oneShotGate;

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

  Future<AuthTokenResponse> _authorized(int revision) async {
    if (!_current(revision)) {
      throw const Wd09bAccessUnavailable();
    }
    try {
      final state = await loadSession();
      final actor = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          actor == null ||
          actor.userId <= 0 ||
          actor.role.trim().toLowerCase() != 'super_admin' ||
          actor.accessToken.isEmpty ||
          actor.accessToken.trim() != actor.accessToken ||
          !_current(revision)) {
        throw const Wd09bAccessUnavailable();
      }
      return actor;
    } on Wd09bAccessUnavailable {
      rethrow;
    } on Object {
      throw const Wd09bAccessUnavailable();
    }
  }

  static void _positive(int id) {
    if (id <= 0 || id > 2147483647) {
      throw ArgumentError.value(
        id,
        'id',
        'Expected a positive user or clinic ID.',
      );
    }
  }

  static Wd09bUser _envelope(Object? raw, int expectedId) {
    if (raw is! Map<String, dynamic> || raw['success'] != true) {
      throw const FormatException('Invalid access-control response envelope.');
    }
    final user = Wd09bUser.fromJson(raw['data']);
    if (user.id != expectedId) {
      throw const FormatException('Access-control user identity mismatch.');
    }
    return user;
  }

  Future<Wd09bUser> readUser(int userId) async {
    _positive(userId);
    final revision = sessionNavigationGuard.revision;
    final before = await _authorized(revision);
    final raw = await apiClient.getJson(
      'api/v1/access-control/users/$userId',
      bearerToken: before.accessToken,
    );
    final after = await _authorized(revision);
    if (!_current(revision) || !_same(before, after)) {
      throw const Wd09bAccessUnavailable();
    }
    return _envelope(raw, userId);
  }

  /// The screen requires a deliberate confirmation before calling this method.
  /// A fresh GET verifies the displayed target still matches the server.
  /// No implicit retry is permitted: no Flask idempotency contract exists here.
  Future<Wd09bUser> change({
    required Wd09bUser expected,
    required Wd09bChangeKind kind,
    String? newRole,
    bool? newStatus,
    int? destinationClinicId,
    String? reason,
  }) async {
    _positive(expected.id);
    final normalizedReason = reason?.trim();
    if (normalizedReason != null && normalizedReason.length > 500) {
      throw ArgumentError('Reason exceeds backend maximum of 500 characters.');
    }
    final reasonValue = normalizedReason == null || normalizedReason.isEmpty
        ? null
        : normalizedReason;
    if (kind == Wd09bChangeKind.role) {
      if (newRole == null ||
          !wd09bAssignableRoles.contains(newRole) ||
          newRole == expected.role ||
          newStatus != null ||
          destinationClinicId != null) {
        throw const Wd09bMutationRejected();
      }
    } else if (kind == Wd09bChangeKind.status) {
      if (newStatus == null ||
          newStatus == expected.isActive ||
          newRole != null ||
          destinationClinicId != null) {
        throw const Wd09bMutationRejected();
      }
    } else {
      if (destinationClinicId == null ||
          destinationClinicId <= 0 ||
          destinationClinicId > 2147483647 ||
          destinationClinicId == expected.clinicId ||
          newRole != null ||
          newStatus != null ||
          expected.role == 'patient') {
        throw const Wd09bMutationRejected();
      }
    }
    final revision = sessionNavigationGuard.revision;
    final before = await _authorized(revision);
    if (expected.id == before.userId || expected.role == 'super_admin') {
      throw const Wd09bMutationRejected();
    }
    final keyTenant = before.tenantId ?? '';
    if (oneShotGate.wasClaimed(
      actorId: before.userId,
      tenant: keyTenant,
      userId: expected.id,
      kind: kind,
    )) {
      throw const Wd09bMutationRejected();
    }

    // A fresh read prevents applying stale screen values. This is not a
    // backend compare-and-swap and does not replace Flask authorization.
    final latest = await readUser(expected.id);
    if (!expected.sameAs(latest)) {
      throw const Wd09bMutationRejected();
    }
    final checked = await _authorized(revision);
    if (!_current(revision) || !_same(before, checked)) {
      throw const Wd09bAccessUnavailable();
    }
    final payload = <String, dynamic>{
      if (kind == Wd09bChangeKind.role) 'role': newRole,
      if (kind == Wd09bChangeKind.status) 'is_active': newStatus,
      if (kind == Wd09bChangeKind.clinic)
        'destination_clinic_id': destinationClinicId,
    };
    if (reasonValue != null) {
      payload['reason'] = reasonValue;
    }
    if (!oneShotGate.claim(
      actorId: before.userId,
      tenant: keyTenant,
      userId: expected.id,
      kind: kind,
    )) {
      throw const Wd09bMutationRejected();
    }

    // An uncertain timeout/response failure MUST NOT be auto-replayed.
    final raw = await apiClient.patchJson(
      'api/v1/access-control/users/${expected.id}/${kind.name}',
      body: payload,
      bearerToken: before.accessToken,
    );
    final after = await _authorized(revision);
    if (!_current(revision) || !_same(before, after)) {
      throw const Wd09bAccessUnavailable();
    }
    if (raw['success'] != true || raw['data'] is! Map<String, dynamic>) {
      throw const FormatException('Access-control mutation response invalid.');
    }
    final result = raw['data'] as Map<String, dynamic>;
    final changed = Wd09bUser.fromJson(result['user']);
    if (changed.id != expected.id || changed.email != expected.email) {
      throw const FormatException('Access-control mutation user mismatch.');
    }
    if (kind == Wd09bChangeKind.role) {
      if (result['previous_role'] != expected.role ||
          result['new_role'] != newRole ||
          changed.role != newRole ||
          changed.isActive != expected.isActive ||
          changed.clinicId != expected.clinicId) {
        throw const FormatException('Role change receipt mismatch.');
      }
    } else if (kind == Wd09bChangeKind.status) {
      if (result['previous_status'] != expected.isActive ||
          result['new_status'] != newStatus ||
          changed.isActive != newStatus ||
          changed.role != expected.role ||
          changed.clinicId != expected.clinicId) {
        throw const FormatException('Status change receipt mismatch.');
      }
    } else {
      if (result['previous_clinic_id'] != expected.clinicId ||
          result['new_clinic_id'] != destinationClinicId ||
          changed.clinicId != destinationClinicId ||
          changed.role != expected.role ||
          changed.isActive != expected.isActive) {
        throw const FormatException('Clinic transfer receipt mismatch.');
      }
    }
    return changed;
  }
}
