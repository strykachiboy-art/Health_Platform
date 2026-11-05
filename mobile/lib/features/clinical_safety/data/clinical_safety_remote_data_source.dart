// ignore_for_file: prefer_initializing_formals

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';

import 'models/clinical_alert.dart';
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
}
