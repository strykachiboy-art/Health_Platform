// ignore_for_file: prefer_initializing_formals

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';

import 'clinical_safety_session_scope.dart';

import 'models/clinical_alert.dart';
import 'models/clinical_alert_acknowledgement.dart';
import 'models/clinical_alert_page.dart';

/// Read-only clinical alert transport. Clinical decisions remain server-owned.
/// P21.S01 does NOT add persistence, replay, acknowledgement, or resolution.
/// No HTTP write was dispatched because device connectivity is unavailable.
class ClinicalSafetyOfflineWriteBlockedException implements Exception {
  const ClinicalSafetyOfflineWriteBlockedException();
}

/// A clinical POST may already have committed under an earlier session.
/// Never retry automatically. Reconcile against server audit history.
class ClinicalSafetyWriteOutcomeUnknownException implements Exception {
  const ClinicalSafetyWriteOutcomeUnknownException();
}

class ClinicalSafetyRemoteDataSource {
  const ClinicalSafetyRemoteDataSource({
    required ApiClient apiClient,
    required AuthenticatedAccessTokenLoader loadAccessToken,
    Future<ClinicalSafetySessionStamp> Function()? loadSessionStamp,
    this.deviceConnectivitySource,
  }) : _apiClient = apiClient,
       _loadAccessToken = loadAccessToken,
       _loadSessionStamp = loadSessionStamp;

  final ApiClient _apiClient;
  final AuthenticatedAccessTokenLoader _loadAccessToken;
  final Future<ClinicalSafetySessionStamp> Function()? _loadSessionStamp;

  /// An unavailable local network blocks clinical writes before HTTP dispatch.
  /// When null, the caller must supply a different explicit network policy;
  /// production main.dart always injects the real device connectivity source.
  final DeviceConnectivitySource? deviceConnectivitySource;

  Future<void> _requireConnectedForWrite() async {
    final device = deviceConnectivitySource;
    if (device == null) return; // Legacy test seam; never used in production.
    try {
      if (await device.checkStatus() != DeviceNetworkStatus.connected) {
        throw const ClinicalSafetyOfflineWriteBlockedException();
      }
    } on ClinicalSafetyOfflineWriteBlockedException {
      rethrow;
    } on Object {
      throw const ClinicalSafetyOfflineWriteBlockedException();
    }
  }

  Future<bool> _sessionMatches(
    Future<ClinicalSafetySessionStamp> Function()? loader,
    ClinicalSafetySessionStamp? before,
  ) async {
    if (loader == null) return true;
    try {
      return await loader() == before;
    } on Object {
      return false;
    }
  }

  Future<T> _withSession<T>(
    Future<T> Function(String) operation, {
    bool isMutation = false,
  }) async {
    final stampLoader = _loadSessionStamp;
    final before = stampLoader == null ? null : await stampLoader();
    final token = await _loadAccessToken();
    // The independently obtained token must agree with the authenticated
    // identity+clinic snapshot used to authorize this request.
    if (before != null && token != before.accessToken) {
      throw const AuthenticatedSessionUnavailableException(
        AuthenticatedSessionAccessFailure.recoveryRequired,
      );
    }
    if (!await _sessionMatches(stampLoader, before)) {
      throw const AuthenticatedSessionUnavailableException(
        AuthenticatedSessionAccessFailure.recoveryRequired,
      );
    }
    if (isMutation) {
      // A definitive disconnected/unknown local network state means zero
      // clinical POSTs. This is the write boundary, not merely a UI check.
      await _requireConnectedForWrite();
      if (!await _sessionMatches(stampLoader, before)) {
        throw const AuthenticatedSessionUnavailableException(
          AuthenticatedSessionAccessFailure.recoveryRequired,
        );
      }
    }

    final T result;
    try {
      // A clinical POST may be committed after this call starts. If its
      // session changes, never describe the operation as "not sent".
      result = await operation(token);
    } on Object {
      if (isMutation && !await _sessionMatches(stampLoader, before)) {
        throw const ClinicalSafetyWriteOutcomeUnknownException();
      }
      rethrow;
    }
    if (!await _sessionMatches(stampLoader, before)) {
      if (isMutation) {
        throw const ClinicalSafetyWriteOutcomeUnknownException();
      }
      throw const AuthenticatedSessionUnavailableException(
        AuthenticatedSessionAccessFailure.recoveryRequired,
      );
    }
    // Backend authorization remains authoritative. This also rejects a
    // mismatched-clinic item returned in an otherwise successful response.
    if (before != null) {
      if (result is ClinicalAlert &&
          result.clinicId != before.clinicContextId) {
        throw const FormatException('Alert clinic does not match session.');
      }
      if (result is ClinicalAlertPage &&
          result.items.any((item) => item.clinicId != before.clinicContextId)) {
        throw const FormatException(
          'Alert page clinic does not match session.',
        );
      }
    }
    return result;
  }

  /// Intentionally unfiltered until the backend's StrictInt query parsing
  /// contract is repaired and verified. Returns the backend-default first page.
  Future<ClinicalAlertPage> fetchAlerts() async {
    return _withSession<ClinicalAlertPage>((bearerToken) async {
      final response = await _apiClient.getJson(
        'api/v1/clinical-safety/alerts',
        bearerToken: bearerToken,
      );
      return ClinicalAlertPage.fromJson(response);
    });
  }

  /// Read-only, backend-filtered open alert count. The server owns the count,
  /// clinic authorization and status filter; the UI never counts one page.
  Future<int> fetchOpenAlertCount() async {
    final page = await _withSession<ClinicalAlertPage>((bearerToken) async {
      final response = await _apiClient.getJson(
        'api/v1/clinical-safety/alerts?status=open&per_page=1',
        bearerToken: bearerToken,
      );
      final result = ClinicalAlertPage.fromJson(response);
      if (result.page != 1 ||
          result.perPage != 1 ||
          result.total < result.items.length ||
          result.items.any((item) => item.status != ClinicalAlertStatus.open)) {
        throw const FormatException('Invalid open clinical alert count.');
      }
      return result;
    });
    return page.total;
  }
  Future<ClinicalAlert> fetchAlert({required int alertId}) async {
    if (alertId <= 0) {
      throw ArgumentError.value(alertId, 'alertId', 'Must be positive.');
    }

    return _withSession<ClinicalAlert>((bearerToken) async {
      final response = await _apiClient.getJson(
        'api/v1/clinical-safety/alerts/$alertId',
        bearerToken: bearerToken,
      );

      final alert = response['alert'];
      if (alert is! Map<String, dynamic>) {
        throw const FormatException('Clinical alert detail must be an object.');
      }
      return ClinicalAlert.fromJson(alert);
    });
  }

  /// POST is non-idempotent. Callers MUST NOT retry after unknown outcomes.
  /// A successful acknowledgement is NOT proof that an alert is resolved.
  /// The backend decides role access, hard-rule override constraints, and status.
  Future<ClinicalAlertAcknowledgement> acknowledgeAlert({
    required int alertId,
    required ClinicalAlertAcknowledgementType type,
    String? justification,
  }) async {
    if (alertId <= 0) {
      throw ArgumentError.value(alertId, 'alertId', 'Must be positive.');
    }

    // Never include clinical justification in thrown validation errors/logs.
    var normalizedJustification = justification?.trim();
    if (normalizedJustification != null && normalizedJustification.isEmpty) {
      normalizedJustification = null;
    }
    if (normalizedJustification != null &&
        normalizedJustification.length > 5000) {
      throw ArgumentError('Justification must not exceed 5000 characters.');
    }
    if (type == ClinicalAlertAcknowledgementType.overridden &&
        normalizedJustification == null) {
      throw ArgumentError('Overriding requires a nonblank justification.');
    }

    return _withSession<ClinicalAlertAcknowledgement>((bearerToken) async {
      final response = await _apiClient.postJson(
        'api/v1/clinical-safety/alerts/$alertId/acknowledge',
        body: <String, dynamic>{
          'acknowledgement_type': type.apiValue,
          'justification': ?normalizedJustification,
        },
        bearerToken: bearerToken,
      );

      final value = response['acknowledgement'];
      if (value is! Map<String, dynamic>) {
        throw const FormatException(
          'Clinical acknowledgement response must contain an object.',
        );
      }
      final result = ClinicalAlertAcknowledgement.fromJson(value);
      if (result.alertId != alertId || result.type != type) {
        throw const FormatException(
          'Clinical acknowledgement response does not match the request.',
        );
      }
      return result;
    }, isMutation: true);
  }

  /// Resolution is a non-idempotent, backend-authoritative clinical mutation.
  /// Do not retry on timeouts, connection failures, HTTP 5xx, or invalid
  /// response bodies: the server may already have committed the transition.
  /// The UI must reconcile with a separate GET and must not use this returned
  /// value to grant clinical permission to proceed with blocked care.
  Future<ClinicalAlert> resolveAlert({required int alertId}) async {
    if (alertId <= 0) {
      throw ArgumentError.value(alertId, 'alertId', 'Must be positive.');
    }

    return _withSession<ClinicalAlert>((bearerToken) async {
      final response = await _apiClient.postJson(
        'api/v1/clinical-safety/alerts/$alertId/resolve',
        body: <String, dynamic>{},
        bearerToken: bearerToken,
      );

      final value = response['alert'];
      if (value is! Map<String, dynamic>) {
        throw const FormatException(
          'Clinical resolution response must contain an alert object.',
        );
      }
      final alert = ClinicalAlert.fromJson(value);
      if (alert.id != alertId ||
          alert.status != ClinicalAlertStatus.resolved ||
          alert.resolvedAt == null) {
        throw const FormatException(
          'Clinical resolution response does not confirm the requested alert.',
        );
      }
      return alert;
    }, isMutation: true);
  }
}
