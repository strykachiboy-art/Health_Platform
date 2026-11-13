import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/surgery/data/surgery_read_remote_data_source.dart';

import 'surgery_action_contract.dart';

class SurgeryMutationUnavailable implements Exception {
  const SurgeryMutationUnavailable();
}

class SurgeryMutationAlreadyAttempted implements Exception {
  const SurgeryMutationAlreadyAttempted();
}

class SurgeryMutationUnconfirmed implements Exception {
  const SurgeryMutationUnconfirmed();
}

class SurgeryMutationConfirmed {
  const SurgeryMutationConfirmed({required this.recordId, required this.kind});
  final int recordId;
  final SurgeryActionKind kind;
}

/// A single writer is owned by the router and retained across route revisits.
/// There are no offline queues, retries, idempotency keys, or automatic replay.
class SurgeryMutationRemoteDataSource {
  SurgeryMutationRemoteDataSource({
    required this.reader,
    required this.connectivity,
  });

  final SurgeryReadRemoteDataSource reader;
  final DeviceConnectivitySource connectivity;
  final Set<String> _attempted = <String>{};

  SessionNavigationGuard? get _guard =>
      reader.dashboardSource.sessionNavigationGuard;

  bool _current(int revision) =>
      _guard != null &&
      _guard!.canAccessProtectedRoutes &&
      _guard!.revision == revision;

  static bool _same(AuthTokenResponse x, AuthTokenResponse y) =>
      x.userId == y.userId &&
      x.role.toLowerCase() == y.role.toLowerCase() &&
      x.clinicContextId == y.clinicContextId &&
      x.accessToken == y.accessToken &&
      x.refreshToken == y.refreshToken;

  Future<AuthTokenResponse> _session(int revision, String role) async {
    if (!_current(revision)) {
      throw const SurgeryMutationUnavailable();
    }
    try {
      final state = await reader.dashboardSource.loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          session.role.toLowerCase() != role ||
          !_current(revision)) {
        throw const SurgeryMutationUnavailable();
      }
      return session;
    } on SurgeryMutationUnavailable {
      rethrow;
    } on Object {
      throw const SurgeryMutationUnavailable();
    }
  }

  Future<void> _verify(
    AuthTokenResponse first,
    int revision,
    String role,
  ) async {
    if (!_same(first, await _session(revision, role)) || !_current(revision)) {
      throw const SurgeryMutationUnavailable();
    }
  }

  Future<Map<String, dynamic>> _get(
    String path,
    AuthTokenResponse session,
    int revision,
  ) async {
    if (!_current(revision)) {
      throw const SurgeryMutationUnavailable();
    }
    final response = await reader.dashboardSource.apiClient.getJson(
      path,
      bearerToken: session.accessToken,
    );
    await _verify(session, revision, session.role.toLowerCase());
    if (response['success'] != true ||
        response['data'] is! Map<String, dynamic>) {
      throw const FormatException(
        'Surgery status readback is not authoritative.',
      );
    }
    final data = response['data'] as Map<String, dynamic>;
    return data;
  }

  String _statusPath(SurgeryActionKind kind, int caseId) => switch (kind) {
    SurgeryActionKind.book ||
    SurgeryActionKind.reschedule ||
    SurgeryActionKind.cancelBooking => 'api/v1/surgery/cases/$caseId/bookings',
    SurgeryActionKind.withdrawConsent =>
      'api/v1/surgery/cases/$caseId/consents',
    SurgeryActionKind.signIn =>
      'api/v1/surgery/cases/$caseId/checklists/sign_in',
    SurgeryActionKind.timeOut =>
      'api/v1/surgery/cases/$caseId/checklists/time_out',
    SurgeryActionKind.preanesthesia ||
    SurgeryActionKind.baseline ||
    SurgeryActionKind.attestPreanesthesia =>
      'api/v1/surgery/cases/$caseId/preanesthesia',
    SurgeryActionKind.reportRevise ||
    SurgeryActionKind.reportSign => 'api/v1/surgery/cases/$caseId/reports',
  };

  static int _id(Object? raw) {
    if (raw is! int || raw <= 0) {
      throw const FormatException('Invalid Surgery status identity.');
    }
    return raw;
  }

  /// Explicit user-requested readback only; never sends a write.
  /// Missing optional resources remain unavailable rather than invented.
  Future<Map<String, dynamic>> reconcile({
    required int caseId,
    required SurgeryActionKind kind,
  }) async {
    final revision = _guard?.revision ?? -1;
    final session = await _session(revision, kind.role);
    final current = await reader.fetchCase(caseId);
    await _verify(session, revision, kind.role);
    if (current.caseRecord.caseId != caseId ||
        current.clinicId != session.clinicContextId) {
      throw const SurgeryMutationUnavailable();
    }
    final data = await _get(_statusPath(kind, caseId), session, revision);
    if (data.containsKey('clinic_id') &&
        data['clinic_id'] != session.clinicContextId) {
      throw const SurgeryMutationUnavailable();
    }
    if (data.containsKey('case_id') && data['case_id'] != caseId) {
      throw const SurgeryMutationUnavailable();
    }
    if (data.containsKey('surgical_case_id') &&
        data['surgical_case_id'] != caseId) {
      throw const SurgeryMutationUnavailable();
    }
    return data;
  }

  static Map<String, dynamic> _findItem(Map<String, dynamic> data, int id) {
    final items = data['items'];
    if (items is! List<dynamic>) {
      throw const FormatException('Surgery status list is missing.');
    }
    final matches = items
        .where((item) => item is Map<String, dynamic> && item['id'] == id)
        .toList();
    if (matches.length != 1 || matches.single is! Map<String, dynamic>) {
      throw const FormatException(
        'Surgery status identity not uniquely confirmed.',
      );
    }
    return matches.single as Map<String, dynamic>;
  }

  /// Ensure an existing resource belongs to this selected case before a write.
  Future<void> _preflightResource(
    SurgeryActionInput input,
    AuthTokenResponse session,
    int revision,
  ) async {
    final id = input.resourceId;
    if (id == null) {
      return;
    }
    final data = await _get(
      _statusPath(input.kind, input.caseId),
      session,
      revision,
    );
    if (data.containsKey('case_id') && data['case_id'] != input.caseId) {
      throw const SurgeryMutationUnavailable();
    }
    switch (input.kind) {
      case SurgeryActionKind.reschedule:
      case SurgeryActionKind.cancelBooking:
      case SurgeryActionKind.withdrawConsent:
        final row = _findItem(data, id);
        if (row['clinic_id'] != session.clinicContextId ||
            row['surgical_case_id'] != input.caseId ||
            row['version'] !=
                int.tryParse(input.raw['expected_version'] ?? '')) {
          throw const SurgeryMutationUnavailable();
        }
      case SurgeryActionKind.baseline:
      case SurgeryActionKind.attestPreanesthesia:
      case SurgeryActionKind.reportRevise:
      case SurgeryActionKind.reportSign:
        if (data['id'] != id ||
            data['clinic_id'] != session.clinicContextId ||
            data['surgical_case_id'] != input.caseId ||
            data['version'] !=
                int.tryParse(input.raw['expected_version'] ?? '')) {
          throw const SurgeryMutationUnavailable();
        }
      default:
        throw const SurgeryMutationUnavailable();
    }
  }

  Future<SurgeryMutationConfirmed> submitOnce(SurgeryActionInput input) async {
    // Parse complete strict input BEFORE any irreversible attempt is recorded.
    final body = input.encode();
    final resourceId = input.resourceId;
    final revision = _guard?.revision ?? -1;
    final session = await _session(revision, input.kind.role);
    final key =
        '${session.userId}:${session.clinicContextId}:${input.caseId}:'
        '${input.kind.name}:${resourceId ?? 0}';
    if (_attempted.contains(key)) {
      throw const SurgeryMutationAlreadyAttempted();
    }
    if (await connectivity.checkStatus() != DeviceNetworkStatus.connected) {
      throw const SurgeryMutationUnavailable();
    }
    // The existing CE1 read service authenticates assignment and case lineage.
    final current = await reader.fetchCase(input.caseId);
    await _verify(session, revision, input.kind.role);
    if (current.caseRecord.caseId != input.caseId ||
        current.clinicId != session.clinicContextId ||
        current.caseRecord.caseStatus != 'planned') {
      throw const SurgeryMutationUnavailable();
    }
    if (resourceId != null) {
      await _preflightResource(input, session, revision);
      await _verify(session, revision, input.kind.role);
    }
    // Retain the attempt across route revisits. Even 4xx, 409, timeout, or
    // a malformed response cannot re-enable the same mutation automatically.
    if (!_attempted.add(key)) {
      throw const SurgeryMutationAlreadyAttempted();
    }
    try {
      final path = input.kind.path(input.caseId, resourceId);
      final api = reader.dashboardSource.apiClient;
      final response = input.kind.usePatch
          ? await api.patchJson(
              path,
              body: body,
              bearerToken: session.accessToken,
            )
          : await api.postJson(
              path,
              body: body,
              bearerToken: session.accessToken,
            );
      await _verify(session, revision, input.kind.role);
      if (response['success'] != true ||
          response['documentation_only'] != true ||
          response['operative_completion_authorized'] != false ||
          response['anesthesia_induction_authorized'] != false ||
          response['medication_administration_authorized'] != false ||
          response['nursing_transfer_authorized'] != false ||
          response['billing_clearance_authorized'] != false ||
          response['data'] is! Map<String, dynamic>) {
        throw const FormatException(
          'Surgery response contains unsafe authority flags.',
        );
      }
      final row = response['data'] as Map<String, dynamic>;
      if (row['clinic_id'] != session.clinicContextId ||
          (row.containsKey('surgical_case_id') &&
              row['surgical_case_id'] != input.caseId)) {
        throw const SurgeryMutationUnavailable();
      }
      final recordId = _id(row['id']);
      final status = await _get(
        _statusPath(input.kind, input.caseId),
        session,
        revision,
      );
      if (status.containsKey('case_id') && status['case_id'] != input.caseId) {
        throw const FormatException('Surgery readback has a different case.');
      }
      final Map<String, dynamic> matched;
      if (input.kind == SurgeryActionKind.book ||
          input.kind == SurgeryActionKind.reschedule ||
          input.kind == SurgeryActionKind.cancelBooking ||
          input.kind == SurgeryActionKind.withdrawConsent) {
        matched = _findItem(status, recordId);
      } else {
        matched = status;
        if (input.kind == SurgeryActionKind.signIn ||
            input.kind == SurgeryActionKind.timeOut) {
          if (status['phase'] !=
                  (input.kind == SurgeryActionKind.signIn
                      ? 'sign_in'
                      : 'time_out') ||
              status['evidence_sha256'] is! String ||
              (status['evidence_sha256'] as String).length != 64) {
            throw const FormatException(
              'Checklist attestation evidence is missing.',
            );
          }
        } else if (input.kind == SurgeryActionKind.baseline) {
          // Baseline is appended under an assessment; its version increases.
          if (status['id'] != resourceId ||
              (status['version'] is! int) ||
              (status['version'] as int) <= (body['expected_version'] as int)) {
            throw const FormatException(
              'Assessment baseline version did not advance.',
            );
          }
        } else if (input.kind == SurgeryActionKind.reportSign) {
          // A signed report is re-read from the case, but the signature itself
          // must be reconciled by an explicit authoritative status GET below.
          final signatures = await _get(
            'api/v1/surgery/reports/$resourceId/signatures',
            session,
            revision,
          );
          final sign = _findItem(signatures, recordId);
          if (sign['report_id'] != resourceId ||
              sign['signer_kind'] != body['signer_kind'] ||
              sign['document_sha256'] is! String) {
            throw const FormatException(
              'Clinical signature was not confirmed.',
            );
          }
        }
      }
      if (matched.containsKey('clinic_id') &&
          matched['clinic_id'] != session.clinicContextId) {
        throw const FormatException('Surgery readback clinic mismatch.');
      }
      if (matched.containsKey('surgical_case_id') &&
          matched['surgical_case_id'] != input.caseId) {
        throw const FormatException('Surgery readback case mismatch.');
      }
      if (input.kind != SurgeryActionKind.baseline &&
          input.kind != SurgeryActionKind.reportSign &&
          _id(matched['id']) != recordId) {
        throw const FormatException('Surgery readback identity mismatch.');
      }
      if (row['version'] is int && matched['version'] != row['version']) {
        throw const FormatException('Surgery readback version mismatch.');
      }
      return SurgeryMutationConfirmed(recordId: recordId, kind: input.kind);
    } on Object {
      // The request might have committed. Only GET reconciliation is allowed;
      // this adapter will never retry the clinical write.
      throw const SurgeryMutationUnconfirmed();
    }
  }
}
