import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'models/pharmacy_stock.dart';

class PharmacyInventorySessionUnavailable implements Exception {
  const PharmacyInventorySessionUnavailable();
}

/// GET-only stock reader. No persistence, optimistic stock adjustments,
/// offline queue, dispense mutation, or automatic retry.
class PharmacyInventoryRemoteDataSource {
  const PharmacyInventoryRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  static const _readerRoles = <String>{'admin', 'pharmacist'};

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _authorize(int revision) async {
    if (!_current(revision)) {
      throw const PharmacyInventorySessionUnavailable();
    }
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
          !_readerRoles.contains(session.role.trim().toLowerCase()) ||
          !_current(revision)) {
        throw const PharmacyInventorySessionUnavailable();
      }
      return session;
    } on PharmacyInventorySessionUnavailable {
      rethrow;
    } on Object {
      throw const PharmacyInventorySessionUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<Map<String, dynamic>> _get(
    String path,
    int revision,
    AuthTokenResponse original,
  ) async {
    final before = await _authorize(revision);
    if (!_same(original, before)) {
      throw const PharmacyInventorySessionUnavailable();
    }
    final response = await apiClient.getJson(
      path,
      bearerToken: before.accessToken,
    );
    final after = await _authorize(revision);
    if (!_same(before, after) || !_same(original, after)) {
      throw const PharmacyInventorySessionUnavailable();
    }
    return response;
  }

  static Map<String, dynamic> _data(Map<String, dynamic> envelope) {
    final raw = envelope['data'];
    if (envelope['success'] != true || raw is! Map<String, dynamic>) {
      throw const FormatException('Invalid pharmacy detail envelope.');
    }
    return raw;
  }

  static void _id(int value, String name) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, name, 'Invalid positive ID.');
    }
  }

  static void _page(int page, int perPage) {
    if (page <= 0 || perPage <= 0 || perPage > 100) {
      throw ArgumentError('Invalid pharmacy pagination.');
    }
  }

  Future<PharmacyPage<PharmacyDrug>> listDrugs({
    int page = 1,
    int perPage = 20,
  }) async {
    _page(page, perPage);
    final revision = sessionNavigationGuard.revision;
    final session = await _authorize(revision);
    final envelope = await _get(
      'api/v1/pharmacy/drugs?page=$page&per_page=$perPage&include_inactive=false',
      revision,
      session,
    );
    return PharmacyPage<PharmacyDrug>.fromEnvelope(
      envelope,
      requestedPage: page,
      requestedPerPage: perPage,
      parse: (json) => PharmacyDrug.fromJson(
        json,
        expectedClinicId: session.clinicContextId!,
      ),
      idOf: (drug) => drug.id,
    );
  }

  Future<PharmacyStockSnapshot> loadStock({
    required int drugId,
    int page = 1,
    int perPage = 20,
  }) async {
    _id(drugId, 'drugId');
    _page(page, perPage);
    final revision = sessionNavigationGuard.revision;
    final session = await _authorize(revision);
    final clinicId = session.clinicContextId!;

    final rawDrug = _data(
      await _get('api/v1/pharmacy/drugs/$drugId', revision, session),
    );
    final drug = PharmacyDrug.fromJson(
      rawDrug,
      expectedClinicId: clinicId,
      expectedId: drugId,
    );
    final rawSummary = _data(
      await _get(
        'api/v1/pharmacy/drugs/$drugId/stock-summary',
        revision,
        session,
      ),
    );
    final summary = PharmacyStockSummary.fromJson(
      rawSummary,
      expectedClinicId: clinicId,
      expectedDrugId: drugId,
    );
    if (summary.drugName != drug.name) {
      throw const FormatException('Conflicting pharmacy drug names.');
    }
    final envelope = await _get(
      'api/v1/pharmacy/drugs/$drugId/batches?page=$page&per_page=$perPage&include_expired=true',
      revision,
      session,
    );
    final batches = PharmacyPage<PharmacyBatch>.fromEnvelope(
      envelope,
      requestedPage: page,
      requestedPerPage: perPage,
      parse: (json) => PharmacyBatch.fromJson(
        json,
        expectedClinicId: clinicId,
        expectedDrugId: drugId,
      ),
      idOf: (batch) => batch.id,
    );
    return PharmacyStockSnapshot(
      drug: drug,
      summary: summary,
      batches: batches,
    );
  }
}
