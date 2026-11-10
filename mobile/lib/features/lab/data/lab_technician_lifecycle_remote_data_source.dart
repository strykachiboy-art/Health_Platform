import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/models/lab_order_read.dart';

// S05A: technician-only, online-only, one-shot specimen transition.
// No automatic replay or offline clinical queue. POSTs have no idempotency contract.
enum LabTechnicianAction { collect, linkEquipment, process }

class LabTechnicianScopeUnavailable implements Exception {
  const LabTechnicianScopeUnavailable();
}

class LabTechnicianEvidenceChanged implements Exception {
  const LabTechnicianEvidenceChanged();
}

class LabTechnicianOutcomeUnconfirmed implements Exception {
  const LabTechnicianOutcomeUnconfirmed();
}

class LabTechnicianAlreadyAttempted implements Exception {
  const LabTechnicianAlreadyAttempted();
}

class LabTechnicianOrderEvidence {
  const LabTechnicianOrderEvidence({
    required this.order,
    required this.qrCode,
    required this.collectedById,
    required this.collectedAt,
    required this.processedById,
    required this.processedAt,
    required this.equipmentReference,
  });

  final LabPatientOrder order;
  final String qrCode;
  final int? collectedById;
  final String? collectedAt;
  final int? processedById;
  final String? processedAt;
  final String? equipmentReference;

  static int? _optionalId(Map<String, dynamic> raw, String field) {
    final value = raw[field];
    if (value == null) {
      return null;
    }
    if (value is! int || value <= 0 || value > 2147483647) {
      throw FormatException('Invalid laboratory field: $field');
    }
    return value;
  }

  static String? _optionalString(Map<String, dynamic> raw, String field) {
    final value = raw[field];
    if (value == null) {
      return null;
    }
    if (value is! String || value.trim().isEmpty || value != value.trim()) {
      throw FormatException('Invalid laboratory field: $field');
    }
    return value;
  }

  factory LabTechnicianOrderEvidence.fromEnvelope(
    Map<String, dynamic> raw, {
    required int clinicId,
    required int patientId,
    required int orderId,
  }) {
    final data = raw['data'];
    if (raw['success'] != true || data is! Map<String, dynamic>) {
      throw const FormatException('Invalid laboratory order response.');
    }
    final order = LabPatientOrder.fromJson(
      data,
      expectedClinicId: clinicId,
      expectedPatientId: patientId,
      expectedId: orderId,
    );
    final qrCode = _optionalString(data, 'qr_code');
    if (qrCode == null) {
      throw const FormatException('Laboratory order QR is missing.');
    }
    final collectedById = _optionalId(data, 'collected_by_id');
    final collectedAt = _optionalString(data, 'sample_collected_at');
    final processedById = _optionalId(data, 'processed_by_id');
    final processedAt = _optionalString(data, 'processed_at');
    if ((collectedById == null) != (collectedAt == null) ||
        (processedById == null) != (processedAt == null)) {
      throw const FormatException('Inconsistent lab actor timestamps.');
    }
    return LabTechnicianOrderEvidence(
      order: order,
      qrCode: qrCode,
      collectedById: collectedById,
      collectedAt: collectedAt,
      processedById: processedById,
      processedAt: processedAt,
      equipmentReference: _optionalString(data, 'equipment_reference_id'),
    );
  }

  bool sameSnapshot(LabTechnicianOrderEvidence other) =>
      order.id == other.order.id &&
      order.clinicId == other.order.clinicId &&
      order.patientId == other.order.patientId &&
      order.status == other.order.status &&
      qrCode == other.qrCode &&
      collectedById == other.collectedById &&
      collectedAt == other.collectedAt &&
      processedById == other.processedById &&
      processedAt == other.processedAt &&
      equipmentReference == other.equipmentReference;
}

class LabTechnicianReview {
  const LabTechnicianReview({
    required this.evidence,
    required this.action,
    required this.qrInput,
    required this.equipmentInput,
  });

  final LabTechnicianOrderEvidence evidence;
  final LabTechnicianAction action;
  final String? qrInput;
  final String? equipmentInput;
}

class LabTechnicianLifecycleRemoteDataSource {
  LabTechnicianLifecycleRemoteDataSource({required this.reader});

  final LabOrderReadRemoteDataSource reader;
  bool _attempted = false;
  LabTechnicianReview? _review;
  AuthTokenResponse? _reviewSession;
  int? _reviewRevision;

  bool get writeAttempted => _attempted;

  static void _id(int value) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, 'id', 'Expected positive ID.');
    }
  }

  static bool _sameSession(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  bool _valid(int revision) =>
      reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      reader.sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _technician(int revision) async {
    if (!_valid(revision)) {
      throw const LabTechnicianScopeUnavailable();
    }
    try {
      final state = await reader.loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.trim().isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          session.role.trim().toLowerCase() != 'lab_technician' ||
          !_valid(revision)) {
        throw const LabTechnicianScopeUnavailable();
      }
      return session;
    } on LabTechnicianScopeUnavailable {
      rethrow;
    } on Object {
      throw const LabTechnicianScopeUnavailable();
    }
  }

  Future<void> _verify(AuthTokenResponse original, int revision) async {
    final current = await _technician(revision);
    if (!_sameSession(original, current) || !_valid(revision)) {
      throw const LabTechnicianScopeUnavailable();
    }
  }

  Future<LabTechnicianOrderEvidence> _getEvidence({
    required AuthTokenResponse session,
    required int revision,
    required int patientId,
    required int orderId,
  }) async {
    await _verify(session, revision);
    final raw = await reader.apiClient.getJson(
      'api/v1/lab/orders/$orderId',
      bearerToken: session.accessToken,
    );
    await _verify(session, revision);
    return LabTechnicianOrderEvidence.fromEnvelope(
      raw,
      clinicId: session.clinicContextId!,
      patientId: patientId,
      orderId: orderId,
    );
  }

  static String? _optionalReference(String? value) {
    if (value == null || value.trim().isEmpty) {
      return null;
    }
    final candidate = value.trim();
    if (candidate.length > 150) {
      throw const FormatException('Equipment reference too long.');
    }
    return candidate;
  }

  static void _authorize(LabTechnicianReview review) {
    final evidence = review.evidence;
    switch (review.action) {
      case LabTechnicianAction.collect:
        if (evidence.order.status != LabOrderStatus.ordered ||
            evidence.collectedById != null ||
            evidence.collectedAt != null ||
            review.qrInput != evidence.qrCode) {
          throw const LabTechnicianEvidenceChanged();
        }
        return;
      case LabTechnicianAction.linkEquipment:
        if (evidence.order.status != LabOrderStatus.sampleCollected ||
            evidence.collectedById == null ||
            evidence.collectedAt == null ||
            evidence.processedById != null ||
            evidence.processedAt != null ||
            review.equipmentInput == null) {
          throw const LabTechnicianEvidenceChanged();
        }
        return;
      case LabTechnicianAction.process:
        if (!const <LabOrderStatus>{
              LabOrderStatus.sampleCollected,
              LabOrderStatus.inProgress,
            }.contains(evidence.order.status) ||
            evidence.collectedById == null ||
            evidence.collectedAt == null ||
            evidence.processedById != null ||
            evidence.processedAt != null) {
          throw const LabTechnicianEvidenceChanged();
        }
        return;
    }
  }

  Future<LabTechnicianReview> review({
    required int patientId,
    required int orderId,
    required LabTechnicianAction action,
    String? scannedQrCode,
    String? equipmentReference,
  }) async {
    if (_attempted) {
      throw const LabTechnicianAlreadyAttempted();
    }
    _id(patientId);
    _id(orderId);
    final qrInput = scannedQrCode?.trim();
    if (action == LabTechnicianAction.collect &&
        (qrInput == null || qrInput.isEmpty)) {
      throw const FormatException('A matching order QR is required.');
    }
    final equipmentInput = _optionalReference(equipmentReference);
    if (action == LabTechnicianAction.linkEquipment && equipmentInput == null) {
      throw const FormatException('Equipment reference is required.');
    }
    final revision = reader.sessionNavigationGuard.revision;
    final session = await _technician(revision);
    final evidence = await _getEvidence(
      session: session,
      revision: revision,
      patientId: patientId,
      orderId: orderId,
    );
    final proposed = LabTechnicianReview(
      evidence: evidence,
      action: action,
      qrInput: qrInput,
      equipmentInput: equipmentInput,
    );
    _authorize(proposed);
    await _verify(session, revision);
    _review = proposed;
    _reviewRevision = revision;
    _reviewSession = session;
    return proposed;
  }

  Future<LabTechnicianOrderEvidence> submitOnce({
    required LabTechnicianReview reviewed,
  }) async {
    if (_attempted) {
      throw const LabTechnicianAlreadyAttempted();
    }
    final revision = reader.sessionNavigationGuard.revision;
    final original = _reviewSession;
    if (!identical(reviewed, _review) ||
        original == null ||
        _reviewRevision != revision) {
      throw const LabTechnicianEvidenceChanged();
    }
    await _verify(original, revision);
    final expected = reviewed.evidence;
    final fresh = await _getEvidence(
      session: original,
      revision: revision,
      patientId: expected.order.patientId,
      orderId: expected.order.id,
    );
    if (!expected.sameSnapshot(fresh)) {
      throw const LabTechnicianEvidenceChanged();
    }
    _authorize(reviewed);
    await _verify(original, revision);
    final path = 'api/v1/lab/orders/${expected.order.id}';
    final String endpoint;
    final Map<String, dynamic> body;
    switch (reviewed.action) {
      case LabTechnicianAction.collect:
        endpoint = '$path/collect-sample';
        body = <String, dynamic>{'scanned_qr_code': reviewed.qrInput};
        break;
      case LabTechnicianAction.linkEquipment:
        endpoint = '$path/equipment';
        body = <String, dynamic>{
          'equipment_reference_id': reviewed.equipmentInput,
        };
        break;
      case LabTechnicianAction.process:
        endpoint = '$path/process';
        body = <String, dynamic>{};
        if (reviewed.equipmentInput != null) {
          body['equipment_reference_id'] = reviewed.equipmentInput;
        }
        break;
    }
    // From this point forward the clinical outcome may be uncertain.
    _attempted = true;
    try {
      final posted = await reader.apiClient.postJson(
        endpoint,
        body: body,
        bearerToken: original.accessToken,
      );
      await _verify(original, revision);
      final receipt = LabTechnicianOrderEvidence.fromEnvelope(
        posted,
        clinicId: original.clinicContextId!,
        patientId: expected.order.patientId,
        orderId: expected.order.id,
      );
      final authoritative = await _getEvidence(
        session: original,
        revision: revision,
        patientId: expected.order.patientId,
        orderId: expected.order.id,
      );
      if (!receipt.sameSnapshot(authoritative) ||
          !_confirmed(reviewed, authoritative)) {
        throw const LabTechnicianOutcomeUnconfirmed();
      }
      return authoritative;
    } on Object {
      throw const LabTechnicianOutcomeUnconfirmed();
    }
  }

  static bool _confirmed(
    LabTechnicianReview review,
    LabTechnicianOrderEvidence current,
  ) {
    switch (review.action) {
      case LabTechnicianAction.collect:
        return current.order.status == LabOrderStatus.sampleCollected &&
            current.collectedById != null &&
            current.collectedAt != null &&
            current.processedById == null;
      case LabTechnicianAction.linkEquipment:
        return current.order.status == LabOrderStatus.inProgress &&
            current.equipmentReference == review.equipmentInput &&
            current.processedById == null;
      case LabTechnicianAction.process:
        return current.order.status == LabOrderStatus.inProgress &&
            current.processedById != null &&
            current.processedAt != null &&
            (review.equipmentInput == null ||
                current.equipmentReference == review.equipmentInput);
    }
  }
}
