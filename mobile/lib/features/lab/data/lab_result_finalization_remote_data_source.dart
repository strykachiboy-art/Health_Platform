import 'dart:convert';

import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/models/lab_order_read.dart';

// S05B: lab-technician-only result entry, verification and completion.
// Every clinical POST is one-shot. No offline queue or automatic retry.
enum LabResultAction { enterResult, verify, complete }

class LabResultScopeUnavailable implements Exception {
  const LabResultScopeUnavailable();
}

class LabResultEvidenceChanged implements Exception {
  const LabResultEvidenceChanged();
}

class LabResultOutcomeUnconfirmed implements Exception {
  const LabResultOutcomeUnconfirmed();
}

class LabResultAlreadyAttempted implements Exception {
  const LabResultAlreadyAttempted();
}

class LabResultOrderEvidence {
  const LabResultOrderEvidence({
    required this.order,
    required this.raw,
    required this.snapshot,
    required this.collectedById,
    required this.collectedAt,
    required this.processedById,
    required this.processedAt,
    required this.verifiedById,
    required this.verifiedAt,
    required this.completedAt,
    required this.items,
  });

  final LabPatientOrder order;
  final Map<String, dynamic> raw;
  final String snapshot;
  final int? collectedById;
  final String? collectedAt;
  final int? processedById;
  final String? processedAt;
  final int? verifiedById;
  final String? verifiedAt;
  final String? completedAt;
  final Map<int, Map<String, dynamic>> items;

  static int? _optionalId(Map<String, dynamic> data, String key) {
    final value = data[key];
    if (value == null) {
      return null;
    }
    if (value is! int || value <= 0 || value > 2147483647) {
      throw FormatException('Invalid laboratory actor: $key');
    }
    return value;
  }

  static String? _optionalText(Map<String, dynamic> data, String key) {
    final value = data[key];
    if (value == null) {
      return null;
    }
    if (value is! String || value.trim().isEmpty || value != value.trim()) {
      throw FormatException('Invalid laboratory field: $key');
    }
    return value;
  }

  factory LabResultOrderEvidence.fromEnvelope(
    Map<String, dynamic> response, {
    required int clinicId,
    required int patientId,
    required int orderId,
  }) {
    final data = response['data'];
    if (response['success'] != true || data is! Map<String, dynamic>) {
      throw const FormatException('Invalid laboratory order response.');
    }
    final order = LabPatientOrder.fromJson(
      data,
      expectedClinicId: clinicId,
      expectedPatientId: patientId,
      expectedId: orderId,
    );
    final collectedBy = _optionalId(data, 'collected_by_id');
    final collectedAt = _optionalText(data, 'sample_collected_at');
    final processedBy = _optionalId(data, 'processed_by_id');
    final processedAt = _optionalText(data, 'processed_at');
    final verifiedBy = _optionalId(data, 'verified_by_id');
    final verifiedAt = _optionalText(data, 'verified_at');
    if ((collectedBy == null) != (collectedAt == null) ||
        (processedBy == null) != (processedAt == null) ||
        (verifiedBy == null) != (verifiedAt == null)) {
      throw const FormatException('Incomplete lab actor/timestamp evidence.');
    }
    final rawItems = data['items'] as List;
    final items = <int, Map<String, dynamic>>{};
    for (final rawItem in rawItems) {
      if (rawItem is! Map<String, dynamic>) {
        throw const FormatException('Invalid laboratory item.');
      }
      final id = rawItem['id'];
      if (id is! int || id <= 0 || items.containsKey(id)) {
        throw const FormatException('Invalid laboratory item ID.');
      }
      final resultedAt = _optionalText(rawItem, 'resulted_at');
      final result = _optionalText(rawItem, 'result_value');
      if ((result == null) != (resultedAt == null)) {
        throw const FormatException('Incomplete laboratory result evidence.');
      }
      _optionalText(rawItem, 'result_file_url');
      items[id] = rawItem;
    }
    return LabResultOrderEvidence(
      order: order,
      raw: data,
      snapshot: jsonEncode(data),
      collectedById: collectedBy,
      collectedAt: collectedAt,
      processedById: processedBy,
      processedAt: processedAt,
      verifiedById: verifiedBy,
      verifiedAt: verifiedAt,
      completedAt: _optionalText(data, 'completed_at'),
      items: Map<int, Map<String, dynamic>>.unmodifiable(items),
    );
  }

  bool sameSnapshot(LabResultOrderEvidence other) => snapshot == other.snapshot;
  bool get allResulted =>
      items.isNotEmpty &&
      items.values.every(
        (item) =>
            item['result_value'] is String && item['resulted_at'] is String,
      );
}

class LabResultReview {
  const LabResultReview({
    required this.evidence,
    required this.action,
    required this.orderItemId,
    required this.resultValue,
    required this.resultNotes,
  });

  final LabResultOrderEvidence evidence;
  final LabResultAction action;
  final int? orderItemId;
  final String? resultValue;
  final String? resultNotes;
}

class LabResultFinalizationRemoteDataSource {
  LabResultFinalizationRemoteDataSource({required this.reader});

  final LabOrderReadRemoteDataSource reader;
  bool _attempted = false;
  LabResultReview? _review;
  AuthTokenResponse? _session;
  int? _revision;

  bool get writeAttempted => _attempted;

  static void _id(int value) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, 'id', 'Expected a positive ID.');
    }
  }

  bool _valid(int revision) =>
      reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      reader.sessionNavigationGuard.revision == revision;

  static bool _sameSession(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> _technician(int revision) async {
    if (!_valid(revision)) {
      throw const LabResultScopeUnavailable();
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
          session.accessToken != session.accessToken.trim() ||
          session.role.trim().toLowerCase() != 'lab_technician' ||
          !_valid(revision)) {
        throw const LabResultScopeUnavailable();
      }
      return session;
    } on LabResultScopeUnavailable {
      rethrow;
    } on Object {
      throw const LabResultScopeUnavailable();
    }
  }

  Future<void> _verify(AuthTokenResponse original, int revision) async {
    final current = await _technician(revision);
    if (!_valid(revision) || !_sameSession(original, current)) {
      throw const LabResultScopeUnavailable();
    }
  }

  Future<LabResultOrderEvidence> _get(
    AuthTokenResponse session,
    int revision,
    int patientId,
    int orderId,
  ) async {
    await _verify(session, revision);
    final response = await reader.apiClient.getJson(
      'api/v1/lab/orders/$orderId',
      bearerToken: session.accessToken,
    );
    await _verify(session, revision);
    return LabResultOrderEvidence.fromEnvelope(
      response,
      clinicId: session.clinicContextId!,
      patientId: patientId,
      orderId: orderId,
    );
  }

  static void _authorize(LabResultReview review) {
    final evidence = review.evidence;
    if (evidence.collectedById == null ||
        evidence.collectedAt == null ||
        evidence.processedById == null ||
        evidence.processedAt == null) {
      throw const LabResultEvidenceChanged();
    }
    switch (review.action) {
      case LabResultAction.enterResult:
        final item = evidence.items[review.orderItemId];
        if (evidence.order.status != LabOrderStatus.inProgress ||
            evidence.verifiedById != null ||
            evidence.verifiedAt != null ||
            item == null ||
            review.resultValue == null ||
            (review.resultValue?.isEmpty ?? true)) {
          throw const LabResultEvidenceChanged();
        }
        return;
      case LabResultAction.verify:
        if (evidence.order.status != LabOrderStatus.inProgress ||
            evidence.verifiedById != null ||
            evidence.verifiedAt != null ||
            !evidence.allResulted) {
          throw const LabResultEvidenceChanged();
        }
        return;
      case LabResultAction.complete:
        if (evidence.order.status != LabOrderStatus.inProgress ||
            evidence.verifiedById == null ||
            evidence.verifiedAt == null ||
            !evidence.allResulted) {
          throw const LabResultEvidenceChanged();
        }
        return;
    }
  }

  Future<LabResultReview> review({
    required int patientId,
    required int orderId,
    required LabResultAction action,
    int? orderItemId,
    String? resultValue,
    String? resultNotes,
  }) async {
    if (_attempted) {
      throw const LabResultAlreadyAttempted();
    }
    _id(patientId);
    _id(orderId);
    if (action == LabResultAction.enterResult) {
      if (orderItemId == null) {
        throw const FormatException('Order item ID is required.');
      }
      _id(orderItemId);
    } else if (orderItemId != null) {
      throw const FormatException('Unexpected result item for action.');
    }
    final value = resultValue?.trim();
    final notes = resultNotes?.trim();
    if (action == LabResultAction.enterResult) {
      if (value == null || value.isEmpty || value.length > 150) {
        throw const FormatException('Result value must be 1–150 characters.');
      }
    } else if (value != null && value.isNotEmpty) {
      throw const FormatException('Unexpected result value.');
    }
    if (notes != null && notes.length > 1000) {
      throw const FormatException('Result notes exceed 1000 characters.');
    }
    final revision = reader.sessionNavigationGuard.revision;
    final session = await _technician(revision);
    final evidence = await _get(session, revision, patientId, orderId);
    final proposed = LabResultReview(
      evidence: evidence,
      action: action,
      orderItemId: orderItemId,
      resultValue: action == LabResultAction.enterResult ? value : null,
      resultNotes:
          action == LabResultAction.enterResult &&
              notes != null &&
              notes.isNotEmpty
          ? notes
          : null,
    );
    _authorize(proposed);
    await _verify(session, revision);
    _session = session;
    _revision = revision;
    _review = proposed;
    return proposed;
  }

  Future<LabResultOrderEvidence> submitOnce({
    required LabResultReview reviewed,
  }) async {
    if (_attempted) {
      throw const LabResultAlreadyAttempted();
    }
    final revision = reader.sessionNavigationGuard.revision;
    final original = _session;
    if (!identical(reviewed, _review) ||
        original == null ||
        _revision != revision) {
      throw const LabResultEvidenceChanged();
    }
    await _verify(original, revision);
    final evidence = reviewed.evidence;
    final fresh = await _get(
      original,
      revision,
      evidence.order.patientId,
      evidence.order.id,
    );
    if (!evidence.sameSnapshot(fresh)) {
      throw const LabResultEvidenceChanged();
    }
    _authorize(reviewed);
    await _verify(original, revision);
    final String endpoint;
    final Map<String, dynamic> body;
    final orderPath = 'api/v1/lab/orders/${evidence.order.id}';
    switch (reviewed.action) {
      case LabResultAction.enterResult:
        endpoint = 'api/v1/lab/order-items/${reviewed.orderItemId}/result';
        body = <String, dynamic>{'result_value': reviewed.resultValue};
        if (reviewed.resultNotes != null) {
          body['result_notes'] = reviewed.resultNotes;
        }
        break;
      case LabResultAction.verify:
        endpoint = '$orderPath/verify';
        body = <String, dynamic>{};
        break;
      case LabResultAction.complete:
        endpoint = '$orderPath/complete';
        body = <String, dynamic>{};
        break;
    }
    // Lock before the POST: even a dropped response may have persisted a write.
    _attempted = true;
    try {
      final posted = await reader.apiClient.postJson(
        endpoint,
        body: body,
        bearerToken: original.accessToken,
      );
      await _verify(original, revision);
      if (posted['success'] != true ||
          posted['data'] is! Map<String, dynamic>) {
        throw const LabResultOutcomeUnconfirmed();
      }
      final receipt = posted['data'] as Map<String, dynamic>;
      final authoritative = await _get(
        original,
        revision,
        evidence.order.patientId,
        evidence.order.id,
      );
      final confirmed = switch (reviewed.action) {
        LabResultAction.enterResult => _confirmedResult(
          reviewed,
          receipt,
          authoritative,
        ),
        LabResultAction.verify => _confirmedOrder(
          reviewed,
          receipt,
          authoritative,
        ),
        LabResultAction.complete => _confirmedOrder(
          reviewed,
          receipt,
          authoritative,
        ),
      };
      if (!confirmed) {
        throw const LabResultOutcomeUnconfirmed();
      }
      return authoritative;
    } on Object {
      throw const LabResultOutcomeUnconfirmed();
    }
  }

  static bool _confirmedResult(
    LabResultReview review,
    Map<String, dynamic> receipt,
    LabResultOrderEvidence result,
  ) {
    final item = result.items[review.orderItemId];
    return item != null &&
        receipt['id'] == review.orderItemId &&
        receipt['order_id'] == review.evidence.order.id &&
        receipt['test_id'] == item['test_id'] &&
        receipt['result_value'] == review.resultValue &&
        receipt['result_value'] == item['result_value'] &&
        receipt['result_notes'] == item['result_notes'] &&
        item['result_notes'] == review.resultNotes &&
        receipt['flag'] == item['flag'] &&
        receipt['resulted_at'] == item['resulted_at'] &&
        item['resulted_at'] is String &&
        result.order.status == LabOrderStatus.inProgress &&
        result.verifiedAt == null;
  }

  static bool _confirmedOrder(
    LabResultReview review,
    Map<String, dynamic> receipt,
    LabResultOrderEvidence result,
  ) {
    LabResultOrderEvidence receiptEvidence;
    try {
      receiptEvidence = LabResultOrderEvidence.fromEnvelope(
        <String, dynamic>{'success': true, 'data': receipt},
        clinicId: result.order.clinicId,
        patientId: result.order.patientId,
        orderId: result.order.id,
      );
    } on Object {
      return false;
    }
    if (!receiptEvidence.sameSnapshot(result)) {
      return false;
    }
    switch (review.action) {
      case LabResultAction.enterResult:
        return false;
      case LabResultAction.verify:
        return result.order.status == LabOrderStatus.inProgress &&
            result.verifiedById != null &&
            result.verifiedAt != null &&
            result.allResulted;
      case LabResultAction.complete:
        return result.order.status == LabOrderStatus.completed &&
            result.verifiedById != null &&
            result.verifiedAt != null &&
            result.completedAt != null &&
            result.allResulted;
    }
  }
}
