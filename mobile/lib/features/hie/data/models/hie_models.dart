import 'dart:collection';

/// Bounded FHIR R4 resource types supported by the backend's HIE contract.
const List<String> fhirReadOnlyResourceTypes = <String>[
  'Patient',
  'Encounter',
  'Condition',
  'AllergyIntolerance',
  'MedicationRequest',
  'Observation',
  'DiagnosticReport',
];

/// Valid `HIEPurposeOfUse` enum values enforced by the backend.
const List<String> hiePurposesOfUse = <String>[
  'treatment',
  'payment',
  'healthcare_operations',
  'public_health',
  'individual_access',
];

/// Valid `HIEIntegrationStatus` enum values enforced by the backend.
const List<String> hieIntegrationStatuses = <String>[
  'pending',
  'active',
  'suspended',
  'disabled',
];

/// Valid export scopes for `/api/v1/hie/fhir/export/patient/<id>`.
const List<String> fhirExportSections = <String>[
  'full',
  'patient',
  'encounters',
  'observations',
  'allergies',
  'medications',
];

int _requirePositiveInt(Object? value, String fieldName) {
  if (value is! int || value <= 0) {
    throw FormatException('Invalid $fieldName.');
  }
  return value;
}

int _requireNonNegativeInt(Object? value, String fieldName) {
  if (value is! int || value < 0) {
    throw FormatException('Invalid $fieldName.');
  }
  return value;
}

String _requireNonEmptyString(Object? value, String fieldName) {
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('Invalid $fieldName.');
  }
  return value.trim();
}

String? _optionalString(Object? value, String fieldName) {
  if (value == null) return null;
  if (value is! String) {
    throw FormatException('Invalid $fieldName.');
  }
  final trimmed = value.trim();
  return trimmed.isEmpty ? null : trimmed;
}

DateTime _requireDateTime(Object? value, String fieldName) {
  if (value is! String || value.trim().isEmpty) {
    throw FormatException('Invalid $fieldName.');
  }
  final parsed = DateTime.tryParse(value.trim());
  if (parsed == null) {
    throw FormatException('Invalid $fieldName.');
  }
  return parsed;
}

DateTime? _optionalDateTime(Object? value, String fieldName) {
  if (value == null) return null;
  return _requireDateTime(value, fieldName);
}

class HieIntegration {
  HieIntegration({
    required this.id,
    required this.clinicId,
    required this.provider,
    required this.status,
    required this.createdAt,
    required this.updatedAt,
    this.endpointUrl,
    this.organizationId,
    this.facilityId,
    this.lastSyncAt,
  });

  factory HieIntegration.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
  }) {
    final clinicId = _requirePositiveInt(json['clinic_id'], 'clinic_id');
    if (clinicId != expectedClinicId) {
      throw const FormatException('HIE integration clinic mismatch.');
    }

    final status = _requireNonEmptyString(json['status'], 'status')
        .toLowerCase();
    if (!hieIntegrationStatuses.contains(status)) {
      throw const FormatException('Invalid HIE integration status.');
    }

    return HieIntegration(
      id: _requirePositiveInt(json['id'], 'id'),
      clinicId: clinicId,
      provider: _requireNonEmptyString(json['provider'], 'provider'),
      status: status,
      endpointUrl: _optionalString(json['endpoint_url'], 'endpoint_url'),
      organizationId: _optionalString(
        json['organization_id'],
        'organization_id',
      ),
      facilityId: _optionalString(json['facility_id'], 'facility_id'),
      lastSyncAt: _optionalDateTime(json['last_sync_at'], 'last_sync_at'),
      createdAt: _requireDateTime(json['created_at'], 'created_at'),
      updatedAt: _requireDateTime(json['updated_at'], 'updated_at'),
    );
  }

  final int id;
  final int clinicId;
  final String provider;
  final String status;
  final String? endpointUrl;
  final String? organizationId;
  final String? facilityId;
  final DateTime? lastSyncAt;
  final DateTime createdAt;
  final DateTime updatedAt;
}

class HieSubmission {
  HieSubmission({
    required this.id,
    required this.integrationId,
    required this.clinicId,
    required this.operation,
    required this.status,
    required this.retryCount,
    required this.createdAt,
    required this.updatedAt,
    this.patientId,
    this.failureClass,
    this.externalReference,
    this.statusCode,
    this.errorMessage,
    this.submittedAt,
  });

  factory HieSubmission.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
  }) {
    final clinicId = _requirePositiveInt(json['clinic_id'], 'clinic_id');
    if (clinicId != expectedClinicId) {
      throw const FormatException('HIE submission clinic mismatch.');
    }

    final patientIdRaw = json['patient_id'];
    final int? patientId = patientIdRaw == null
        ? null
        : _requirePositiveInt(patientIdRaw, 'patient_id');

    final statusCodeRaw = json['status_code'];
    final int? statusCode = statusCodeRaw == null
        ? null
        : _requireNonNegativeInt(statusCodeRaw, 'status_code');

    return HieSubmission(
      id: _requirePositiveInt(json['id'], 'id'),
      integrationId: _requirePositiveInt(
        json['integration_id'],
        'integration_id',
      ),
      clinicId: clinicId,
      patientId: patientId,
      operation: _requireNonEmptyString(json['operation'], 'operation'),
      status: _requireNonEmptyString(json['status'], 'status'),
      failureClass: _optionalString(json['failure_class'], 'failure_class'),
      externalReference: _optionalString(
        json['external_reference'],
        'external_reference',
      ),
      statusCode: statusCode,
      errorMessage: _optionalString(json['error_message'], 'error_message'),
      retryCount: _requireNonNegativeInt(json['retry_count'], 'retry_count'),
      submittedAt: _optionalDateTime(json['submitted_at'], 'submitted_at'),
      createdAt: _requireDateTime(json['created_at'], 'created_at'),
      updatedAt: _requireDateTime(json['updated_at'], 'updated_at'),
    );
  }

  final int id;
  final int integrationId;
  final int clinicId;
  final int? patientId;
  final String operation;
  final String status;
  final String? failureClass;
  final String? externalReference;
  final int? statusCode;
  final String? errorMessage;
  final int retryCount;
  final DateTime? submittedAt;
  final DateTime createdAt;
  final DateTime updatedAt;
}

class HieSubmissionPage {
  HieSubmissionPage({
    required List<HieSubmission> items,
    required this.total,
    required this.page,
    required this.perPage,
  }) : items = List<HieSubmission>.unmodifiable(items);

  factory HieSubmissionPage.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
  }) {
    final rawItems = json['items'];
    if (rawItems is! List) {
      throw const FormatException('Invalid HIE submissions list.');
    }

    final items = <HieSubmission>[];
    for (final raw in rawItems) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Invalid HIE submission item.');
      }
      items.add(
        HieSubmission.fromJson(raw, expectedClinicId: expectedClinicId),
      );
    }

    return HieSubmissionPage(
      items: items,
      total: _requireNonNegativeInt(json['total'], 'total'),
      page: _requirePositiveInt(json['page'], 'page'),
      perPage: _requirePositiveInt(json['per_page'], 'per_page'),
    );
  }

  final List<HieSubmission> items;
  final int total;
  final int page;
  final int perPage;
}

class HieQueryResult {
  HieQueryResult({
    required this.statusCode,
    required List<Map<String, dynamic>> records,
    this.externalReference,
  }) : records = List<Map<String, dynamic>>.unmodifiable(
         records.map(
           (item) => UnmodifiableMapView<String, dynamic>(
             Map<String, dynamic>.from(item),
           ),
         ),
       );

  factory HieQueryResult.fromJson(Map<String, dynamic> json) {
    final statusCode = _requirePositiveInt(json['status_code'], 'status_code');
    final externalReference = _optionalString(
      json['external_reference'],
      'external_reference',
    );
    final rawRecords = json['records'];
    final records = <Map<String, dynamic>>[];

    if (rawRecords != null) {
      if (rawRecords is! List) {
        throw const FormatException('Invalid HIE records list.');
      }
      for (final entry in rawRecords) {
        if (entry is! Map<String, dynamic>) {
          throw const FormatException('Invalid FHIR record entry.');
        }
        records.add(entry);
      }
    }

    return HieQueryResult(
      statusCode: statusCode,
      externalReference: externalReference,
      records: records,
    );
  }

  final int statusCode;
  final String? externalReference;
  final List<Map<String, dynamic>> records;
}

class FhirExportBundle {
  FhirExportBundle({
    required this.resourceType,
    required this.type,
    required this.total,
    required List<Map<String, dynamic>> resources,
    required Map<String, dynamic> rawBundle,
    this.id,
    this.timestamp,
  }) : resources = List<Map<String, dynamic>>.unmodifiable(
         resources.map(
           (item) => UnmodifiableMapView<String, dynamic>(
             Map<String, dynamic>.from(item),
           ),
         ),
       ),
       rawBundle = UnmodifiableMapView<String, dynamic>(
         Map<String, dynamic>.from(rawBundle),
       );

  factory FhirExportBundle.fromJson(Map<String, dynamic> json) {
    final resourceType = _requireNonEmptyString(
      json['resourceType'],
      'resourceType',
    );
    if (resourceType != 'Bundle') {
      throw const FormatException('Expected a FHIR Bundle resource.');
    }

    final bundleType = _requireNonEmptyString(json['type'], 'type');
    if (bundleType != 'collection') {
      throw const FormatException('Expected a FHIR collection Bundle.');
    }

    final rawEntries = json['entry'];
    if (rawEntries is! List) {
      throw const FormatException('FHIR Bundle.entry must be an array.');
    }

    final resources = <Map<String, dynamic>>[];
    for (final entry in rawEntries) {
      if (entry is! Map<String, dynamic>) {
        throw const FormatException('FHIR Bundle entry must be an object.');
      }
      final resource = entry['resource'];
      if (resource is! Map<String, dynamic>) {
        throw const FormatException('FHIR Bundle entry is missing resource.');
      }
      _requireNonEmptyString(resource['resourceType'], 'resource.resourceType');
      resources.add(resource);
    }

    final totalRaw = json['total'];
    final total = totalRaw == null
        ? resources.length
        : _requireNonNegativeInt(totalRaw, 'total');

    return FhirExportBundle(
      resourceType: resourceType,
      type: bundleType,
      total: total,
      id: _optionalString(json['id'], 'id'),
      timestamp: _optionalDateTime(json['timestamp'], 'timestamp'),
      resources: resources,
      rawBundle: json,
    );
  }

  final String resourceType;
  final String type;
  final int total;
  final String? id;
  final DateTime? timestamp;
  final List<Map<String, dynamic>> resources;
  final Map<String, dynamic> rawBundle;
}
