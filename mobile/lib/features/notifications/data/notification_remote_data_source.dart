import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'notification_models.dart';

class NotificationSessionUnavailable implements Exception {
  const NotificationSessionUnavailable();
}

class NotificationWriteOutcomeUnconfirmed implements Exception {
  const NotificationWriteOutcomeUnconfirmed();
}

/// Immutable session identity used for permission and stale-result checks.
class NotificationSessionIdentity {
  const NotificationSessionIdentity(this.session, this.guardRevision);
  final AuthTokenResponse session;
  final int guardRevision;

  String get actionScope =>
      '${session.userId}/${session.clinicContextId}/$guardRevision';
}

/// P24A: backend-authoritative inbox; HTTP POST read actions are never queued.
class NotificationRemoteDataSource {
  const NotificationRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const allowedRoles = <String>{
    'super_admin',
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
  };

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<NotificationSessionIdentity> requireSession({
    int? expectedRevision,
  }) async {
    final revision = expectedRevision ?? sessionNavigationGuard.revision;
    if (!_current(revision)) throw const NotificationSessionUnavailable();
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.trim().isEmpty ||
          session.accessToken != session.accessToken.trim() ||
          session.role.trim().isEmpty ||
          !allowedRoles.contains(session.role.trim().toLowerCase()) ||
          !_current(revision)) {
        throw const NotificationSessionUnavailable();
      }
      return NotificationSessionIdentity(session, revision);
    } on NotificationSessionUnavailable {
      rethrow;
    } on Object {
      throw const NotificationSessionUnavailable();
    }
  }

  static bool _same(
    NotificationSessionIdentity a,
    NotificationSessionIdentity b,
  ) {
    final x = a.session, y = b.session;
    return a.guardRevision == b.guardRevision &&
        x.userId == y.userId &&
        x.clinicContextId == y.clinicContextId &&
        x.role.trim().toLowerCase() == y.role.trim().toLowerCase() &&
        x.accessToken == y.accessToken &&
        x.refreshToken == y.refreshToken;
  }

  /// Refuses to expose data if identity, clinic, role or guard revision changed
  /// while the response was in flight. Caller clears UI on guard invalidation.
  Future<T> _read<T>(
    Future<T> Function(NotificationSessionIdentity) perform,
  ) async {
    final revision = sessionNavigationGuard.revision;
    final before = await requireSession(expectedRevision: revision);
    final result = await perform(before);
    final after = await requireSession(expectedRevision: revision);
    if (!_same(before, after) || !_current(revision)) {
      throw const NotificationSessionUnavailable();
    }
    return result;
  }

  Future<ClinicNotificationPage> list({
    int page = 1,
    int perPage = 20,
    bool unreadOnly = false,
  }) async {
    if (page < 1 || perPage < 1 || perPage > 500) {
      throw ArgumentError('Invalid notification pagination.');
    }
    return _read((identity) async {
      final path = unreadOnly
          ? 'api/v1/notifications/unread'
          : 'api/v1/notifications/';
      final response = await apiClient.getJson(
        '$path?page=$page&per_page=$perPage',
        bearerToken: identity.session.accessToken,
      );
      return ClinicNotificationPage.fromEnvelope(
        response,
        expectedUserId: identity.session.userId,
        expectedClinicId: identity.session.clinicContextId!,
        requestedPage: page,
        requestedPerPage: perPage,
        unreadOnly: unreadOnly,
      );
    });
  }

  Future<ClinicNotification> detail(int id) async {
    if (id < 1) throw ArgumentError('Invalid notification ID.');
    return _read((identity) async {
      final response = await apiClient.getJson(
        'api/v1/notifications/$id',
        bearerToken: identity.session.accessToken,
      );
      return ClinicNotification.fromEnvelope(
        response,
        expectedUserId: identity.session.userId,
        expectedClinicId: identity.session.clinicContextId!,
        expectedId: id,
      );
    });
  }

  /// The caller permanently locks an attempted action after an uncertain POST.
  /// Any request failure after dispatch is an unknown outcome, not "not sent".
  Future<T> _mutate<T>(
    Future<T> Function(NotificationSessionIdentity) perform, {
    required String expectedScope,
  }) async {
    final revision = sessionNavigationGuard.revision;
    final before = await requireSession(expectedRevision: revision);
    if (before.actionScope != expectedScope) {
      throw const NotificationSessionUnavailable();
    }
    try {
      final result = await perform(before);
      final after = await requireSession(expectedRevision: revision);
      if (!_same(before, after) || !_current(revision)) {
        throw const NotificationWriteOutcomeUnconfirmed();
      }
      return result;
    } on NotificationWriteOutcomeUnconfirmed {
      rethrow;
    } on ApiException catch (error) {
      if (const <int>{401, 403, 404, 422}.contains(error.statusCode)) {
        rethrow;
      }
      throw const NotificationWriteOutcomeUnconfirmed();
    } on Object {
      throw const NotificationWriteOutcomeUnconfirmed();
    }
  }

  Future<ClinicNotification> markRead(
    int id, {
    required String expectedScope,
  }) async {
    if (id < 1) throw ArgumentError('Invalid notification ID.');
    return _mutate((identity) async {
      final response = await apiClient.postJson(
        'api/v1/notifications/$id/read',
        body: const <String, dynamic>{},
        bearerToken: identity.session.accessToken,
      );
      final result = ClinicNotification.fromEnvelope(
        response,
        expectedUserId: identity.session.userId,
        expectedClinicId: identity.session.clinicContextId!,
        expectedId: id,
      );
      if (!result.isRead) {
        throw const FormatException('Notification read not confirmed.');
      }
      return result;
    }, expectedScope: expectedScope);
  }

  Future<int> markAllRead({required String expectedScope}) async {
    return _mutate((identity) async {
      final response = await apiClient.postJson(
        'api/v1/notifications/read-all',
        body: const <String, dynamic>{},
        bearerToken: identity.session.accessToken,
      );
      if (response['success'] != true ||
          response['data'] is! Map<String, dynamic>) {
        throw const FormatException('Read-all was not confirmed.');
      }
      final count = (response['data'] as Map<String, dynamic>)['updated_count'];
      if (count is! int || count < 0) {
        throw const FormatException('Invalid read-all result.');
      }
      return count;
    }, expectedScope: expectedScope);
  }
}
