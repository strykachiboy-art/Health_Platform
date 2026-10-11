import 'package:clinic_system_pro/core/networking/api_client.dart';

import 'models/patient.dart';
import 'models/patient_page.dart';

/// Read-only data access for the existing Flask patient endpoints.
class PatientRemoteDataSource {
  const PatientRemoteDataSource({required ApiClient apiClient})
    : _apiClient = apiClient; // ignore: prefer_initializing_formals

  final ApiClient _apiClient;

  Future<PatientPage> fetchPatients({
    required String bearerToken,
    String? search,
    int page = 1,
    int perPage = 25,
    bool activeOnly = true,
  }) async {
    _validateToken(bearerToken);
    if (page < 1) {
      throw ArgumentError.value(page, 'page', 'Must be at least 1.');
    }
    if (perPage < 1 || perPage > 500) {
      throw ArgumentError.value(
        perPage,
        'perPage',
        'Must be between 1 and 500.',
      );
    }

    final normalizedSearch = search?.trim();
    if (normalizedSearch != null && normalizedSearch.length > 255) {
      throw ArgumentError.value(
        search,
        'search',
        'Must be at most 255 characters.',
      );
    }

    final query = <String, String>{
      'page': '$page',
      'per_page': '$perPage',
      'active_only': '$activeOnly',
      if (normalizedSearch != null && normalizedSearch.isNotEmpty)
        'search': normalizedSearch,
    };
    final path = Uri(
      path: 'api/v1/patients',
      queryParameters: query,
    ).toString();
    final envelope = await _apiClient.getJson(path, bearerToken: bearerToken);
    final data = _dataFromEnvelope(envelope);
    return PatientPage.fromJson(data);
  }

  Future<Patient> fetchPatient({
    required String bearerToken,
    required int patientId,
  }) async {
    _validateToken(bearerToken);
    if (patientId <= 0) {
      throw ArgumentError.value(patientId, 'patientId', 'Must be positive.');
    }

    final envelope = await _apiClient.getJson(
      'api/v1/patients/$patientId',
      bearerToken: bearerToken,
    );
    final data = _dataFromEnvelope(envelope);
    return Patient.fromJson(data);
  }

  Map<String, dynamic> _dataFromEnvelope(Map<String, dynamic> envelope) {
    if (envelope['success'] != true) {
      throw const FormatException(
        'Patient API response did not indicate success.',
      );
    }
    final data = envelope['data'];
    if (data is! Map<String, dynamic>) {
      throw const FormatException(
        'Patient API response data must be a JSON object.',
      );
    }
    return data;
  }

  void _validateToken(String token) {
    if (token.isEmpty || token.trim() != token) {
      throw ArgumentError.value(
        token,
        'bearerToken',
        'Must be non-empty and have no surrounding whitespace.',
      );
    }
  }
}
