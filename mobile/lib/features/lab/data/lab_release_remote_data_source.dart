import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const labReleaseRoles = {'doctor', 'lab_technician', 'patient'};
const labWithdrawalReasons = {
  'clinical_correction',
  'identity_review',
  'other_safety_review',
};
const labRejectionReasons = {'incorrect_correction', 'insufficient_evidence'};
const labCommunicationMethods = {
  'in_person',
  'telephone',
  'secure_clinical_conversation',
};
const _flags = {'normal', 'abnormal', 'critical'};

class LabReleaseUnavailable implements Exception {
  const LabReleaseUnavailable();
}

class LabReleaseUncertain implements Exception {
  const LabReleaseUncertain();
}

Never _invalid() =>
    throw const FormatException('Invalid laboratory release response.');
int _integer(Object? value, {int min = 1, int max = 2147483647}) {
  if (value is! int || value < min || value > max) {
    _invalid();
  }
  return value;
}

String _text(Object? value, int max) {
  if (value is! String ||
      value.trim().isEmpty ||
      value.length > max ||
      RegExp(r'[\x00-\x1f\x7f]').hasMatch(value)) {
    _invalid();
  }
  return value;
}

String? _optional(Object? value, int max) =>
    value == null ? null : _text(value, max);
Map<String, dynamic> _map(Object? value) {
  if (value is! Map<String, dynamic>) {
    _invalid();
  }
  return value;
}

String _date(Object? value) {
  final s = _text(value, 64);
  if (!RegExp(r'(Z|[+-]\d\d:\d\d)$').hasMatch(s) ||
      DateTime.tryParse(s) == null) {
    _invalid();
  }
  return s;
}

String _choice(Object? v, Set<String> options) {
  if (v is! String || !options.contains(v)) {
    _invalid();
  }
  return v;
}

class LabReleasedReport {
  LabReleasedReport._(
    this.orderId,
    this.patientId,
    this.version,
    this.verifiedAt,
    this.completedAt,
    this.releasedAt,
    this.correctionVerifiedAt,
    this.results,
  );
  final int orderId, patientId, version;
  final String verifiedAt, completedAt;
  final String? releasedAt, correctionVerifiedAt;
  final List<Map<String, String?>> results;
  bool get critical => results.any((r) => r['flag'] == 'critical');
  static LabReleasedReport parse(
    Object? raw, {
    required int orderId,
    required int clinicId,
    required bool published,
  }) {
    final row = _map(raw);
    if (_integer(row['order_id']) != orderId ||
        _integer(row['clinic_id']) != clinicId) {
      _invalid();
    }
    final version = row.containsKey('record_version')
        ? _integer(row['record_version'])
        : 1;
    final verified = _date(row['verified_at']);
    final completed = _date(row['completed_at']);
    final corrected = row['correction_verified_at'] == null
        ? null
        : _date(row['correction_verified_at']);
    if ((version > 1) != (corrected != null) ||
        DateTime.parse(completed).isBefore(DateTime.parse(verified))) {
      _invalid();
    }
    final released = published ? _date(row['released_at']) : null;
    if (published &&
        (row['release_status'] != 'released' ||
            DateTime.parse(released!)
                .isBefore(DateTime.parse(corrected ?? completed)))) {
      _invalid();
    }
    final items = row['results'];
    if (items is! List || items.isEmpty || items.length > 1000) {
      _invalid();
    }
    final results = <Map<String, String?>>[];
    for (final item in items) {
      final r = _map(item);
      final flag = _choice(r['flag'], _flags);
      if (version > 1 && flag == 'critical') {
        _invalid();
      }
      final recorded = _date(r['resulted_at']);
      if (DateTime.parse(recorded).isAfter(DateTime.parse(verified))) {
        _invalid();
      }
      results.add(
        Map.unmodifiable(<String, String?>{
          'test_name': _text(r['test_name'], 150),
          'loinc_code': _optional(r['loinc_code'], 20),
          'unit': _optional(r['unit'], 30),
          'reference_range': _optional(r['reference_range'], 150),
          'result_value': _text(r['result_value'], 150),
          'flag': flag,
          'resulted_at': recorded,
        }),
      );
    }
    return LabReleasedReport._(
      orderId,
      _integer(row['patient_id']),
      version,
      verified,
      completed,
      released,
      corrected,
      List.unmodifiable(results),
    );
  }
}

class LabReleaseStatus {
  LabReleaseStatus._(
    this._owner,
    this.orderId,
    this.version,
    this.state,
    this.pending,
    this.rejected,
    this.items,
    this.patientId,
  );
  final LabReleaseSession _owner;
  final int orderId, version;
  final String state;
  final bool pending, rejected;
  final List<LabCorrectionItem> items;
  final int? patientId;
}

class LabCorrectionItem {
  const LabCorrectionItem(this.id, this.name, this.originalValue);
  final int id;
  final String name;
  final String? originalValue;
}

class LabCorrection {
  const LabCorrection({
    required this.itemId,
    required this.value,
    required this.flag,
  });
  final int itemId;
  final String value, flag;
}

enum LabPreviewKind { initial, critical, replacement }

class LabReleasePreview {
  LabReleasePreview._(this._owner, this.kind, this.report, this._digest);
  final LabReleaseSession _owner;
  final LabPreviewKind kind;
  final LabReleasedReport report;
  final String _digest;
  bool _consumed = false;
}

class LabReleaseSummary {
  const LabReleaseSummary(
    this.orderId,
    this.version,
    this.count,
    this.releasedAt,
  );
  final int orderId, version, count;
  final String releasedAt;
}

class LabReleasePage {
  LabReleasePage._(this.items, this.page, this.hasNext);
  final List<LabReleaseSummary> items;
  final int page;
  final bool hasNext;
}

class LabReleaseRemoteDataSource {
  LabReleaseRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.guard,
  });
  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard guard;
  bool _uncertain = false, _writing = false;
  bool get writesHeld => _uncertain || _writing;
  Future<AuthTokenResponse> _require(int revision) async {
    if (!guard.canAccessProtectedRoutes || guard.revision != revision) {
      throw const LabReleaseUnavailable();
    }
    final state = await loadSession();
    final s = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        s == null ||
        s.userId <= 0 ||
        (s.clinicContextId ?? 0) <= 0 ||
        s.accessToken.trim().isEmpty ||
        s.accessToken != s.accessToken.trim() ||
        !labReleaseRoles.contains(s.role) ||
        guard.revision != revision ||
        !guard.canAccessProtectedRoutes) {
      throw const LabReleaseUnavailable();
    }
    return s;
  }

  Future<LabReleaseSession> connect() async {
    final revision = guard.revision;
    return LabReleaseSession._(this, await _require(revision), revision);
  }
}

class LabReleaseSession {
  LabReleaseSession._(this._source, this._actor, this.revision);
  final LabReleaseRemoteDataSource _source;
  final AuthTokenResponse _actor;
  final int revision;
  String get role => _actor.role;
  int get clinicId => _actor.clinicContextId!;
  Future<void> check() async {
    final s = await _source._require(revision);
    if (s.userId != _actor.userId ||
        s.role != role ||
        s.clinicContextId != clinicId ||
        s.tenantId != _actor.tenantId ||
        s.accessToken != _actor.accessToken ||
        s.refreshToken != _actor.refreshToken) {
      throw const LabReleaseUnavailable();
    }
  }

  void _role(Set<String> allowed) {
    if (!allowed.contains(role)) {
      throw const LabReleaseUnavailable();
    }
  }

  String _order(int id, String suffix) =>
      'api/v1/lab/orders/${_integer(id)}/$suffix';
  Future<Map<String, dynamic>> _read(String path) async {
    await check();
    final raw = await _source.apiClient.getJson(
      path,
      bearerToken: _actor.accessToken,
    );
    await check();
    if (raw['success'] != true) {
      _invalid();
    }
    return _map(raw['data']);
  }

  Future<void> _write(
    String path,
    Map<String, dynamic> body,
    int orderId,
    String state, {
    int? version,
  }) async {
    await check();
    if (_source.writesHeld) {
      throw const LabReleaseUncertain();
    }
    _source._writing = true;
    try {
      final raw = await _source.apiClient.postJson(
        path,
        body: body,
        bearerToken: _actor.accessToken,
      );
      await check();
      if (raw['success'] != true) {
        _invalid();
      }
      final result = _map(raw['data']);
      if (_integer(result['order_id']) != orderId ||
          result['release_status'] != state ||
          (version != null && result['record_version'] != version)) {
        _invalid();
      }
      if (state == 'released') {
        _date(result['released_at']);
      }
    } on ApiException catch (e) {
      if (!const {400, 401, 403, 404, 409, 422}.contains(e.statusCode)) {
        _source._uncertain = true;
        throw const LabReleaseUncertain();
      }
      rethrow;
    } on Object {
      _source._uncertain = true;
      throw const LabReleaseUncertain();
    } finally {
      _source._writing = false;
    }
  }

  Future<LabReleaseStatus> status(int id) async {
    _role({'doctor', 'lab_technician'});
    final raw = await _read(_order(id, 'patient-release'));
    if (_integer(raw['order_id']) != id ||
        raw['correction_pending'] is! bool ||
        (raw.containsKey('correction_rejected') &&
            raw['correction_rejected'] is! bool)) {
      _invalid();
    }
    final v = _integer(raw['record_version'], min: 0);
    final state = _choice(raw['release_status'], {
      'unreleased',
      'released',
      'withdrawn',
    });
    final pending = raw['correction_pending'] == true,
        rejected = raw['correction_rejected'] == true;
    if ((state == 'unreleased') != (v == 0) ||
        ((pending || rejected) && (state != 'withdrawn' || v < 2)) ||
        (pending && rejected)) {
      _invalid();
    }
    final items = <LabCorrectionItem>[];
    int? patientId;
    if (role == 'lab_technician' && state == 'withdrawn' && !pending) {
      // Existing staff-only order detail; no patient-facing raw notes or URLs.
      final order = await _read('api/v1/lab/orders/$id');
      if (order['id'] != id ||
          order['clinic_id'] != clinicId ||
          order['status'] != 'completed') {
        _invalid();
      }
      patientId = _integer(order['patient_id']);
      final rows = order['items'];
      if (rows is! List || rows.isEmpty || rows.length > 1000) {
        _invalid();
      }
      final seen = <int>{};
      for (final r in rows) {
        final item = _map(r), test = _map(_map(r)['test']);
        final itemId = _integer(item['id']);
        if (!seen.add(itemId) ||
            item['order_id'] != id ||
            test['id'] != item['test_id'] ||
            (test['clinic_id'] != null && test['clinic_id'] != clinicId)) {
          _invalid();
        }
        items.add(
          LabCorrectionItem(
            itemId,
            _text(test['name'], 150),
            _optional(item['result_value'], 150),
          ),
        );
      }
    }
    return LabReleaseStatus._(
      this,
      id,
      v,
      state,
      pending,
      rejected,
      List.unmodifiable(items),
      patientId,
    );
  }

  Future<LabReleasePreview> preview(int id, LabPreviewKind kind) async {
    _role({'doctor'});
    final suffix = switch (kind) {
      LabPreviewKind.initial => 'patient-release-preview',
      LabPreviewKind.critical => 'patient-critical-release-preview',
      LabPreviewKind.replacement => 'patient-replacement-preview',
    };
    final raw = await _read(_order(id, suffix));
    final report = LabReleasedReport.parse(
      raw['report'],
      orderId: id,
      clinicId: clinicId,
      published: false,
    );
    final digest = _text(raw['preview_sha256'], 64);
    if (!RegExp(r'^[a-f0-9]{64}$').hasMatch(digest) ||
        (kind == LabPreviewKind.critical) != report.critical ||
        (kind == LabPreviewKind.replacement) != (report.version > 1) ||
        (kind == LabPreviewKind.critical &&
            raw['requires_direct_communication'] != true)) {
      _invalid();
    }
    return LabReleasePreview._(this, kind, report, digest);
  }

  void _consume(LabReleasePreview p) {
    _role({'doctor'});
    if (!identical(p._owner, this) || p._consumed) {
      throw const LabReleaseUnavailable();
    }
    p._consumed = true;
  }

  Future<void> publish(
    LabReleasePreview p, {
    required bool confirmed,
    String? communicationMethod,
  }) async {
    if (!confirmed ||
        (p.kind == LabPreviewKind.critical
            ? !labCommunicationMethods.contains(communicationMethod)
            : communicationMethod != null)) {
      throw const LabReleaseUnavailable();
    }
    _consume(p);
    final suffix = switch (p.kind) {
      LabPreviewKind.initial => 'patient-release',
      LabPreviewKind.critical => 'patient-critical-release',
      LabPreviewKind.replacement => 'patient-replacement',
    };
    await _write(
      _order(p.report.orderId, suffix),
      {
        'preview_sha256': p._digest,
        if (p.kind == LabPreviewKind.critical) ...{
          'communication_confirmed': true,
          'communication_method': communicationMethod,
        },
      },
      p.report.orderId,
      'released',
      version: p.kind == LabPreviewKind.replacement ? p.report.version : null,
    );
  }

  Future<void> reject(
    LabReleasePreview p,
    String reason, {
    required bool confirmed,
  }) async {
    if (!confirmed ||
        p.kind != LabPreviewKind.replacement ||
        !labRejectionReasons.contains(reason)) {
      throw const LabReleaseUnavailable();
    }
    _consume(p);
    await _write(
      _order(p.report.orderId, 'patient-replacement-rejection'),
      {'preview_sha256': p._digest, 'reason_code': reason},
      p.report.orderId,
      'withdrawn',
      version: p.report.version,
    );
  }

  Future<void> withdraw(
    LabReleaseStatus s,
    String reason, {
    required bool confirmed,
  }) async {
    _role({'doctor'});
    if (!confirmed ||
        !identical(s._owner, this) ||
        s.state != 'released' ||
        !labWithdrawalReasons.contains(reason)) {
      throw const LabReleaseUnavailable();
    }
    final fresh = await status(s.orderId);
    if (fresh.state != 'released' || fresh.version != s.version) {
      throw const LabReleaseUnavailable();
    }
    await _write(
      _order(s.orderId, 'patient-release-withdrawal'),
      {'reason_code': reason},
      s.orderId,
      'withdrawn',
      version: s.version > 1 ? s.version : null,
    );
  }

  Future<void> correct(
    LabReleaseStatus s,
    List<LabCorrection> changes,
    String reason, {
    required bool confirmed,
  }) async {
    _role({'lab_technician'});
    if (!confirmed ||
        !identical(s._owner, this) ||
        s.state != 'withdrawn' ||
        s.pending ||
        s.version < 1 ||
        changes.isEmpty ||
        changes.length > 100 ||
        reason.trim().length < 5 ||
        reason.trim().length > 1000) {
      throw const LabReleaseUnavailable();
    }
    final seen = <int>{};
    final body = <Map<String, dynamic>>[];
    for (final c in changes) {
      if (!s.items.any((i) => i.id == c.itemId) ||
          !seen.add(c.itemId) ||
          !const {'normal', 'abnormal'}.contains(c.flag)) {
        throw const LabReleaseUnavailable();
      }
      body.add({
        'item_id': c.itemId,
        'result_value': _text(c.value.trim(), 150),
        'flag': c.flag,
      });
    }
    await _write(
      _order(s.orderId, 'patient-release-correction'),
      {'expected_version': s.version, 'changes': body, 'reason': reason.trim()},
      s.orderId,
      'correction_pending',
      version: s.version + 1,
    );
  }

  Future<LabReleasePage> patientPage(int page) async {
    _role({'patient'});
    _integer(page, max: 10000);
    final raw = await _read('api/v1/lab/me/released-results?page=$page');
    final total = _integer(raw['total'], min: 0),
        pages = _integer(raw['pages'], min: 0);
    if (raw['page'] != page ||
        raw['per_page'] != 20 ||
        pages != (total + 19) ~/ 20 ||
        raw['has_next'] != (page < pages)) {
      _invalid();
    }
    final rows = raw['items'];
    if (rows is! List ||
        rows.length > 20 ||
        rows.length != (total - 20 * (page - 1)).clamp(0, 20)) {
      _invalid();
    }
    final items = <LabReleaseSummary>[];
    final seen = <int>{};
    for (final row in rows) {
      final r = _map(row);
      final id = _integer(r['order_id']);
      final released = _date(r['released_at']);
      if (!seen.add(id) ||
          r['release_status'] != 'released' ||
          DateTime.parse(released)
              .isBefore(DateTime.parse(_date(r['completed_at'])))) {
        _invalid();
      }
      items.add(
        LabReleaseSummary(
          id,
          _integer(r['record_version']),
          _integer(r['test_count']),
          released,
        ),
      );
    }
    return LabReleasePage._(List.unmodifiable(items), page, page < pages);
  }

  Future<LabReleasedReport> patientDetail(int orderId) async {
    _role({'patient'});
    final data = await _read(
      'api/v1/lab/me/released-results/${_integer(orderId)}',
    );
    return LabReleasedReport.parse(
      data,
      orderId: orderId,
      clinicId: clinicId,
      published: true,
    );
  }
}
