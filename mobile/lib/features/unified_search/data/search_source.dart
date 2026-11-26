import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const searchRoles = <String>{
  'admin',
  'doctor',
  'nurse',
  'receptionist',
  'pharmacist',
  'lab_technician',
  'paramedic',
  'emt',
};
const _appointmentRoles = <String>{'admin', 'doctor', 'nurse', 'receptionist'};
const _labRoles = <String>{
  'admin',
  'doctor',
  'nurse',
  'receptionist',
  'lab_technician',
};
const _clinicalRoles = <String>{'doctor', 'nurse'};
const _orderStatuses = <String>{
  'pending',
  'active',
  'in_progress',
  'on_hold',
  'completed',
  'cancelled',
};
// Ordered exactly as the backend rank, not by display text.
const searchResources = <String, (Set<String>, Set<String>)>{
  'patient': (searchRoles, {'active', 'inactive'}),
  'appointment': (
    _appointmentRoles,
    {'scheduled', 'confirmed', 'cancelled', 'completed', 'no_show'},
  ),
  'lab_order': (
    _labRoles,
    {'ordered', 'sample_collected', 'in_progress', 'completed', 'cancelled'},
  ),
  'prescription': (
    {'doctor', 'pharmacist'},
    {'active', 'completed', 'cancelled', 'expired'},
  ),
  'imaging_order': (_clinicalRoles, _orderStatuses),
  'procedure_order': (_clinicalRoles, _orderStatuses),
  'blood_product_order': (
    {'doctor', 'nurse', 'lab_technician'},
    _orderStatuses,
  ),
  'diet_order': (_clinicalRoles, _orderStatuses),
  'nursing_order': (_clinicalRoles, _orderStatuses),
  'nursing_task': (
    _clinicalRoles,
    {'pending', 'in_progress', 'completed', 'cancelled'},
  ),
  'medication_administration': (
    {'doctor', 'nurse'},
    {
      'scheduled',
      'due',
      'administered',
      'held',
      'refused',
      'missed',
      'cancelled',
    },
  ),
};
const searchDebounce = Duration(milliseconds: 350);
const _maxId = 2147483647;
Never _bad() =>
    throw const FormatException('Invalid unified search data or filters.');
Map<String, dynamic> _map(Object? v) => v is Map<String, dynamic> ? v : _bad();
List<dynamic> _list(Object? v) => v is List<dynamic> ? v : _bad();
int _id(Object? v) => v is int && v > 0 && v <= _maxId ? v : _bad();
String _text(Object? v, int max) =>
    v is String && v.trim().isNotEmpty && v.runes.length <= max ? v : _bad();
String _uuid(Object? v) {
  final s = _text(v, 36);
  if (!RegExp(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')
      .hasMatch(s)) {
    _bad();
  }
  return s.toLowerCase();
}

class SearchUnavailable implements Exception {
  const SearchUnavailable();
}

class SearchFilters {
  SearchFilters(String text, {this.resourceType, this.status, this.limit = 25})
    : query = text.trim() {
    if (text.runes.length > 120 ||
        query.runes.length < 2 ||
        RegExp(r'[\x00-\x1f\x7f-\x9f]').hasMatch(text) ||
        limit < 1 ||
        limit > 50) {
      _bad();
    }
    if (query.startsWith('#') &&
        (!RegExp(r'^#[1-9][0-9]{0,9}$').hasMatch(query) ||
            (int.tryParse(query.substring(1)) ?? _maxId + 1) > _maxId)) {
      _bad();
    }
    if (resourceType != null && !searchResources.containsKey(resourceType)) {
      _bad();
    }
    if (status != null &&
        !(resourceType == null
            ? searchResources.values.any((r) => r.$2.contains(status))
            : searchResources[resourceType]!.$2.contains(status))) {
      _bad();
    }
  }
  final String query;
  final String? resourceType, status;
  final int limit;
  int? get exactId =>
      query.startsWith('#') ? int.parse(query.substring(1)) : null;
  (String, String?, String?, int) get key =>
      (query, resourceType, status, limit);
  Map<String, dynamic> body(String? cursor) => {
    'q': query,
    'resource_types': resourceType == null ? null : [resourceType],
    'status': status,
    'limit': limit,
    'cursor': cursor,
  };
}

class SearchHit {
  SearchHit._(Map<String, dynamic> j)
    : resourceType = _text(j['resource_type'], 40),
      recordId = _id(j['record_id']),
      patientId = _id(j['patient_id']),
      patientName = _text(j['patient_name'], 161),
      patientNumber = _text(j['patient_number'], 50),
      status = _text(j['status'], 40),
      admissionId = j['admission_id'] == null ? null : _id(j['admission_id']),
      kind = _text(j['kind'], 16) {
    final def = searchResources[resourceType];
    if (def == null ||
        !def.$2.contains(status) ||
        kind != (resourceType == 'patient' ? 'patient' : 'operational') ||
        (isPatient && (patientId != recordId || admissionId != null))) {
      _bad();
    }
    if (const {
          'imaging_order',
          'procedure_order',
          'blood_product_order',
          'diet_order',
          'nursing_order',
          'nursing_task',
          'medication_administration',
        }.contains(resourceType) &&
        admissionId == null) {
      _bad();
    }
  }
  final String resourceType, patientName, patientNumber, status, kind;
  final int recordId, patientId;
  final int? admissionId;
  bool get isPatient => resourceType == 'patient';
  int compareTo(SearchHit other) {
    final ranks = searchResources.keys.toList();
    final rank = ranks
        .indexOf(resourceType)
        .compareTo(ranks.indexOf(other.resourceType));
    return rank == 0 ? other.recordId.compareTo(recordId) : rank;
  }

  // Existing destinations only. Collections are not represented as exact detail.
  String? routeFor(String role) {
    if (searchResources[resourceType]?.$1.contains(role) != true) return null;
    return switch (resourceType) {
      'patient' => '/patients/$patientId',
      'appointment' => '/appointments/patient/$patientId',
      'lab_order' => '/patients/$patientId/lab-orders',
      'prescription' => '/patients/$patientId/prescriptions/$recordId',
      'imaging_order' => '/clinical-orders/patient/$patientId/imaging',
      'procedure_order' => '/clinical-orders/patient/$patientId/procedures',
      'blood_product_order' =>
        role == 'lab_technician'
            ? '/clinical-orders/blood-products'
            : '/clinical-orders/patient/$patientId/blood-products',
      'diet_order' => '/clinical-orders/patient/$patientId/diets',
      'nursing_order' => '/clinical-orders/patient/$patientId/nursing-orders',
      'nursing_task' => '/wards',
      'medication_administration' => '/emar/patient/$patientId',
      _ => null,
    };
  }

  bool get exactRoute => isPatient || resourceType == 'prescription';
}

class SearchPage {
  const SearchPage._(this.items, this.cursor, this._session, this._key);
  final List<SearchHit> items;
  final String? cursor;
  final SearchSession _session;
  final (String, String?, String?, int) _key;
}

class SearchSource {
  const SearchSource({
    required this.apiClient,
    required this.loadSession,
    required this.guard,
  });
  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard guard;
  Future<AuthTokenResponse> _ready(int revision) async {
    if (!guard.canAccessProtectedRoutes || guard.revision != revision) {
      throw const SearchUnavailable();
    }
    try {
      final state = await loadSession();
      final a = state.session;
      if (!guard.canAccessProtectedRoutes ||
          guard.revision != revision ||
          state.disposition != AuthSessionDisposition.ready ||
          a == null ||
          a.userId <= 0 ||
          (a.clinicContextId ?? 0) <= 0 ||
          a.tenantId == null ||
          a.accessToken.trim().isEmpty ||
          a.accessToken.trim() != a.accessToken ||
          a.refreshToken.trim().isEmpty ||
          a.refreshToken.trim() != a.refreshToken ||
          !searchRoles.contains(a.role.trim().toLowerCase())) {
        throw const SearchUnavailable();
      }
      _uuid(a.tenantId);
      return a;
    } on Object {
      throw const SearchUnavailable();
    }
  }

  Future<SearchSession> open() async {
    final revision = guard.revision;
    final auth = await _ready(revision);
    return SearchSession._(this, auth, revision);
  }
}

class SearchSession {
  const SearchSession._(this._source, this._auth, this._revision);
  final SearchSource _source;
  final AuthTokenResponse _auth;
  final int _revision;
  String get role => _auth.role.trim().toLowerCase();
  bool get current =>
      _source.guard.canAccessProtectedRoutes &&
      _source.guard.revision == _revision;
  Map<String, Set<String>> get available => Map.unmodifiable({
    for (final e in searchResources.entries)
      if (e.value.$1.contains(role)) e.key: e.value.$2,
  });
  Future<void> check() async {
    final a = await _source._ready(_revision);
    if (a.userId != _auth.userId ||
        a.role != _auth.role ||
        a.clinicContextId != _auth.clinicContextId ||
        a.tenantId != _auth.tenantId ||
        a.accessToken != _auth.accessToken ||
        a.refreshToken != _auth.refreshToken) {
      throw const SearchUnavailable();
    }
  }

  Future<SearchPage> read(
    SearchFilters filters, {
    SearchPage? previous,
    bool Function()? stillWanted,
  }) async {
    await check();
    final allowed = available;
    final kind = filters.resourceType;
    if (kind != null && !allowed.containsKey(kind)) {
      throw const SearchUnavailable();
    }
    final status = filters.status;
    if (status != null &&
        !(kind == null
            ? allowed.values.any((s) => s.contains(status))
            : allowed[kind]!.contains(status))) {
      _bad();
    }
    final cursor = previous?.cursor;
    if (previous != null &&
        (cursor == null ||
            !identical(previous._session, this) ||
            previous._key != filters.key)) {
      _bad();
    }
    // Direct transport only; never send a read search to an offline mutation queue.
    if (stillWanted?.call() == false) throw const SearchUnavailable();
    final raw = await _source.apiClient.postJson(
      'api/v1/search',
      body: filters.body(cursor),
      bearerToken: _auth.accessToken,
    );
    await check();
    if (raw['success'] != true) {
      _bad();
    }
    final j = _map(raw['data']);
    if (_uuid(j['organization_id']) != _uuid(_auth.tenantId) ||
        _id(j['clinic_id']) != _auth.clinicContextId) {
      _bad();
    }
    final actual = _map(j['available_resources']);
    if (actual.length != allowed.length ||
        !actual.keys.every(allowed.containsKey)) {
      _bad();
    }
    for (final e in actual.entries) {
      final statuses = _list(e.value);
      if (statuses.length != allowed[e.key]!.length ||
          statuses.toSet().length != statuses.length ||
          !statuses.every(allowed[e.key]!.contains)) {
        _bad();
      }
    }
    final rows = _list(j['items']);
    final next = j['next_cursor'] == null
        ? null
        : _text(j['next_cursor'], 2048);
    if (rows.length > filters.limit ||
        (next != null && (next == cursor || rows.length != filters.limit))) {
      _bad();
    }
    final hits = <SearchHit>[];
    SearchHit? last = previous == null || previous.items.isEmpty
        ? null
        : previous.items.last;
    for (final row in rows) {
      final hit = SearchHit._(_map(row));
      if (!allowed.containsKey(hit.resourceType) ||
          (kind != null && hit.resourceType != kind) ||
          (status != null && hit.status != status) ||
          (filters.exactId != null && hit.recordId != filters.exactId) ||
          (last != null && last.compareTo(hit) >= 0)) {
        _bad();
      }
      hits.add(hit);
      last = hit;
    }
    return SearchPage._(List.unmodifiable(hits), next, this, filters.key);
  }
}
