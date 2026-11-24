import 'dart:convert';

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const hieViewRoles = <String>{
  'admin',
  'doctor',
  'nurse',
  'lab_technician',
  'pharmacist',
};
const hieHoldCode = 'HIE_PARTNER_APPROVAL_REQUIRED';
const _statuses = <String>{'pending', 'active', 'suspended', 'disabled'};
const _operations = <String>{
  'patient_submission',
  'clinical_data_submission',
  'clinical_document_submission',
  'patient_query',
  'clinical_data_query',
};
const _submissionStatuses = <String>{'pending', 'success', 'failed'};
const _failureClasses = <String>{
  'retryable',
  'reconciliation_required',
  'user_action_required',
  'fail_closed',
};

class HieUnavailable implements Exception {
  const HieUnavailable();
}

class HieMutationUncertain implements Exception {
  const HieMutationUncertain();
}

int _id(Object? value) {
  if (value is! int || value <= 0 || value > 2147483647) {
    throw const FormatException('Invalid HIE identifier.');
  }
  return value;
}

String _requiredText(Object? value, int limit) {
  if (value is! String ||
      value.isEmpty ||
      value.trim() != value ||
      value.length > limit ||
      RegExp(r'[\x00-\x1f\x7f]').hasMatch(value)) {
    throw const FormatException('Invalid HIE text.');
  }
  return value;
}

String? _optionalText(Object? value, int limit) =>
    value == null ? null : _requiredText(value, limit);

String? _endpoint(Object? value) {
  final text = _optionalText(value, 255);
  if (text == null) return null;
  final uri = Uri.tryParse(text);
  if (uri == null ||
      uri.scheme != 'https' ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      uri.hasQuery ||
      uri.hasFragment ||
      uri.port != 443 ||
      text.contains('\\') ||
      text.contains('@') ||
      RegExp(r'\s').hasMatch(text)) {
    throw const FormatException('Invalid HIE endpoint.');
  }
  return text;
}

bool _sameEndpoint(String? a, String? b) {
  if (a == null || b == null) return a == b;
  final x = Uri.parse(a);
  final y = Uri.parse(b);
  return x.host.toLowerCase() == y.host.toLowerCase() &&
      x.port == y.port &&
      (x.path.isEmpty ? '/' : x.path) == (y.path.isEmpty ? '/' : y.path);
}

class HieIntegration {
  const HieIntegration._({
    required this.id,
    required this.clinicId,
    required this.provider,
    required this.status,
    required this.endpoint,
    required this.organization,
    required this.facility,
    required this.holdConfirmed,
    required this.providerRegistered,
  });

  final int id;
  final int clinicId;
  final String provider;
  final String status;
  final String? endpoint;
  final String? organization;
  final String? facility;
  final bool holdConfirmed;
  final bool? providerRegistered;

  // This staged client never authorizes external queries or activation,
  // including when a legacy server reports ACTIVE or readiness=true.
  bool get externalAccessAllowed => false;

  factory HieIntegration.parse(
    Map<String, dynamic> row,
    int clinic, {
    int? expectedId,
  }) {
    final id = _id(row['id']);
    final clinicId = _id(row['clinic_id']);
    if (clinicId != clinic ||
        (expectedId != null && id != expectedId) ||
        !_statuses.contains(row['status'])) {
      throw const FormatException('HIE integration scope or status mismatch.');
    }
    final readiness = row['readiness'];
    final held =
        readiness is Map<String, dynamic> &&
        readiness['provider_registered'] is bool &&
        readiness['external_access_allowed'] == false &&
        readiness['reason_code'] == hieHoldCode;
    return HieIntegration._(
      id: id,
      clinicId: clinicId,
      provider: _requiredText(row['provider'], 50),
      status: row['status'] as String,
      endpoint: _endpoint(row['endpoint_url']),
      organization: _optionalText(row['organization_id'], 100),
      facility: _optionalText(row['facility_id'], 100),
      holdConfirmed: held,
      providerRegistered: held
          ? readiness['provider_registered'] as bool
          : null,
    );
  }
}

class HieDraft {
  const HieDraft({
    required this.provider,
    this.endpoint = '',
    this.organization = '',
    this.facility = '',
  });
  final String provider;
  final String endpoint;
  final String organization;
  final String facility;

  Map<String, dynamic> toJson() {
    final name = _requiredText(provider.trim().toLowerCase(), 50);
    final url = endpoint.trim();
    final org = organization.trim();
    final fac = facility.trim();
    return <String, dynamic>{
      'provider': name,
      if (url.isNotEmpty) 'endpoint_url': _endpoint(url),
      if (org.isNotEmpty) 'organization_id': _requiredText(org, 100),
      if (fac.isNotEmpty) 'facility_id': _requiredText(fac, 100),
    };
  }
}

class HieSubmission {
  const HieSubmission._(
    this.id,
    this.integrationId,
    this.operation,
    this.status,
    this.failureClass,
    this.statusCode,
    this.retryCount,
    this.createdAt,
  );
  final int id;
  final int integrationId;
  final String operation;
  final String status;
  final String? failureClass;
  final int? statusCode;
  final int retryCount;
  final String createdAt;

  factory HieSubmission.parse(
    Map<String, dynamic> row,
    int clinic, {
    int? integrationId,
  }) {
    final integration = _id(row['integration_id']);
    final failure = row['failure_class'];
    final code = row['status_code'];
    final retries = row['retry_count'];
    final created = _requiredText(row['created_at'], 80);
    if (_id(row['clinic_id']) != clinic ||
        (integrationId != null && integration != integrationId) ||
        !_operations.contains(row['operation']) ||
        !_submissionStatuses.contains(row['status']) ||
        (failure != null && !_failureClasses.contains(failure)) ||
        (code != null && (code is! int || code < 100 || code > 599)) ||
        retries is! int ||
        retries < 0 ||
        DateTime.tryParse(created) == null) {
      throw const FormatException('Invalid HIE submission metadata.');
    }
    // Intentionally never retain patient identifiers, request/response bodies,
    // external references, or provider error text (even from a legacy server).
    return HieSubmission._(
      _id(row['id']),
      integration,
      row['operation'] as String,
      row['status'] as String,
      failure as String?,
      code as int?,
      retries,
      created,
    );
  }
}

class HieSubmissionPage {
  const HieSubmissionPage(this.items, this.total, this.page, this.perPage);
  final List<HieSubmission> items;
  final int total;
  final int page;
  final int perPage;
  bool get hasNext => page * perPage < total;
}

/// Keep one instance per router lifetime when wiring navigation in Run B.
/// No token storage, new transport, queue, retry or external query method.
class HieRemoteDataSource {
  HieRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.guard,
  });
  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard guard;
  bool _inFlight = false;
  bool _uncertain = false;
  final Set<String> _confirmedCreates = <String>{};

  bool get writesBlocked => _uncertain || _inFlight;

  Future<AuthTokenResponse> _session(int revision, {bool admin = false}) async {
    if (!guard.canAccessProtectedRoutes || guard.revision != revision) {
      throw const HieUnavailable();
    }
    final state = await loadSession();
    final s = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        s == null ||
        s.userId <= 0 ||
        s.clinicContextId == null ||
        s.clinicContextId! <= 0 ||
        s.accessToken.isEmpty ||
        s.accessToken.trim() != s.accessToken ||
        !hieViewRoles.contains(s.role.toLowerCase()) ||
        (admin && s.role.toLowerCase() != 'admin') ||
        !guard.canAccessProtectedRoutes ||
        guard.revision != revision) {
      throw const HieUnavailable();
    }
    return s;
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.tenantId == b.tenantId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<void> _check(
    AuthTokenResponse before,
    int revision, {
    bool admin = false,
  }) async {
    if (!_same(before, await _session(revision, admin: admin))) {
      throw const HieUnavailable();
    }
  }

  Future<String> role() async =>
      (await _session(guard.revision)).role.toLowerCase();

  Map<String, dynamic> _data(Map<String, dynamic> envelope) {
    final data = envelope['data'];
    if (envelope['success'] != true || data is! Map<String, dynamic>) {
      throw const FormatException('Invalid HIE response.');
    }
    return data;
  }

  Future<HieIntegration> read(int id) async {
    _id(id);
    final rev = guard.revision;
    final s = await _session(rev);
    final raw = await apiClient.getJson(
      'api/v1/hie/integrations/$id',
      bearerToken: s.accessToken,
    );
    await _check(s, rev);
    return HieIntegration.parse(_data(raw), s.clinicContextId!, expectedId: id);
  }

  Future<HieSubmissionPage> submissions({
    int? integrationId,
    int page = 1,
    int perPage = 20,
  }) async {
    if (integrationId != null) _id(integrationId);
    if (page < 1 || perPage < 1 || perPage > 100) {
      throw const FormatException('Invalid HIE pagination.');
    }
    final rev = guard.revision;
    final s = await _session(rev);
    final path = Uri(
      path: 'api/v1/hie/submissions',
      queryParameters: {
        if (integrationId != null) 'integration_id': '$integrationId',
        'page': '$page',
        'per_page': '$perPage',
      },
    ).toString();
    final raw = await apiClient.getJson(path, bearerToken: s.accessToken);
    await _check(s, rev);
    final data = _data(raw);
    final rows = data['items'];
    final total = data['total'];
    if (data['page'] != page ||
        data['per_page'] != perPage ||
        rows is! List ||
        total is! int ||
        total < 0 ||
        rows.length > perPage ||
        rows.length > total) {
      throw const FormatException('Invalid HIE page.');
    }
    final ids = <int>{};
    final items = <HieSubmission>[];
    for (final row in rows) {
      if (row is! Map<String, dynamic>) {
        throw const FormatException('Invalid HIE row.');
      }
      final item = HieSubmission.parse(
        row,
        s.clinicContextId!,
        integrationId: integrationId,
      );
      if (!ids.add(item.id)) {
        throw const FormatException('Duplicate HIE submission.');
      }
      items.add(item);
    }
    return HieSubmissionPage(List.unmodifiable(items), total, page, perPage);
  }

  Future<HieIntegration> create(HieDraft draft) async {
    final body = draft.toJson();
    return _write(body: body);
  }

  Future<HieIntegration> update(
    int id, {
    HieDraft? draft,
    String? status,
  }) async {
    _id(id);
    if ((status != null &&
            !const {'pending', 'suspended', 'disabled'}.contains(status)) ||
        (draft == null && status == null)) {
      throw const HieUnavailable();
    }
    return _write(
      id: id,
      body: {if (draft != null) ...draft.toJson(), 'status': ?status},
    );
  }

  Future<HieIntegration> _write({
    int? id,
    required Map<String, dynamic> body,
  }) async {
    if (writesBlocked) throw const HieMutationUncertain();
    _inFlight = true;
    var sent = false;
    try {
      final rev = guard.revision;
      final s = await _session(rev, admin: true);
      final key =
          '${s.userId}:${s.tenantId}:${s.clinicContextId}:${jsonEncode(body)}';
      if (id == null && _confirmedCreates.contains(key)) {
        throw const HieUnavailable();
      }
      if (id != null) {
        final existing = await read(id);
        if (!existing.holdConfirmed ||
            (existing.status == 'active' &&
                !const {'suspended', 'disabled'}.contains(body['status']))) {
          throw const HieUnavailable();
        }
      }
      await _check(s, rev, admin: true);
      sent = true;
      final raw = id == null
          ? await apiClient.postJson(
              'api/v1/hie/integrations',
              body: body,
              bearerToken: s.accessToken,
            )
          : await apiClient.patchJson(
              'api/v1/hie/integrations/$id',
              body: body,
              bearerToken: s.accessToken,
            );
      await _check(s, rev, admin: true);
      final record = HieIntegration.parse(
        _data(raw),
        s.clinicContextId!,
        expectedId: id,
      );
      if (!record.holdConfirmed ||
          record.status == 'active' ||
          (id == null && record.status != 'pending') ||
          (body.containsKey('status') && record.status != body['status']) ||
          (body.containsKey('provider') &&
              record.provider != body['provider']) ||
          (body.containsKey('endpoint_url') &&
              !_sameEndpoint(
                record.endpoint,
                body['endpoint_url'] as String?,
              )) ||
          (body.containsKey('organization_id') &&
              record.organization != body['organization_id']) ||
          (body.containsKey('facility_id') &&
              record.facility != body['facility_id'])) {
        throw const FormatException('Unconfirmed HIE receipt.');
      }
      if (id == null) _confirmedCreates.add(key);
      return record;
    } on Object {
      // Conservative even on HTTP errors: no blind repeat of a sent mutation.
      if (sent) {
        _uncertain = true;
        throw const HieMutationUncertain();
      }
      rethrow;
    } finally {
      _inFlight = false;
    }
  }
}
