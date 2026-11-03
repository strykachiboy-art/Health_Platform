// ignore_for_file: prefer_initializing_formals

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';

import 'models/emergency_access_grant.dart';

class EmergencyAccessRemoteDataSource {
  const EmergencyAccessRemoteDataSource({
    required ApiClient apiClient,
    required AuthenticatedAccessTokenLoader loadAccessToken,
  }) : _apiClient = apiClient,
       _loadAccessToken = loadAccessToken;

  final ApiClient _apiClient;
  final AuthenticatedAccessTokenLoader _loadAccessToken;

  static final RegExp _scopeTokenPattern = RegExp(
    r'^[a-z0-9][a-z0-9_-]{0,79}$',
  );

  Future<EmergencyAccessGrant> requestAccess({
    required int patientId,
    required String reason,
    required String purpose,
    required List<String> scope,
  }) async {
    if (patientId <= 0) {
      throw ArgumentError.value(patientId, 'patientId', 'Must be positive.');
    }

    final normalizedReason = _requiredText(
      reason,
      field: 'reason',
      maxLength: 500,
    );
    final normalizedPurpose = _requiredText(
      purpose,
      field: 'purpose',
      maxLength: 255,
    );
    final normalizedScope = _normalizeScope(scope);

    final bearerToken = await _loadAccessToken();

    final envelope = await _apiClient.postJson(
      'api/v1/emergency-access/requests',
      body: <String, dynamic>{
        'patient_id': patientId,
        'reason': normalizedReason,
        'purpose': normalizedPurpose,
        'scope': normalizedScope,
      },
      bearerToken: bearerToken,
    );

    return EmergencyAccessGrant.fromJson(_dataFromEnvelope(envelope));
  }

  Future<EmergencyAccessGrant> fetchRequest({
    required int emergencyAccessId,
  }) async {
    _validateEmergencyAccessId(emergencyAccessId);

    final bearerToken = await _loadAccessToken();

    final envelope = await _apiClient.getJson(
      'api/v1/emergency-access/requests/$emergencyAccessId',
      bearerToken: bearerToken,
    );

    return EmergencyAccessGrant.fromJson(_dataFromEnvelope(envelope));
  }

  Future<EmergencyAccessGrant> grantRequest({
    required int emergencyAccessId,
    required int durationMinutes,
    String? reviewNotes,
  }) async {
    _validateEmergencyAccessId(emergencyAccessId);
    _validateDuration(durationMinutes);

    final normalizedNotes = _optionalText(
      reviewNotes,
      field: 'reviewNotes',
      maxLength: 1000,
    );

    final bearerToken = await _loadAccessToken();

    final envelope = await _apiClient.postJson(
      'api/v1/emergency-access/requests/$emergencyAccessId/grant',
      body: <String, dynamic>{
        'duration_minutes': durationMinutes,
        'review_notes': normalizedNotes,
      },
      bearerToken: bearerToken,
    );

    return EmergencyAccessGrant.fromJson(_dataFromEnvelope(envelope));
  }

  Future<EmergencyAccessGrant> denyRequest({
    required int emergencyAccessId,
    required int durationMinutes,
    required String reviewNotes,
  }) async {
    _validateEmergencyAccessId(emergencyAccessId);
    _validateDuration(durationMinutes);

    final normalizedNotes = _requiredText(
      reviewNotes,
      field: 'reviewNotes',
      maxLength: 1000,
    );

    final bearerToken = await _loadAccessToken();

    final envelope = await _apiClient.postJson(
      'api/v1/emergency-access/requests/$emergencyAccessId/deny',
      body: <String, dynamic>{
        'duration_minutes': durationMinutes,
        'review_notes': normalizedNotes,
      },
      bearerToken: bearerToken,
    );

    return EmergencyAccessGrant.fromJson(_dataFromEnvelope(envelope));
  }

  Future<EmergencyAccessGrant> revokeRequest({
    required int emergencyAccessId,
    required String reason,
  }) async {
    _validateEmergencyAccessId(emergencyAccessId);

    final normalizedReason = _requiredText(
      reason,
      field: 'reason',
      maxLength: 500,
    );

    final bearerToken = await _loadAccessToken();

    final envelope = await _apiClient.postJson(
      'api/v1/emergency-access/requests/$emergencyAccessId/revoke',
      body: <String, dynamic>{'reason': normalizedReason},
      bearerToken: bearerToken,
    );

    return EmergencyAccessGrant.fromJson(_dataFromEnvelope(envelope));
  }

  static void _validateEmergencyAccessId(int value) {
    if (value <= 0) {
      throw ArgumentError.value(
        value,
        'emergencyAccessId',
        'Must be positive.',
      );
    }
  }

  static void _validateDuration(int value) {
    if (value < 1 || value > 60) {
      throw ArgumentError.value(
        value,
        'durationMinutes',
        'Must be between 1 and 60.',
      );
    }
  }

  static String _requiredText(
    String value, {
    required String field,
    required int maxLength,
  }) {
    final normalized = value.trim();

    if (normalized.isEmpty || normalized.length > maxLength) {
      throw ArgumentError.value(
        value,
        field,
        'Must contain between 1 and $maxLength characters.',
      );
    }

    return normalized;
  }

  static String? _optionalText(
    String? value, {
    required String field,
    required int maxLength,
  }) {
    if (value == null) return null;

    final normalized = value.trim();
    if (normalized.isEmpty) return null;

    if (normalized.length > maxLength) {
      throw ArgumentError.value(
        value,
        field,
        'Must be at most $maxLength characters.',
      );
    }

    return normalized;
  }

  static List<String> _normalizeScope(List<String> scope) {
    if (scope.isEmpty || scope.length > 50) {
      throw ArgumentError.value(
        scope,
        'scope',
        'Must contain between 1 and 50 items.',
      );
    }

    final normalized = <String>[];

    for (final rawItem in scope) {
      final token = rawItem.trim().toLowerCase();

      if (token.isEmpty) {
        throw ArgumentError.value(
          rawItem,
          'scope',
          'Scope items cannot be empty.',
        );
      }

      if (token.length > 150) {
        throw ArgumentError.value(
          rawItem,
          'scope',
          'Scope items must be at most 150 characters.',
        );
      }

      final parts = token.split(':');
      if (parts.length != 2 && parts.length != 3) {
        throw ArgumentError.value(
          rawItem,
          'scope',
          "Use 'resource:action' or 'resource:resource_id:action'.",
        );
      }

      final resourceType = parts[0];

      if (resourceType == '*' || !_scopeTokenPattern.hasMatch(resourceType)) {
        throw ArgumentError.value(
          rawItem,
          'scope',
          'Invalid emergency-access resource type.',
        );
      }

      if (parts.length == 2) {
        final action = parts[1];

        if (action.isEmpty ||
            (action != '*' && !_scopeTokenPattern.hasMatch(action))) {
          throw ArgumentError.value(
            rawItem,
            'scope',
            'Invalid emergency-access action.',
          );
        }
      } else {
        final resourceId = int.tryParse(parts[1]);
        if (resourceId == null || resourceId <= 0) {
          throw ArgumentError.value(
            rawItem,
            'scope',
            'Scope resource ID must be a positive integer.',
          );
        }

        final action = parts[2];
        if (action != '*' && !_scopeTokenPattern.hasMatch(action)) {
          throw ArgumentError.value(
            rawItem,
            'scope',
            'Invalid emergency-access action.',
          );
        }
      }

      if (!normalized.contains(token)) {
        normalized.add(token);
      }
    }

    return List<String>.unmodifiable(normalized);
  }

  static Map<String, dynamic> _dataFromEnvelope(Map<String, dynamic> envelope) {
    if (envelope['success'] != true) {
      throw const FormatException(
        'Emergency access API response did not indicate success.',
      );
    }

    final data = envelope['data'];
    if (data is! Map<String, dynamic>) {
      throw const FormatException(
        'Emergency access API response data must be a JSON object.',
      );
    }

    return data;
  }
}
