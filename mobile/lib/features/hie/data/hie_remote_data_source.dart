import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/hie/data/models/hie_models.dart';

class HieSessionException implements Exception {
  const HieSessionException();

  @override
  String toString() => 'HIE / FHIR access unavailable for this session.';
}

/// Online-only HIE / FHIR adapter. Never persists clinical FHIR payloads or
/// PHI locally; every call validates active clinic-scoped auth before and
/// after network transit.
class HieRemoteDataSource {
  const HieRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const Set<String> viewRoles = <String>{
    'admin',
    'doctor',
    'nurse',
    'lab_technician',
    'pharmacist',
  };

  static const Set<String> adminRoles = <String>{'admin'};

  bool _active(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _authorized({required Set<String> allowedRoles}) async {
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.trim().isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          !allowedRoles.contains(session.role.trim().toLowerCase())) {
        throw const HieSessionException();
      }
      return session;
    } on HieSessionException {
      rethrow;
    } on Object {
      throw const HieSessionException();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> authorizedSession() async {
    final revision = sessionNavigationGuard.revision;
    if (!_active(revision)) {
      throw const HieSessionException();
    }
    final session = await _authorized(allowedRoles: viewRoles);
    if (!_active(revision)) {
      throw const HieSessionException();
    }
    return session;
  }

  Map<String, dynamic> _unwrapEnvelopeData(Map<String, dynamic> response) {
    if (response['success'] != true || response['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid HIE response envelope.');
    }
    return response['data'] as Map<String, dynamic>;
  }

  Future<HieIntegration> createIntegration({
    required String provider,
    String? endpointUrl,
    String? organizationId,
    String? facilityId,
  }) async {
    final normalizedProvider = provider.trim().toLowerCase();
    if (normalizedProvider.isEmpty || normalizedProvider.length > 50) {
      throw ArgumentError.value(provider, 'provider');
    }

    final normalizedEndpoint = endpointUrl?.trim();
    if (normalizedEndpoint != null && normalizedEndpoint.isEmpty) {
      throw ArgumentError.value(endpointUrl, 'endpointUrl');
    }

    final normalizedOrg = organizationId?.trim();
    if (normalizedOrg != null && normalizedOrg.isEmpty) {
      throw ArgumentError.value(organizationId, 'organizationId');
    }

    final normalizedFacility = facilityId?.trim();
    if (normalizedFacility != null && normalizedFacility.isEmpty) {
      throw ArgumentError.value(facilityId, 'facilityId');
    }

    final revision = sessionNavigationGuard.revision;
    if (!_active(revision)) throw const HieSessionException();
    final before = await _authorized(allowedRoles: adminRoles);
    if (!_active(revision)) throw const HieSessionException();

    final body = <String, dynamic>{
      'provider': normalizedProvider,
      'endpoint_url': ?normalizedEndpoint,
      'organization_id': ?normalizedOrg,
      'facility_id': ?normalizedFacility,
    };

    final response = await apiClient.postJson(
      'api/v1/hie/integrations',
      body: body,
      bearerToken: before.accessToken,
    );

    if (!_active(revision)) throw const HieSessionException();
    final after = await _authorized(allowedRoles: adminRoles);
    if (!_active(revision) || !_same(before, after)) {
      throw const HieSessionException();
    }

    return HieIntegration.fromJson(
      _unwrapEnvelopeData(response),
      expectedClinicId: before.clinicContextId!,
    );
  }

  Future<HieIntegration> fetchIntegration({required int integrationId}) async {
    if (integrationId <= 0 || integrationId > 2147483647) {
      throw ArgumentError.value(integrationId, 'integrationId');
    }

    final revision = sessionNavigationGuard.revision;
    if (!_active(revision)) throw const HieSessionException();
    final before = await _authorized(allowedRoles: viewRoles);
    if (!_active(revision)) throw const HieSessionException();

    final response = await apiClient.getJson(
      'api/v1/hie/integrations/$integrationId',
      bearerToken: before.accessToken,
    );

    if (!_active(revision)) throw const HieSessionException();
    final after = await _authorized(allowedRoles: viewRoles);
    if (!_active(revision) || !_same(before, after)) {
      throw const HieSessionException();
    }

    return HieIntegration.fromJson(
      _unwrapEnvelopeData(response),
      expectedClinicId: before.clinicContextId!,
    );
  }

  Future<HieIntegration> updateIntegration({
    required int integrationId,
    String? provider,
    String? status,
    String? endpointUrl,
    String? organizationId,
    String? facilityId,
  }) async {
    if (integrationId <= 0 || integrationId > 2147483647) {
      throw ArgumentError.value(integrationId, 'integrationId');
    }
    if (status != null && !hieIntegrationStatuses.contains(status.trim().toLowerCase())) {
      throw ArgumentError.value(status, 'status');
    }

    final normalizedProvider = provider?.trim().toLowerCase();
    if (normalizedProvider != null && normalizedProvider.isEmpty) {
      throw ArgumentError.value(provider, 'provider');
    }
    final normalizedEndpoint = endpointUrl?.trim();
    if (normalizedEndpoint != null && normalizedEndpoint.isEmpty) {
      throw ArgumentError.value(endpointUrl, 'endpointUrl');
    }
    final normalizedOrg = organizationId?.trim();
    if (normalizedOrg != null && normalizedOrg.isEmpty) {
      throw ArgumentError.value(organizationId, 'organizationId');
    }
    final normalizedFacility = facilityId?.trim();
    if (normalizedFacility != null && normalizedFacility.isEmpty) {
      throw ArgumentError.value(facilityId, 'facilityId');
    }

    final revision = sessionNavigationGuard.revision;
    if (!_active(revision)) throw const HieSessionException();
    final before = await _authorized(allowedRoles: adminRoles);
    if (!_active(revision)) throw const HieSessionException();

    final body = <String, dynamic>{
      'provider': ?normalizedProvider,
      if (status != null) 'status': status.trim().toLowerCase(),
      'endpoint_url': ?normalizedEndpoint,
      'organization_id': ?normalizedOrg,
      'facility_id': ?normalizedFacility,
    };

    final response = await apiClient.patchJson(
      'api/v1/hie/integrations/$integrationId',
      body: body,
      bearerToken: before.accessToken,
    );

    if (!_active(revision)) throw const HieSessionException();
    final after = await _authorized(allowedRoles: adminRoles);
    if (!_active(revision) || !_same(before, after)) {
      throw const HieSessionException();
    }

    return HieIntegration.fromJson(
      _unwrapEnvelopeData(response),
      expectedClinicId: before.clinicContextId!,
    );
  }

  Future<HieQueryResult> queryPatient({
    required String patientIdentifier,
    String purposeOfUse = 'treatment',
    int? integrationId,
  }) async {
    final normalizedId = patientIdentifier.trim();
    if (normalizedId.isEmpty || normalizedId.length > 255) {
      throw ArgumentError.value(patientIdentifier, 'patientIdentifier');
    }
    final normalizedPurpose = purposeOfUse.trim().toLowerCase();
    if (!hiePurposesOfUse.contains(normalizedPurpose)) {
      throw ArgumentError.value(purposeOfUse, 'purposeOfUse');
    }
    if (integrationId != null &&
        (integrationId <= 0 || integrationId > 2147483647)) {
      throw ArgumentError.value(integrationId, 'integrationId');
    }

    final revision = sessionNavigationGuard.revision;
    if (!_active(revision)) throw const HieSessionException();
    final before = await _authorized(allowedRoles: viewRoles);
    if (!_active(revision)) throw const HieSessionException();

    final response = await apiClient.postJson(
      'api/v1/hie/queries/patient',
      body: <String, dynamic>{
        'patient_identifier': normalizedId,
        'purpose_of_use': normalizedPurpose,
        'integration_id': ?integrationId,
      },
      bearerToken: before.accessToken,
    );

    if (!_active(revision)) throw const HieSessionException();
    final after = await _authorized(allowedRoles: viewRoles);
    if (!_active(revision) || !_same(before, after)) {
      throw const HieSessionException();
    }

    return HieQueryResult.fromJson(_unwrapEnvelopeData(response));
  }

  Future<HieQueryResult> queryClinicalData({
    required String patientIdentifier,
    String purposeOfUse = 'treatment',
    String resourceType = 'Observation',
    Map<String, String>? searchParams,
    int? integrationId,
  }) async {
    final normalizedId = patientIdentifier.trim();
    if (normalizedId.isEmpty || normalizedId.length > 255) {
      throw ArgumentError.value(patientIdentifier, 'patientIdentifier');
    }
    final normalizedPurpose = purposeOfUse.trim().toLowerCase();
    if (!hiePurposesOfUse.contains(normalizedPurpose)) {
      throw ArgumentError.value(purposeOfUse, 'purposeOfUse');
    }
    if (!fhirReadOnlyResourceTypes.contains(resourceType)) {
      throw ArgumentError.value(resourceType, 'resourceType');
    }
    if (integrationId != null &&
        (integrationId <= 0 || integrationId > 2147483647)) {
      throw ArgumentError.value(integrationId, 'integrationId');
    }

    final revision = sessionNavigationGuard.revision;
    if (!_active(revision)) throw const HieSessionException();
    final before = await _authorized(allowedRoles: viewRoles);
    if (!_active(revision)) throw const HieSessionException();

    final filters = <String, dynamic>{
      'resource_type': resourceType,
      if (searchParams != null && searchParams.isNotEmpty) 'params': searchParams,
    };

    final response = await apiClient.postJson(
      'api/v1/hie/queries/clinical-data',
      body: <String, dynamic>{
        'patient_identifier': normalizedId,
        'purpose_of_use': normalizedPurpose,
        'filters': filters,
        'integration_id': ?integrationId,
      },
      bearerToken: before.accessToken,
    );

    if (!_active(revision)) throw const HieSessionException();
    final after = await _authorized(allowedRoles: viewRoles);
    if (!_active(revision) || !_same(before, after)) {
      throw const HieSessionException();
    }

    return HieQueryResult.fromJson(_unwrapEnvelopeData(response));
  }

  Future<HieSubmissionPage> fetchSubmissions({
    int? integrationId,
    int? patientId,
    String? status,
    int page = 1,
    int perPage = 20,
  }) async {
    if (integrationId != null &&
        (integrationId <= 0 || integrationId > 2147483647)) {
      throw ArgumentError.value(integrationId, 'integrationId');
    }
    if (patientId != null && (patientId <= 0 || patientId > 2147483647)) {
      throw ArgumentError.value(patientId, 'patientId');
    }
    if (page < 1) throw ArgumentError.value(page, 'page');
    if (perPage < 1 || perPage > 100) {
      throw ArgumentError.value(perPage, 'perPage');
    }

    final revision = sessionNavigationGuard.revision;
    if (!_active(revision)) throw const HieSessionException();
    final before = await _authorized(allowedRoles: viewRoles);
    if (!_active(revision)) throw const HieSessionException();

    final path = Uri(
      path: 'api/v1/hie/submissions',
      queryParameters: <String, String>{
        if (integrationId != null) 'integration_id': '$integrationId',
        if (patientId != null) 'patient_id': '$patientId',
        if (status != null && status.trim().isNotEmpty) 'status': status.trim(),
        'page': '$page',
        'per_page': '$perPage',
      },
    ).toString();

    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );

    if (!_active(revision)) throw const HieSessionException();
    final after = await _authorized(allowedRoles: viewRoles);
    if (!_active(revision) || !_same(before, after)) {
      throw const HieSessionException();
    }

    return HieSubmissionPage.fromJson(
      _unwrapEnvelopeData(response),
      expectedClinicId: before.clinicContextId!,
    );
  }

  /// Fetches an admin-only FHIR R4 `collection` Bundle from
  /// `/api/v1/hie/fhir/export/patient/<patientId>` (or a resource-specific
  /// sub-endpoint when [section] is one of `patient`, `encounters`,
  /// `observations`, `allergies`, `medications`).
  Future<FhirExportBundle> exportPatientFhirBundle({
    required int patientId,
    String section = 'full',
  }) async {
    if (patientId <= 0 || patientId > 2147483647) {
      throw ArgumentError.value(patientId, 'patientId');
    }
    if (!fhirExportSections.contains(section)) {
      throw ArgumentError.value(section, 'section');
    }

    final revision = sessionNavigationGuard.revision;
    if (!_active(revision)) throw const HieSessionException();
    final before = await _authorized(allowedRoles: adminRoles);
    if (!_active(revision)) throw const HieSessionException();

    final suffix = section == 'full' ? '' : '/$section';
    final path = 'api/v1/hie/fhir/export/patient/$patientId$suffix';

    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );

    if (!_active(revision)) throw const HieSessionException();
    final after = await _authorized(allowedRoles: adminRoles);
    if (!_active(revision) || !_same(before, after)) {
      throw const HieSessionException();
    }

    return FhirExportBundle.fromJson(response);
  }
}
