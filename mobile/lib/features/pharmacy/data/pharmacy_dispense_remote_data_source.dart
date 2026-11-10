// P23H.S03B — pharmacist-only, one-shot dispensing.
// The backend locks and validates remaining prescription quantity and stock.
// This adapter never queues, retries or locally subtracts inventory.
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_prescription.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_prescription_detail.dart';
import 'package:clinic_system_pro/features/pharmacy/data/models/pharmacy_stock.dart';
import 'package:clinic_system_pro/features/pharmacy/data/models/pharmacy_dispense.dart';
import 'package:clinic_system_pro/features/pharmacy/data/pharmacy_inventory_remote_data_source.dart';

class PharmacyDispenseScopeUnavailable implements Exception {
  const PharmacyDispenseScopeUnavailable();
}

class PharmacyDispenseEvidenceChanged implements Exception {
  const PharmacyDispenseEvidenceChanged();
}

class PharmacyDispenseOutcomeUnconfirmed implements Exception {
  const PharmacyDispenseOutcomeUnconfirmed();
}

class PharmacyDispenseAlreadyAttempted implements Exception {
  const PharmacyDispenseAlreadyAttempted();
}

class PharmacyDispenseReview {
  const PharmacyDispenseReview({
    required this.patientId,
    required this.prescriptionId,
    required this.prescriptionItemId,
    required this.drugId,
    required this.drugName,
    required this.batchId,
    required this.batchNumber,
    required this.quantity,
    required this.batchQuantity,
    required this.batchExpiry,
    required this.prescribedQuantity,
  });

  final int patientId;
  final int prescriptionId;
  final int prescriptionItemId;
  final int drugId;
  final String drugName;
  final int batchId;
  final String batchNumber;
  final int quantity;
  final int batchQuantity;
  final String batchExpiry;
  final int prescribedQuantity;

  bool sameServerEvidence(PharmacyDispenseReview other) =>
      patientId == other.patientId &&
      prescriptionId == other.prescriptionId &&
      prescriptionItemId == other.prescriptionItemId &&
      drugId == other.drugId &&
      drugName == other.drugName &&
      batchId == other.batchId &&
      batchNumber == other.batchNumber &&
      quantity == other.quantity &&
      batchQuantity == other.batchQuantity &&
      batchExpiry == other.batchExpiry &&
      prescribedQuantity == other.prescribedQuantity;
}

class PharmacyDispenseRemoteDataSource {
  PharmacyDispenseRemoteDataSource({required this.inventory});

  final PharmacyInventoryRemoteDataSource inventory;
  bool _writeAttempted = false;
  PharmacyDispenseReview? _review;

  bool get writeAttempted => _writeAttempted;

  bool _current(int revision) =>
      inventory.sessionNavigationGuard.canAccessProtectedRoutes &&
      inventory.sessionNavigationGuard.revision == revision;

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> _pharmacist(int revision) async {
    if (!_current(revision)) {
      throw const PharmacyDispenseScopeUnavailable();
    }
    try {
      final state = await inventory.loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.role.trim().toLowerCase() != 'pharmacist' ||
          session.accessToken.isEmpty ||
          session.accessToken != session.accessToken.trim() ||
          !_current(revision)) {
        throw const PharmacyDispenseScopeUnavailable();
      }
      return session;
    } on PharmacyDispenseScopeUnavailable {
      rethrow;
    } on Object {
      throw const PharmacyDispenseScopeUnavailable();
    }
  }

  Future<void> _verify(AuthTokenResponse original, int revision) async {
    final current = await _pharmacist(revision);
    if (!_same(original, current) || !_current(revision)) {
      throw const PharmacyDispenseScopeUnavailable();
    }
  }

  static void _id(int value, String name) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, name, 'Must be a positive ID.');
    }
  }

  Future<Map<String, dynamic>> _get(
    String path,
    AuthTokenResponse original,
    int revision,
  ) async {
    await _verify(original, revision);
    final result = await inventory.apiClient.getJson(
      path,
      bearerToken: original.accessToken,
    );
    await _verify(original, revision);
    return result;
  }

  static Map<String, dynamic> _data(Map<String, dynamic> result) {
    if (result['success'] != true || result['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid pharmacy server evidence.');
    }
    return result['data'] as Map<String, dynamic>;
  }

  static bool _expiryFuture(String isoDate) {
    final today = DateTime.now().toUtc();
    final isoToday =
        '${today.year.toString().padLeft(4, '0')}-'
        '${today.month.toString().padLeft(2, '0')}-'
        '${today.day.toString().padLeft(2, '0')}';
    return isoDate.compareTo(isoToday) > 0;
  }

  Future<PharmacyDispenseReview> _loadEvidence({
    required AuthTokenResponse original,
    required int revision,
    required int patientId,
    required int prescriptionId,
    required int prescriptionItemId,
    required int batchId,
    required int quantity,
  }) async {
    final prescriptionEnvelope = await _get(
      'api/v1/prescriptions/$prescriptionId',
      original,
      revision,
    );
    final prescriptionDetail = PatientPrescriptionDetail.fromEnvelope(
      prescriptionEnvelope,
      expectedClinicId: original.clinicContextId!,
      expectedPatientId: patientId,
      expectedPrescriptionId: prescriptionId,
    );
    final prescription = prescriptionDetail.prescription;
    if (prescription.status != PatientPrescriptionStatus.active) {
      throw const PharmacyDispenseEvidenceChanged();
    }
    final matches = prescription.items.where(
      (row) => row.id == prescriptionItemId,
    );
    if (matches.length != 1) {
      throw const PharmacyDispenseEvidenceChanged();
    }
    final item = matches.single;
    if (item.quantity == null || item.quantity! < quantity) {
      throw const PharmacyDispenseEvidenceChanged();
    }
    final drug = PharmacyDrug.fromJson(
      _data(
        await _get('api/v1/pharmacy/drugs/${item.drugId}', original, revision),
      ),
      expectedClinicId: original.clinicContextId!,
      expectedId: item.drugId,
    );
    if (!drug.isActive) {
      throw const PharmacyDispenseEvidenceChanged();
    }
    final batch = PharmacyBatch.fromJson(
      _data(await _get('api/v1/pharmacy/batches/$batchId', original, revision)),
      expectedClinicId: original.clinicContextId!,
      expectedDrugId: item.drugId,
    );
    if (batch.id != batchId ||
        batch.quantityOnHand < quantity ||
        !_expiryFuture(batch.expiryDate)) {
      throw const PharmacyDispenseEvidenceChanged();
    }
    return PharmacyDispenseReview(
      patientId: patientId,
      prescriptionId: prescriptionId,
      prescriptionItemId: prescriptionItemId,
      drugId: drug.id,
      drugName: drug.name,
      batchId: batch.id,
      batchNumber: batch.batchNumber,
      quantity: quantity,
      batchQuantity: batch.quantityOnHand,
      batchExpiry: batch.expiryDate,
      prescribedQuantity: item.quantity!,
    );
  }

  /// Read-only; a review does not hold stock or authorize dispensing.
  Future<PharmacyDispenseReview> review({
    required int patientId,
    required int prescriptionId,
    required int prescriptionItemId,
    required int batchId,
    required int quantity,
  }) async {
    if (_writeAttempted) {
      throw const PharmacyDispenseAlreadyAttempted();
    }
    _id(patientId, 'patientId');
    _id(prescriptionId, 'prescriptionId');
    _id(prescriptionItemId, 'prescriptionItemId');
    _id(batchId, 'batchId');
    _id(quantity, 'quantity');
    final revision = inventory.sessionNavigationGuard.revision;
    final original = await _pharmacist(revision);
    final evidence = await _loadEvidence(
      original: original,
      revision: revision,
      patientId: patientId,
      prescriptionId: prescriptionId,
      prescriptionItemId: prescriptionItemId,
      batchId: batchId,
      quantity: quantity,
    );
    await _verify(original, revision);
    _review = evidence;
    return evidence;
  }

  /// One POST maximum for this adapter. A fresh GET must confirm identity,
  /// prescription, line, quantity, status, and patient/clinic preflight.
  Future<PharmacyDispenseRecord> dispenseOnce({
    required PharmacyDispenseReview reviewed,
    required String idempotencyKey,
    String? notes,
  }) async {
    if (_writeAttempted) {
      throw const PharmacyDispenseAlreadyAttempted();
    }
    if (!identical(reviewed, _review)) {
      throw const PharmacyDispenseEvidenceChanged();
    }
    if (idempotencyKey.isEmpty ||
        idempotencyKey != idempotencyKey.trim() ||
        idempotencyKey.length > 255 ||
        idempotencyKey.contains('\n') ||
        idempotencyKey.contains('\r') ||
        (notes != null && (notes.length > 2000 || notes != notes.trim()))) {
      throw const FormatException('Invalid dispensing submission.');
    }
    final revision = inventory.sessionNavigationGuard.revision;
    final original = await _pharmacist(revision);
    final fresh = await _loadEvidence(
      original: original,
      revision: revision,
      patientId: reviewed.patientId,
      prescriptionId: reviewed.prescriptionId,
      prescriptionItemId: reviewed.prescriptionItemId,
      batchId: reviewed.batchId,
      quantity: reviewed.quantity,
    );
    await _verify(original, revision);
    if (!reviewed.sameServerEvidence(fresh)) {
      throw const PharmacyDispenseEvidenceChanged();
    }

    _writeAttempted = true;
    _review = null;
    try {
      final response = await inventory.apiClient.postJson(
        'api/v1/pharmacy/dispense',
        body: <String, dynamic>{
          'prescription_id': reviewed.prescriptionId,
          'items': <Map<String, dynamic>>[
            <String, dynamic>{
              'prescription_item_id': reviewed.prescriptionItemId,
              'batch_id': reviewed.batchId,
              'quantity': reviewed.quantity,
            },
          ],
          'notes': ?notes,
        },
        bearerToken: original.accessToken,
        idempotencyKey: idempotencyKey,
      );
      await _verify(original, revision);
      final accepted = PharmacyDispenseRecord.fromEnvelope(
        response,
        expectedPrescriptionId: reviewed.prescriptionId,
      );
      accepted.verifyCreatedLine(
        prescriptionItemId: reviewed.prescriptionItemId,
        batchId: reviewed.batchId,
        quantity: reviewed.quantity,
      );
      final confirmed = PharmacyDispenseRecord.fromEnvelope(
        await _get(
          'api/v1/pharmacy/dispense/${accepted.id}',
          original,
          revision,
        ),
        expectedPrescriptionId: reviewed.prescriptionId,
        expectedRecordId: accepted.id,
      );
      confirmed.verifyCreatedLine(
        prescriptionItemId: reviewed.prescriptionItemId,
        batchId: reviewed.batchId,
        quantity: reviewed.quantity,
      );
      if (confirmed.dispensedById != accepted.dispensedById ||
          confirmed.status != accepted.status) {
        throw const PharmacyDispenseOutcomeUnconfirmed();
      }
      await _verify(original, revision);
      return confirmed;
    } on PharmacyDispenseScopeUnavailable {
      rethrow;
    } on PharmacyDispenseOutcomeUnconfirmed {
      rethrow;
    } on Object {
      throw const PharmacyDispenseOutcomeUnconfirmed();
    }
  }
}
