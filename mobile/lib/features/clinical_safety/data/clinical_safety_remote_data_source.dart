// ignore_for_file: prefer_initializing_formals

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';

import 'models/clinical_alert.dart';
import 'models/clinical_alert_acknowledgement.dart';
import 'models/clinical_alert_page.dart';

/// Read-only clinical alert transport. Clinical decisions remain server-owned.
/// P21.S01 does NOT add persistence, replay, acknowledgement, or resolution.
class ClinicalSafetyRemoteDataSource {
  const ClinicalSafetyRemoteDataSource({
    required ApiClient apiClient,
    required AuthenticatedAccessTokenLoader loadAccessToken,
  }) : _apiClient = apiClient,
       _loadAccessToken = loadAccessToken;

  final ApiClient _apiClient;
  final AuthenticatedAccessTokenLoader _loadAccessToken;

  /// Intentionally unfiltered until the backend's StrictInt query parsing
  /// contract is repaired and verified. Returns the backend-default first page.
  Future<ClinicalAlertPage> fetchAlerts() async {
    final bearerToken = await _loadAccessToken();
    final response = await _apiClient.getJson(
      'api/v1/clinical-safety/alerts',
      bearerToken: bearerToken,
    );
    return ClinicalAlertPage.fromJson(response);
  }

  Future<ClinicalAlert> fetchAlert({required int alertId}) async {
    if (alertId <= 0) {
      throw ArgumentError.value(alertId, 'alertId', 'Must be positive.');
    }

    final bearerToken = await _loadAccessToken();
    final response = await _apiClient.getJson(
      'api/v1/clinical-safety/alerts/$alertId',
      bearerToken: bearerToken,
    );

    final alert = response['alert'];
    if (alert is! Map<String, dynamic>) {
      throw const FormatException('Clinical alert detail must be an object.');
    }
    return ClinicalAlert.fromJson(alert);
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

    final bearerToken = await _loadAccessToken();
    final response = await _apiClient.postJson(
      'api/v1/clinical-safety/alerts/$alertId/acknowledge',
      body: <String, dynamic>{
        'acknowledgement_type': type.apiValue,
        if (normalizedJustification != null)
          'justification': normalizedJustification,
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
  }
}
