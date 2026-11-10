import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/models/lab_order_read.dart';

// P23H.S04B: doctor/nurse lab create/cancel only; no queued clinical writes.
// Lab POST endpoints have no verified idempotency contract. Never auto-replay.
class LabMutationScopeUnavailable implements Exception {
  const LabMutationScopeUnavailable();
}

class LabMutationEvidenceChanged implements Exception {
  const LabMutationEvidenceChanged();
}

class LabMutationOutcomeUnconfirmed implements Exception {
  const LabMutationOutcomeUnconfirmed();
}

class LabMutationAlreadyAttempted implements Exception {
  const LabMutationAlreadyAttempted();
}

class LabOrderCreateReview {
  const LabOrderCreateReview({
    required this.patientId,
    required this.testIds,
    required this.testNames,
    required this.consultationId,
  });

  final int patientId;
  final List<int> testIds;
  final List<String> testNames;
  final int? consultationId;

  bool matches(LabOrderCreateReview other) =>
      patientId == other.patientId &&
      consultationId == other.consultationId &&
      _sameIntList(testIds, other.testIds) &&
      _sameStringList(testNames, other.testNames);
}

class LabOrderCancelReview {
  const LabOrderCancelReview({required this.order});

  final LabPatientOrder order;

  bool matches(LabOrderCancelReview other) =>
      order.id == other.order.id &&
      order.clinicId == other.order.clinicId &&
      order.patientId == other.order.patientId &&
      order.consultationId == other.order.consultationId &&
      order.orderedById == other.order.orderedById &&
      order.status == other.order.status &&
      _sameIntList(
        order.items.map((item) => item.testId).toList()..sort(),
        other.order.items.map((item) => item.testId).toList()..sort(),
      );
}

bool _sameIntList(List<int> a, List<int> b) {
  if (a.length != b.length) {
    return false;
  }
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) {
      return false;
    }
  }
  return true;
}

bool _sameStringList(List<String> a, List<String> b) {
  if (a.length != b.length) {
    return false;
  }
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) {
      return false;
    }
  }
  return true;
}

class LabOrderMutationRemoteDataSource {
  LabOrderMutationRemoteDataSource({required this.reader});

  final LabOrderReadRemoteDataSource reader;
  bool _attempted = false;
  Object? _review;
  AuthTokenResponse? _reviewSession;
  int? _reviewRevision;

  bool get writeAttempted => _attempted;

  static bool _sameSession(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  bool _valid(int revision) =>
      reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      reader.sessionNavigationGuard.revision == revision;

  static void _id(int id, String label) {
    if (id <= 0 || id > 2147483647) {
      throw ArgumentError.value(id, label, 'Expected positive identifier.');
    }
  }

  Future<AuthTokenResponse> _clinician(int revision) async {
    if (!_valid(revision)) {
      throw const LabMutationScopeUnavailable();
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
          !const <String>{
            'doctor',
            'nurse',
          }.contains(session.role.trim().toLowerCase()) ||
          !_valid(revision)) {
        throw const LabMutationScopeUnavailable();
      }
      return session;
    } on LabMutationScopeUnavailable {
      rethrow;
    } on Object {
      throw const LabMutationScopeUnavailable();
    }
  }

  Future<void> _verify(AuthTokenResponse expected, int revision) async {
    final session = await _clinician(revision);
    if (!_sameSession(expected, session) || !_valid(revision)) {
      throw const LabMutationScopeUnavailable();
    }
  }

  Future<Map<String, dynamic>> _get(
    String path,
    AuthTokenResponse original,
    int revision,
  ) async {
    await _verify(original, revision);
    final result = await reader.apiClient.getJson(
      path,
      bearerToken: original.accessToken,
    );
    await _verify(original, revision);
    return result;
  }

  static Map<String, dynamic> _data(Map<String, dynamic> raw) {
    if (raw['success'] != true || raw['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid laboratory response envelope.');
    }
    return raw['data'] as Map<String, dynamic>;
  }

  Future<LabOrderCreateReview> _createEvidence({
    required int patientId,
    required List<int> testIds,
    required int? consultationId,
    required AuthTokenResponse session,
    required int revision,
  }) async {
    final names = <String>[];
    for (final id in testIds) {
      final test = LabCatalogTest.fromJson(
        _data(await _get('api/v1/lab/tests/$id', session, revision)),
        expectedClinicId: session.clinicContextId!,
        expectedId: id,
      );
      if (!test.isActive) {
        throw const LabMutationEvidenceChanged();
      }
      names.add(test.name);
    }
    return LabOrderCreateReview(
      patientId: patientId,
      testIds: List<int>.unmodifiable(testIds),
      testNames: List<String>.unmodifiable(names),
      consultationId: consultationId,
    );
  }

  Future<LabOrderCreateReview> reviewCreate({
    required int patientId,
    required List<int> testIds,
    int? consultationId,
  }) async {
    if (_attempted) {
      throw const LabMutationAlreadyAttempted();
    }
    _id(patientId, 'patientId');
    if (consultationId != null) {
      _id(consultationId, 'consultationId');
    }
    if (testIds.isEmpty ||
        testIds.length > 30 ||
        testIds.toSet().length != testIds.length) {
      throw const FormatException('Select 1 to 30 unique laboratory tests.');
    }
    for (final id in testIds) {
      _id(id, 'testId');
    }
    final revision = reader.sessionNavigationGuard.revision;
    final session = await _clinician(revision);
    final review = await _createEvidence(
      patientId: patientId,
      testIds: testIds,
      consultationId: consultationId,
      session: session,
      revision: revision,
    );
    await _verify(session, revision);
    _review = review;
    _reviewSession = session;
    _reviewRevision = revision;
    return review;
  }

  Future<LabOrderCancelReview> reviewCancel({
    required int patientId,
    required int orderId,
  }) async {
    if (_attempted) {
      throw const LabMutationAlreadyAttempted();
    }
    _id(patientId, 'patientId');
    _id(orderId, 'orderId');
    final revision = reader.sessionNavigationGuard.revision;
    final session = await _clinician(revision);
    final order = await reader.fetchOrder(
      patientId: patientId,
      orderId: orderId,
    );
    await _verify(session, revision);
    if (order.status == LabOrderStatus.cancelled ||
        order.status == LabOrderStatus.completed) {
      throw const LabMutationEvidenceChanged();
    }
    final review = LabOrderCancelReview(order: order);
    _review = review;
    _reviewSession = session;
    _reviewRevision = revision;
    return review;
  }

  Future<AuthTokenResponse> _checkReview(Object review, int revision) async {
    if (_attempted) {
      throw const LabMutationAlreadyAttempted();
    }
    if (!identical(review, _review) ||
        _reviewRevision != revision ||
        _reviewSession == null) {
      throw const LabMutationEvidenceChanged();
    }
    final original = _reviewSession!;
    await _verify(original, revision);
    return original;
  }

  void _attempt() {
    _attempted = true;
    _review = null;
    _reviewSession = null;
    _reviewRevision = null;
  }

  static LabPatientOrder _order(
    Map<String, dynamic> envelope,
    AuthTokenResponse session, {
    required int patientId,
    int? expectedId,
  }) => LabPatientOrder.fromJson(
    _data(envelope),
    expectedClinicId: session.clinicContextId!,
    expectedPatientId: patientId,
    expectedId: expectedId,
  );

  // Exactly one create POST per adapter. No lab create idempotency header.
  Future<LabPatientOrder> createOnce({
    required LabOrderCreateReview reviewed,
  }) async {
    final revision = reader.sessionNavigationGuard.revision;
    final session = await _checkReview(reviewed, revision);
    final fresh = await _createEvidence(
      patientId: reviewed.patientId,
      testIds: reviewed.testIds,
      consultationId: reviewed.consultationId,
      session: session,
      revision: revision,
    );
    await _verify(session, revision);
    if (!reviewed.matches(fresh)) {
      throw const LabMutationEvidenceChanged();
    }
    _attempt();
    try {
      final body = <String, dynamic>{
        'patient_id': reviewed.patientId,
        'test_ids': reviewed.testIds,
      };
      if (reviewed.consultationId != null) {
        body['consultation_id'] = reviewed.consultationId;
      }
      final posted = await reader.apiClient.postJson(
        'api/v1/lab/orders',
        bearerToken: session.accessToken,
        body: body,
      );
      await _verify(session, revision);
      final initial = _order(posted, session, patientId: reviewed.patientId);
      if (initial.status != LabOrderStatus.ordered ||
          initial.consultationId != reviewed.consultationId ||
          !_sameIntList(
            initial.items.map((item) => item.testId).toList()..sort(),
            reviewed.testIds.toList()..sort(),
          )) {
        throw const LabMutationOutcomeUnconfirmed();
      }
      final current = _order(
        await _get('api/v1/lab/orders/${initial.id}', session, revision),
        session,
        patientId: reviewed.patientId,
        expectedId: initial.id,
      );
      if (current.status != LabOrderStatus.ordered ||
          current.consultationId != reviewed.consultationId ||
          current.orderedById != initial.orderedById ||
          !_sameIntList(
            current.items.map((item) => item.testId).toList()..sort(),
            reviewed.testIds.toList()..sort(),
          )) {
        throw const LabMutationOutcomeUnconfirmed();
      }
      await _verify(session, revision);
      return current;
    } on Object {
      throw const LabMutationOutcomeUnconfirmed();
    }
  }

  // Exactly one cancellation POST per adapter. No automatic replay.
  Future<LabPatientOrder> cancelOnce({
    required LabOrderCancelReview reviewed,
    required String reason,
  }) async {
    if (reason.trim().isEmpty ||
        reason != reason.trim() ||
        reason.length > 255) {
      throw const FormatException(
        'Enter a cancellation reason (max 255 characters).',
      );
    }
    final revision = reader.sessionNavigationGuard.revision;
    final session = await _checkReview(reviewed, revision);
    final freshOrder = await reader.fetchOrder(
      patientId: reviewed.order.patientId,
      orderId: reviewed.order.id,
    );
    await _verify(session, revision);
    if (!reviewed.matches(LabOrderCancelReview(order: freshOrder)) ||
        freshOrder.status == LabOrderStatus.completed ||
        freshOrder.status == LabOrderStatus.cancelled) {
      throw const LabMutationEvidenceChanged();
    }
    _attempt();
    try {
      final posted = await reader.apiClient.postJson(
        'api/v1/lab/orders/${reviewed.order.id}/cancel',
        bearerToken: session.accessToken,
        body: <String, dynamic>{'reason': reason},
      );
      await _verify(session, revision);
      final initial = _order(
        posted,
        session,
        patientId: reviewed.order.patientId,
        expectedId: reviewed.order.id,
      );
      if (initial.status != LabOrderStatus.cancelled) {
        throw const LabMutationOutcomeUnconfirmed();
      }
      final current = _order(
        await _get('api/v1/lab/orders/${reviewed.order.id}', session, revision),
        session,
        patientId: reviewed.order.patientId,
        expectedId: reviewed.order.id,
      );
      if (current.status != LabOrderStatus.cancelled ||
          current.orderedById != reviewed.order.orderedById ||
          current.consultationId != reviewed.order.consultationId ||
          !_sameIntList(
            current.items.map((item) => item.testId).toList()..sort(),
            reviewed.order.items.map((item) => item.testId).toList()..sort(),
          )) {
        throw const LabMutationOutcomeUnconfirmed();
      }
      await _verify(session, revision);
      return current;
    } on Object {
      throw const LabMutationOutcomeUnconfirmed();
    }
  }
}
