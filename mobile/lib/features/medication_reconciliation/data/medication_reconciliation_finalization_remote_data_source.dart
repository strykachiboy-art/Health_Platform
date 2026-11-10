// P23G.S04: online-only, author-controlled backend finalization and amendment.
// This additive adapter deliberately does not modify S01-S03.D or the router.
import 'dart:convert';

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

class MedRecS04ScopeUnavailable implements Exception {
  const MedRecS04ScopeUnavailable();
}

class MedRecS04EvidenceChanged implements Exception {
  const MedRecS04EvidenceChanged();
}

class MedRecS04OutcomeUnconfirmed implements Exception {
  const MedRecS04OutcomeUnconfirmed();
}

abstract class MedRecS04Transport {
  Future<Map<String, dynamic>> get(String path, String token);
  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body,
    String token, {
    String? idempotencyKey,
  });
}

class MedRecS04ApiTransport implements MedRecS04Transport {
  const MedRecS04ApiTransport(this.apiClient);
  final ApiClient apiClient;

  @override
  Future<Map<String, dynamic>> get(String path, String token) =>
      apiClient.getJson(path, bearerToken: token);

  @override
  Future<Map<String, dynamic>> post(
    String path,
    Map<String, dynamic> body,
    String token, {
    String? idempotencyKey,
  }) => apiClient.postJson(
    path,
    body: body,
    bearerToken: token,
    idempotencyKey: idempotencyKey,
  );
}

/// A strict, in-memory record used for finalization. Never cached to disk.
class MedRecS04Record {
  MedRecS04Record._({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.admissionId,
    required this.authorStaffId,
    required this.eventType,
    required this.wardTransferId,
    required this.status,
    required this.version,
    required this.updatedAt,
    required this.finalizedAt,
    required this.latestAmendmentId,
    required this.noMedicationsReported,
    required this.notes,
    required this.items,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int admissionId;
  final int authorStaffId;
  final String eventType;
  final int? wardTransferId;
  final String status;
  final int version;
  final String updatedAt;
  final String? finalizedAt;
  final int? latestAmendmentId;
  final bool noMedicationsReported;
  final String? notes;
  final List<Map<String, dynamic>> items;

  static int _positive(Map<String, dynamic> json, String name) {
    final value = json[name];
    if (value is! int || value <= 0) {
      throw FormatException('Invalid medication reconciliation $name.');
    }
    return value;
  }

  static int? _optionalPositive(Map<String, dynamic> json, String name) {
    final value = json[name];
    if (value == null) return null;
    if (value is! int || value <= 0) {
      throw FormatException('Invalid medication reconciliation $name.');
    }
    return value;
  }

  static bool _aware(String value) =>
      RegExp(r'(Z|[+-][0-9]{2}:[0-9]{2})$').hasMatch(value) &&
      DateTime.tryParse(value) != null;

  factory MedRecS04Record.fromJson(
    Map<String, dynamic> json, {
    required int clinicId,
    required int patientId,
    required int admissionId,
    required int reconciliationId,
  }) {
    if (_positive(json, 'id') != reconciliationId ||
        _positive(json, 'clinic_id') != clinicId ||
        _positive(json, 'patient_id') != patientId ||
        _positive(json, 'admission_id') != admissionId) {
      throw const FormatException('Medication reconciliation scope mismatch.');
    }
    final status = json['record_status'];
    final event = json['event_type'];
    final itemsRaw = json['items'];
    final timestamp = json['updated_at'];
    final noMeds = json['no_medications_reported'];
    final notes = json['notes'];
    final finalizedAt = json['finalized_at'];
    if (status is! String ||
        !const {'draft', 'final', 'amended'}.contains(status) ||
        event is! String ||
        !const {'admission', 'transfer', 'discharge'}.contains(event) ||
        timestamp is! String ||
        !_aware(timestamp) ||
        itemsRaw is! List ||
        itemsRaw.length > 500 ||
        noMeds is! bool ||
        (notes != null && (notes is! String || notes.length > 10000)) ||
        (finalizedAt != null &&
            (finalizedAt is! String || !_aware(finalizedAt)))) {
      throw const FormatException('Malformed reconciliation record.');
    }
    final items = <Map<String, dynamic>>[];
    for (final entry in itemsRaw) {
      if (entry is! Map<String, dynamic> ||
          entry['medication_name'] is! String ||
          entry['decision'] is! String) {
        throw const FormatException('Malformed reconciliation item.');
      }
      items.add(Map<String, dynamic>.unmodifiable(entry));
    }
    return MedRecS04Record._(
      id: reconciliationId,
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admissionId,
      authorStaffId: _positive(json, 'created_by_staff_id'),
      eventType: event,
      wardTransferId: _optionalPositive(json, 'ward_transfer_id'),
      status: status,
      version: _positive(json, 'record_version'),
      updatedAt: timestamp,
      finalizedAt: finalizedAt as String?,
      latestAmendmentId: _optionalPositive(json, 'latest_amendment_id'),
      noMedicationsReported: noMeds,
      notes: notes as String?,
      items: List<Map<String, dynamic>>.unmodifiable(items),
    );
  }

  Map<String, dynamic> get document => <String, dynamic>{
    'no_medications_reported': noMedicationsReported,
    'notes': notes,
    'items': items.map((e) => Map<String, dynamic>.from(e)).toList(),
  };

  bool get hasResolvedDecisions {
    if ((items.isEmpty && !noMedicationsReported) ||
        (items.isNotEmpty && noMedicationsReported)) {
      return false;
    }
    for (final row in items) {
      final decision = row['decision'];
      if (decision is! String ||
          !const {
            'continue',
            'hold',
            'stop',
            'replace',
            'new',
          }.contains(decision)) {
        return false;
      }
      if (decision == 'replace' &&
          (row['replacement_medication_name'] is! String ||
              (row['replacement_medication_name'] as String).trim().isEmpty)) {
        return false;
      }
    }
    return true;
  }

  String get _documentIdentity => jsonEncode(document);

  bool sameReviewedEvidence(MedRecS04Record other) =>
      id == other.id &&
      clinicId == other.clinicId &&
      patientId == other.patientId &&
      admissionId == other.admissionId &&
      authorStaffId == other.authorStaffId &&
      eventType == other.eventType &&
      wardTransferId == other.wardTransferId &&
      status == other.status &&
      version == other.version &&
      updatedAt == other.updatedAt &&
      _documentIdentity == other._documentIdentity;

  bool sameDocument(MedRecS04Record other) =>
      _documentIdentity == other._documentIdentity;
}

/// A fresh instance is required for a new intentional clinical operation.
/// Once a POST begins the same instance cannot submit another POST, even after
/// a timeout, 409, transport error or malformed response. No offline replay.
class MedRecS04FinalizationRemoteDataSource {
  MedRecS04FinalizationRemoteDataSource({
    required this.transport,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final MedRecS04Transport transport;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  bool _writeStarted = false;
  bool get writeStarted => _writeStarted;

  static const _base = 'api/v1/medication-reconciliations';
  static const _writeRoles = <String>{'doctor', 'pharmacist'};

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _authorized(int revision) async {
    if (!_current(revision)) throw const MedRecS04ScopeUnavailable();
    try {
      final state = await loadSession();
      final s = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          s == null ||
          s.userId <= 0 ||
          s.clinicContextId == null ||
          s.clinicContextId! <= 0 ||
          !_writeRoles.contains(s.role.trim().toLowerCase()) ||
          s.accessToken.isEmpty ||
          s.accessToken.trim() != s.accessToken ||
          !_current(revision)) {
        throw const MedRecS04ScopeUnavailable();
      }
      return s;
    } on MedRecS04ScopeUnavailable {
      rethrow;
    } on Object {
      throw const MedRecS04ScopeUnavailable();
    }
  }

  static bool _sameSession(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  static void _positive(int v) {
    if (v <= 0) throw ArgumentError.value(v, 'id');
  }

  Future<MedRecS04Record> _read(
    int revision,
    int id,
    int clinicId,
    int patientId,
    int admissionId,
  ) async {
    final before = await _authorized(revision);
    if (before.clinicContextId != clinicId) {
      throw const MedRecS04ScopeUnavailable();
    }
    final raw = await transport.get('$_base/$id', before.accessToken);
    final after = await _authorized(revision);
    if (!_sameSession(before, after) || !_current(revision)) {
      throw const MedRecS04ScopeUnavailable();
    }
    final payload = raw['reconciliation'];
    if (payload is! Map<String, dynamic>) {
      throw const FormatException('Missing reconciliation detail envelope.');
    }
    return MedRecS04Record.fromJson(
      payload,
      clinicId: clinicId,
      patientId: patientId,
      admissionId: admissionId,
      reconciliationId: id,
    );
  }

  Future<MedRecS04Record> inspect({
    required int reconciliationId,
    required int clinicId,
    required int patientId,
    required int admissionId,
  }) {
    for (final id in [reconciliationId, clinicId, patientId, admissionId]) {
      _positive(id);
    }
    return _read(
      sessionNavigationGuard.revision,
      reconciliationId,
      clinicId,
      patientId,
      admissionId,
    );
  }

  Future<AuthTokenResponse> _preflight(MedRecS04Record reviewed) async {
    if (_writeStarted) throw const MedRecS04OutcomeUnconfirmed();
    final revision = sessionNavigationGuard.revision;
    final before = await _authorized(revision);
    if (before.clinicContextId != reviewed.clinicId) {
      throw const MedRecS04ScopeUnavailable();
    }
    final fresh = await _read(
      revision,
      reviewed.id,
      reviewed.clinicId,
      reviewed.patientId,
      reviewed.admissionId,
    );
    if (!reviewed.sameReviewedEvidence(fresh) || !_current(revision)) {
      throw const MedRecS04EvidenceChanged();
    }
    final after = await _authorized(revision);
    if (!_sameSession(before, after) || !_current(revision)) {
      throw const MedRecS04ScopeUnavailable();
    }
    // Staff author identity is intentionally enforced by the backend. A JWT
    // user_id is NOT a staff_id and must never be compared to authorStaffId.
    return after;
  }

  Future<MedRecS04Record> finalizeOnce({
    required MedRecS04Record reviewed,
    required String idempotencyKey,
  }) async {
    if (_writeStarted) throw const MedRecS04OutcomeUnconfirmed();
    if (reviewed.status != 'draft' ||
        reviewed.version != 1 ||
        !reviewed.hasResolvedDecisions ||
        idempotencyKey.isEmpty ||
        idempotencyKey.trim() != idempotencyKey ||
        idempotencyKey.length > 255) {
      throw const MedRecS04EvidenceChanged();
    }
    final approved = await _preflight(reviewed);
    final revision = sessionNavigationGuard.revision;
    if (_writeStarted) throw const MedRecS04OutcomeUnconfirmed();
    _writeStarted = true;
    try {
      final result = await transport.post(
        '$_base/${reviewed.id}/finalize',
        <String, dynamic>{
          'expected_version': reviewed.version,
          'expected_updated_at': reviewed.updatedAt,
        },
        approved.accessToken,
        idempotencyKey: idempotencyKey,
      );
      final after = await _authorized(revision);
      if (!_sameSession(approved, after) || !_current(revision)) {
        throw const MedRecS04OutcomeUnconfirmed();
      }
      final payload = result['reconciliation'];
      if (payload is! Map<String, dynamic>) {
        throw const FormatException('Missing finalization response.');
      }
      final response = MedRecS04Record.fromJson(
        payload,
        clinicId: reviewed.clinicId,
        patientId: reviewed.patientId,
        admissionId: reviewed.admissionId,
        reconciliationId: reviewed.id,
      );
      final confirmed = await _read(
        revision,
        reviewed.id,
        reviewed.clinicId,
        reviewed.patientId,
        reviewed.admissionId,
      );
      if (response.status != 'final' ||
          response.version != reviewed.version ||
          response.finalizedAt == null ||
          confirmed.status != 'final' ||
          confirmed.version != response.version ||
          confirmed.finalizedAt == null ||
          confirmed.authorStaffId != reviewed.authorStaffId ||
          !reviewed.sameDocument(confirmed)) {
        throw const MedRecS04OutcomeUnconfirmed();
      }
      return confirmed;
    } on Object {
      throw const MedRecS04OutcomeUnconfirmed();
    }
  }

  /// Current S04 UI only changes notes, but the backend receives the complete
  /// effective document, never a partial amendment or draft-CAS field.
  Future<MedRecS04Record> amendNotesOnce({
    required MedRecS04Record reviewed,
    required String reason,
    required String notes,
  }) async {
    if (_writeStarted) throw const MedRecS04OutcomeUnconfirmed();
    if (!const {'final', 'amended'}.contains(reviewed.status) ||
        !reviewed.hasResolvedDecisions ||
        reason.trim().isEmpty ||
        reason != reason.trim() ||
        reason.length > 10000 ||
        notes.length > 10000 ||
        reviewed.notes == notes) {
      throw const MedRecS04EvidenceChanged();
    }
    final approved = await _preflight(reviewed);
    final revision = sessionNavigationGuard.revision;
    if (_writeStarted) throw const MedRecS04OutcomeUnconfirmed();
    _writeStarted = true;
    try {
      final document = reviewed.document;
      document['notes'] = notes;
      final result = await transport.post(
        '$_base/${reviewed.id}/amend',
        <String, dynamic>{
          'expected_version': reviewed.version,
          'reason': reason,
          'document': document,
        },
        approved.accessToken,
      );
      final after = await _authorized(revision);
      if (!_sameSession(approved, after) || !_current(revision)) {
        throw const MedRecS04OutcomeUnconfirmed();
      }
      final payload = result['reconciliation'];
      final amendment = result['amendment'];
      if (payload is! Map<String, dynamic> ||
          amendment is! Map<String, dynamic> ||
          amendment['id'] is! int ||
          (amendment['id'] as int) <= 0 ||
          amendment['previous_version'] != reviewed.version ||
          amendment['new_version'] != reviewed.version + 1 ||
          amendment['source_record_id'] != reviewed.id) {
        throw const FormatException('Incomplete amendment envelope.');
      }
      final amendmentId = amendment['id'] as int;
      final response = MedRecS04Record.fromJson(
        payload,
        clinicId: reviewed.clinicId,
        patientId: reviewed.patientId,
        admissionId: reviewed.admissionId,
        reconciliationId: reviewed.id,
      );
      final confirmed = await _read(
        revision,
        reviewed.id,
        reviewed.clinicId,
        reviewed.patientId,
        reviewed.admissionId,
      );
      if (response.status != 'amended' ||
          response.version != reviewed.version + 1 ||
          response.latestAmendmentId != amendmentId ||
          confirmed.status != response.status ||
          confirmed.version != response.version ||
          confirmed.latestAmendmentId != response.latestAmendmentId ||
          confirmed.notes != notes ||
          confirmed.items.length != reviewed.items.length ||
          confirmed.noMedicationsReported != reviewed.noMedicationsReported ||
          jsonEncode(confirmed.document['items']) !=
              jsonEncode(reviewed.document['items'])) {
        throw const MedRecS04OutcomeUnconfirmed();
      }
      return confirmed;
    } on Object {
      throw const MedRecS04OutcomeUnconfirmed();
    }
  }
}
