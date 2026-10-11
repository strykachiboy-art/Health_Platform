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

  Future<Patient> updatePatient({
    required String bearerToken,
    required int patientId,
    required Map<String, dynamic> updates,
  }) async {
    _validateToken(bearerToken);
    if (patientId <= 0) {
      throw ArgumentError('patientId must be positive.');
    }
    if (updates.isEmpty) {
      throw ArgumentError('At least one patient field must be changed.');
    }

    const editableFields = <String>{
      'first_name',
      'last_name',
      'date_of_birth',
      'gender',
      'blood_type',
      'phone',
      'email',
      'address',
      'allergies',
      'chronic_conditions',
      'emirates_id',
      'umrn',
    };
    if (updates.keys.any((field) => !editableFields.contains(field))) {
      throw ArgumentError('The update contains an unsupported patient field.');
    }

    final body = <String, dynamic>{};
    for (final entry in updates.entries) {
      final field = entry.key;
      final value = entry.value;

      if (field == 'first_name' || field == 'last_name') {
        if (value is! String) {
          throw ArgumentError('$field must be a string.');
        }
        final normalized = value.trim();
        if (normalized.isEmpty || normalized.length > 80) {
          throw ArgumentError(
            '$field must contain between 1 and 80 characters.',
          );
        }
        body[field] = normalized;
        continue;
      }

      if (field == 'date_of_birth') {
        if (value == null) {
          body[field] = null;
          continue;
        }
        if (value is! DateTime) {
          throw ArgumentError('date_of_birth must be a date or null.');
        }
        final date = DateTime(value.year, value.month, value.day);
        final now = DateTime.now();
        final today = DateTime(now.year, now.month, now.day);
        if (date.isAfter(today)) {
          throw ArgumentError('date_of_birth cannot be in the future.');
        }
        body[field] = _dateOnly(date);
        continue;
      }

      if (field == 'gender') {
        if (value == null) {
          body[field] = null;
          continue;
        }
        if (value is! String) {
          throw ArgumentError('gender must be a string or null.');
        }
        final normalized = value.trim().toLowerCase();
        if (normalized.isEmpty) {
          body[field] = null;
        } else if (const <String>{
          'male',
          'female',
          'other',
        }.contains(normalized)) {
          body[field] = normalized;
        } else {
          throw ArgumentError('gender contains an unsupported value.');
        }
        continue;
      }

      if (field == 'blood_type') {
        if (value == null) {
          body[field] = null;
          continue;
        }
        if (value is! String) {
          throw ArgumentError('blood_type must be a string or null.');
        }
        final normalized = _normalizeBloodType(value);
        if (normalized == null) {
          if (value.trim().isEmpty) {
            body[field] = null;
          } else {
            throw ArgumentError('blood_type contains an unsupported value.');
          }
        } else {
          body[field] = normalized;
        }
        continue;
      }

      if (value == null) {
        body[field] = null;
        continue;
      }
      if (value is! String) {
        throw ArgumentError('$field must be a string or null.');
      }

      final normalized = value.trim();
      if (field == 'phone' && normalized.length > 30) {
        throw ArgumentError('phone must be at most 30 characters.');
      }
      if (field == 'address' && normalized.length > 255) {
        throw ArgumentError('address must be at most 255 characters.');
      }
      if ((field == 'emirates_id' || field == 'umrn') &&
          normalized.length > 50) {
        throw ArgumentError('$field must be at most 50 characters.');
      }
      if (field == 'email' &&
          normalized.isNotEmpty &&
          !RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(normalized)) {
        throw ArgumentError('email must be a valid email address.');
      }
      body[field] = normalized.isEmpty ? null : normalized;
    }

    final envelope = await _apiClient.patchJson(
      'api/v1/patients/$patientId',
      body: body,
      bearerToken: bearerToken,
    );
    return Patient.fromJson(_dataFromEnvelope(envelope));
  }

  static String _dateOnly(DateTime value) {
    final year = value.year.toString().padLeft(4, '0');
    final month = value.month.toString().padLeft(2, '0');
    final day = value.day.toString().padLeft(2, '0');
    return '$year-$month-$day';
  }

  static String? _normalizeBloodType(String value) {
    return switch (value.trim().toUpperCase()) {
      'A+' || 'A_POS' || 'A_POSITIVE' => 'A+',
      'A-' || 'A_NEG' || 'A_NEGATIVE' => 'A-',
      'B+' || 'B_POS' || 'B_POSITIVE' => 'B+',
      'B-' || 'B_NEG' || 'B_NEGATIVE' => 'B-',
      'AB+' || 'AB_POS' || 'AB_POSITIVE' => 'AB+',
      'AB-' || 'AB_NEG' || 'AB_NEGATIVE' => 'AB-',
      'O+' || 'O_POS' || 'O_POSITIVE' => 'O+',
      'O-' || 'O_NEG' || 'O_NEGATIVE' => 'O-',
      'UNKNOWN' => 'unknown',
      _ => null,
    };
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
