import 'package:clinic_system_pro/core/networking/api_client.dart';

import 'models/patient.dart';
import 'models/patient_page.dart';

/// Data access for the existing Flask patient endpoints.
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
      throw ArgumentError('page must be at least 1.');
    }
    if (perPage < 1 || perPage > 500) {
      throw ArgumentError('perPage must be between 1 and 500.');
    }

    final normalizedSearch = search?.trim();
    if (normalizedSearch != null && normalizedSearch.length > 255) {
      throw ArgumentError('search must be at most 255 characters.');
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
      throw ArgumentError('patientId must be positive.');
    }

    final envelope = await _apiClient.getJson(
      'api/v1/patients/$patientId',
      bearerToken: bearerToken,
    );
    final data = _dataFromEnvelope(envelope);
    return Patient.fromJson(data);
  }

  Future<Patient> createPatient({
    required String bearerToken,
    required String firstName,
    required String lastName,
    DateTime? dateOfBirth,
    String? gender,
    String? bloodType,
    String? phone,
    String? email,
    String? address,
    String? allergies,
    String? chronicConditions,
    String? emiratesId,
    String? umrn,
  }) async {
    _validateToken(bearerToken);

    final normalizedFirstName = firstName.trim();
    final normalizedLastName = lastName.trim();

    if (normalizedFirstName.isEmpty || normalizedFirstName.length > 80) {
      throw ArgumentError(
        'firstName must contain between 1 and 80 characters.',
      );
    }

    if (normalizedLastName.isEmpty || normalizedLastName.length > 80) {
      throw ArgumentError('lastName must contain between 1 and 80 characters.');
    }

    if (dateOfBirth != null) {
      final today = DateTime.now();
      final birthDay = DateTime(
        dateOfBirth.year,
        dateOfBirth.month,
        dateOfBirth.day,
      );
      final todayOnly = DateTime(today.year, today.month, today.day);

      if (birthDay.isAfter(todayOnly)) {
        throw ArgumentError('dateOfBirth cannot be in the future.');
      }
    }

    const validGenders = <String>{'male', 'female', 'other'};
    const validBloodTypes = <String>{
      'A+',
      'A-',
      'B+',
      'B-',
      'AB+',
      'AB-',
      'O+',
      'O-',
      'unknown',
    };

    if (gender != null && !validGenders.contains(gender)) {
      throw ArgumentError('gender contains an unsupported value.');
    }

    if (bloodType != null && !validBloodTypes.contains(bloodType)) {
      throw ArgumentError('bloodType contains an unsupported value.');
    }

    final body = <String, dynamic>{
      'first_name': normalizedFirstName,
      'last_name': normalizedLastName,
      if (dateOfBirth != null) 'date_of_birth': _dateOnly(dateOfBirth),
      'gender': ?gender,
      'blood_type': ?bloodType,
    };

    _addOptionalString(body, 'phone', phone, maxLength: 30);
    _addOptionalString(body, 'email', email);
    _addOptionalString(body, 'address', address, maxLength: 255);
    _addOptionalString(body, 'allergies', allergies);
    _addOptionalString(body, 'chronic_conditions', chronicConditions);
    _addOptionalString(body, 'emirates_id', emiratesId, maxLength: 50);
    _addOptionalString(body, 'umrn', umrn, maxLength: 50);

    final envelope = await _apiClient.postJson(
      'api/v1/patients',
      body: body,
      bearerToken: bearerToken,
    );

    return Patient.fromJson(_dataFromEnvelope(envelope));
  }

  static void _addOptionalString(
    Map<String, dynamic> body,
    String field,
    String? value, {
    int? maxLength,
  }) {
    if (value == null) return;

    final normalized = value.trim();
    if (normalized.isEmpty) return;

    if (maxLength != null && normalized.length > maxLength) {
      throw ArgumentError(
        'Optional patient field exceeds its maximum allowed length.',
      );
    }

    body[field] = normalized;
  }

  static String _dateOnly(DateTime value) {
    final year = value.year.toString().padLeft(4, '0');
    final month = value.month.toString().padLeft(2, '0');
    final day = value.day.toString().padLeft(2, '0');
    return '$year-$month-$day';
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
      throw ArgumentError(
        'bearerToken must be non-empty and have no surrounding whitespace.',
      );
    }
  }
}
