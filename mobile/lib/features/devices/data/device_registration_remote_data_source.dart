import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'device_registration_models.dart';

class DeviceRegistrationSessionUnavailable implements Exception {
  const DeviceRegistrationSessionUnavailable();
}

class DeviceRegistrationOffline implements Exception {
  const DeviceRegistrationOffline();
}

class DeviceRegistrationOutcomeUnknown implements Exception {
  const DeviceRegistrationOutcomeUnknown();
}

class DeviceRegistrationSession {
  const DeviceRegistrationSession(this.tokenSession, this.revision);

  final AuthTokenResponse tokenSession;
  final int revision;

  /// Scope contains no access/refresh or device tokens.
  String get key =>
      '${tokenSession.userId}/${tokenSession.role.toLowerCase()}/${tokenSession.clinicContextId}/$revision';
}

/// The backend is authoritative for global token uniqueness and JWT ownership.
/// No persistent device token or offline registration queue exists here.
class DeviceRegistrationRemoteDataSource {
  const DeviceRegistrationRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
    required this.connectivity,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  final DeviceConnectivitySource connectivity;

  // Exactly the backend DEVICE_MANAGEMENT_ROLES. Super-admin is not allowed.
  static const allowedRoles = <String>{
    'doctor',
    'nurse',
    'patient',
    'pharmacist',
    'lab_technician',
    'receptionist',
    'admin',
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

  Future<DeviceRegistrationSession> requireSession({
    int? expectedRevision,
  }) async {
    final revision = expectedRevision ?? sessionNavigationGuard.revision;
    if (!_current(revision)) {
      throw const DeviceRegistrationSessionUnavailable();
    }
    try {
      final result = await loadSession();
      final session = result.session;
      if (result.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId < 1 ||
          session.accessToken.isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          session.refreshToken.isEmpty ||
          session.role.trim() != session.role ||
          !allowedRoles.contains(session.role.toLowerCase()) ||
          !_current(revision)) {
        throw const DeviceRegistrationSessionUnavailable();
      }
      // Device registration belongs to the user, not a selected clinic.
      // PATIENT users without a clinic context are supported by the backend.
      return DeviceRegistrationSession(session, revision);
    } on DeviceRegistrationSessionUnavailable {
      rethrow;
    } on Object {
      throw const DeviceRegistrationSessionUnavailable();
    }
  }

  static bool sameSession(
    DeviceRegistrationSession a,
    DeviceRegistrationSession b,
  ) {
    final x = a.tokenSession;
    final y = b.tokenSession;
    return a.revision == b.revision &&
        x.userId == y.userId &&
        x.role == y.role &&
        x.clinicContextId == y.clinicContextId &&
        x.accessToken == y.accessToken &&
        x.refreshToken == y.refreshToken;
  }

  Future<void> assertSameSession(DeviceRegistrationSession before) async {
    final after = await requireSession(expectedRevision: before.revision);
    if (!sameSession(before, after) || !_current(before.revision)) {
      throw const DeviceRegistrationSessionUnavailable();
    }
  }

  Future<void> _requireOnline() async {
    try {
      if (await connectivity.checkStatus() != DeviceNetworkStatus.connected) {
        throw const DeviceRegistrationOffline();
      }
    } on DeviceRegistrationOffline {
      rethrow;
    } on Object {
      throw const DeviceRegistrationOffline();
    }
  }

  /// A POST may succeed remotely even if the response is lost. Never retry
  /// automatically; the coordinator locks an attempted provider-token digest.
  Future<DeviceRegistrationReceipt> register(
    ProviderDeviceToken token, {
    required DeviceRegistrationSession expectedSession,
  }) async {
    await assertSameSession(expectedSession);
    await _requireOnline();
    await assertSameSession(expectedSession);
    final Map<String, dynamic> response;
    try {
      response = await apiClient.postJson(
        'api/v1/users/devices/',
        bearerToken: expectedSession.tokenSession.accessToken,
        body: <String, dynamic>{
          'device_token': token.deviceToken,
          'platform': token.platform.wireValue,
          'device_name': token.deviceName,
        },
      );
    } on ApiException catch (error) {
      // Definitive backend 4xx refusal is not reported as registration success.
      if (const <int>{
        400,
        401,
        403,
        404,
        409,
        422,
      }.contains(error.statusCode)) {
        rethrow;
      }
      throw const DeviceRegistrationOutcomeUnknown();
    } on Object {
      throw const DeviceRegistrationOutcomeUnknown();
    }
    try {
      final receipt = DeviceRegistrationReceipt.fromEnvelope(
        response,
        expectedUserId: expectedSession.tokenSession.userId,
        expectedToken: token,
      );
      await assertSameSession(expectedSession);
      return receipt;
    } on Object {
      // The POST may already have committed; malformed response is uncertain.
      throw const DeviceRegistrationOutcomeUnknown();
    }
  }

  /// Invoked only after a distinct replacement token is confirmed.
  /// The P24E device-management workflow will own reconciliation if uncertain.
  Future<void> deactivatePrevious(
    int deviceId, {
    required DeviceRegistrationSession expectedSession,
  }) async {
    if (deviceId < 1) {
      throw ArgumentError('Invalid device id.');
    }
    await assertSameSession(expectedSession);
    await _requireOnline();
    await assertSameSession(expectedSession);
    final Map<String, dynamic> response;
    try {
      response = await apiClient.postJson(
        'api/v1/users/devices/$deviceId/deactivate',
        bearerToken: expectedSession.tokenSession.accessToken,
        body: const <String, dynamic>{},
      );
    } on Object {
      throw const DeviceRegistrationOutcomeUnknown();
    }
    try {
      if (response['success'] != true ||
          response['data'] is! Map<String, dynamic>) {
        throw const FormatException('Missing device deactivation receipt.');
      }
      final data = response['data'] as Map<String, dynamic>;
      if (data['id'] != deviceId ||
          data['user_id'] != expectedSession.tokenSession.userId ||
          data['is_active'] != false) {
        throw const FormatException('Device deactivation not confirmed.');
      }
      await assertSameSession(expectedSession);
    } on Object {
      throw const DeviceRegistrationOutcomeUnknown();
    }
  }
}
