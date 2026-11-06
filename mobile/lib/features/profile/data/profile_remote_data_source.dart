// ignore_for_file: prefer_initializing_formals

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

/// Only the non-sensitive fields needed for the account screen are retained.
class AuthenticatedProfile {
  const AuthenticatedProfile({
    required this.userId,
    this.email,
    this.staffName,
    this.assignedClinicId,
    this.assignedClinicName,
  });

  final int userId;
  final String? email;
  final String? staffName;
  final int? assignedClinicId;
  final String? assignedClinicName;

  factory AuthenticatedProfile.fromEnvelope(
    Map<String, dynamic> envelope, {
    required int expectedUserId,
    required int? expectedClinicId,
  }) {
    if (envelope['success'] != true) {
      throw const FormatException('Profile response was not successful.');
    }
    final data = envelope['data'];
    if (data is! Map<String, dynamic>) {
      throw const FormatException('Profile data must be an object.');
    }
    final user = data['user'];
    if (user is! Map<String, dynamic> ||
        user['id'] is! int ||
        user['id'] != expectedUserId ||
        user['is_active'] != true) {
      throw const FormatException('Profile identity is invalid.');
    }
    final userClinic = _positiveOptionalInt(user['clinic_id'], 'user clinic');
    if (userClinic != null &&
        expectedClinicId != null &&
        userClinic != expectedClinicId) {
      throw const FormatException('Profile clinic does not match session.');
    }

    final staff = data['staff'];
    final clinic = data['clinic'];
    if (staff != null && staff is! Map<String, dynamic>) {
      throw const FormatException('Staff profile must be an object.');
    }
    if (clinic != null && clinic is! Map<String, dynamic>) {
      throw const FormatException('Clinic profile must be an object.');
    }
    final staffMap = staff as Map<String, dynamic>?;
    final clinicMap = clinic as Map<String, dynamic>?;
    if (staffMap != null) {
      final staffClinic = _positiveOptionalInt(
        staffMap['clinic_id'],
        'staff clinic',
      );
      if (staffClinic == null ||
          (expectedClinicId != null && staffClinic != expectedClinicId)) {
        throw const FormatException('Staff clinic does not match session.');
      }
    }
    final assigned = clinicMap == null
        ? null
        : _positiveOptionalInt(clinicMap['id'], 'clinic');
    if (assigned != null &&
        expectedClinicId != null &&
        assigned != expectedClinicId) {
      throw const FormatException('Profile clinic does not match session.');
    }
    final firstName = _safeOptionalText(staffMap?['first_name']);
    final lastName = _safeOptionalText(staffMap?['last_name']);
    final fullName = [?firstName, ?lastName].join(' ');

    return AuthenticatedProfile(
      userId: expectedUserId,
      email: _safeOptionalText(user['email']),
      staffName: fullName.isEmpty ? null : fullName,
      assignedClinicId: assigned ?? userClinic,
      assignedClinicName: _safeOptionalText(clinicMap?['name']),
    );
  }

  static int? _positiveOptionalInt(Object? raw, String field) {
    if (raw == null) return null;
    if (raw is! int || raw <= 0) {
      throw FormatException('$field ID must be positive.');
    }
    return raw;
  }

  static String? _safeOptionalText(Object? raw) {
    if (raw == null) return null;
    if (raw is! String) {
      throw const FormatException('Profile text field is invalid.');
    }
    final value = raw.trim();
    return value.isEmpty ? null : value;
  }
}

/// A GET-only, session-stamped boundary; no profile PHI is cached on disk.
class ProfileRemoteDataSource {
  const ProfileRemoteDataSource({
    required ApiClient apiClient,
    required this.loadSession,
    this.sessionNavigationGuard,
  }) : _apiClient = apiClient;

  final ApiClient _apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard? sessionNavigationGuard;

  Future<AuthenticatedProfile> fetchMyProfile() async {
    final guard = sessionNavigationGuard;
    final revision = guard?.revision;
    if (guard != null && !guard.canAccessProtectedRoutes) {
      throw StateError('Authenticated profile session unavailable.');
    }
    final before = await loadSession();
    final session = before.session;
    if (before.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.userId <= 0 ||
        session.role.trim().toUpperCase() == 'SUPER_ADMIN') {
      // The backend's /profile/me role allowlist excludes super-admin.
      throw StateError('Profile endpoint is unavailable for this account.');
    }
    if (guard != null &&
        (!guard.canAccessProtectedRoutes || guard.revision != revision)) {
      throw StateError('Profile session changed before request.');
    }
    final response = await _apiClient.getJson(
      'api/v1/profile/me',
      bearerToken: session.accessToken,
    );
    final after = await loadSession();
    if ((guard != null &&
            (!guard.canAccessProtectedRoutes || guard.revision != revision)) ||
        after.disposition != AuthSessionDisposition.ready ||
        !_sameSession(session, after.session)) {
      throw StateError('Profile session changed during request.');
    }
    return AuthenticatedProfile.fromEnvelope(
      response,
      expectedUserId: session.userId,
      expectedClinicId: session.clinicContextId,
    );
  }

  static bool _sameSession(AuthTokenResponse a, AuthTokenResponse? b) =>
      b != null &&
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;
}
