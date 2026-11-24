import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'rad7_ris_contract.dart';

class RadcUnavailable implements Exception {
  const RadcUnavailable();
}

class RadcUncertain implements Exception {
  const RadcUncertain();
}

Never _bad() =>
    throw const FormatException('Invalid staged radiology response.');
int _id(Object? v) => rad7Positive(v, 'identity');
Map<String, dynamic> _map(Object? v) => v is Map<String, dynamic> ? v : _bad();
String _digest(Object? v) =>
    v is String && RegExp(r'^[0-9a-f]{64}$').hasMatch(v) ? v : _bad();
String _text(Object? v) =>
    v is String &&
        v.startsWith('SYNTHETIC ') &&
        v.trim() == v &&
        v.length <= 12000 &&
        !RegExp(r'[^\x09\x0a\x20-\x7e]').hasMatch(v)
    ? v
    : _bad();
String _date(Object? v) =>
    v is String &&
        v.length <= 64 &&
        RegExp(r'(Z|[+-]\d\d:\d\d)$').hasMatch(v) &&
        DateTime.tryParse(v) != null
    ? v
    : _bad();
bool _bool(Object? v) => v is bool ? v : _bad();
String _uid(Object? v) =>
    v is String &&
        v.length <= 64 &&
        RegExp(r'^(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))+$').hasMatch(v)
    ? v
    : _bad();
Map<String, dynamic> _staged(Map<String, dynamic> raw) {
  if (raw['success'] != true ||
      raw['synthetic_only'] != true ||
      raw['diagnostic_report_signed'] != false ||
      raw['released_to_chart'] != false) {
    _bad();
  }
  final d = _map(raw['data']);
  if (d['synthetic_only'] != true ||
      (d.containsKey('diagnostic_report_signed') &&
          d['diagnostic_report_signed'] != false) ||
      (d.containsKey('released_to_chart') && d['released_to_chart'] != false) ||
      (d.containsKey('archive_verified') && d['archive_verified'] != false)) {
    _bad();
  }
  return d;
}

class RadcWorklist {
  RadcWorklist._(
    this._owner,
    this.examId,
    this.examVersion,
    this.state,
    this.publicationId,
  );
  final RadcSession _owner;
  final int examId, examVersion;
  final String state;
  final int? publicationId;
  bool get current => state == 'local_published';
}

class RadcReport {
  RadcReport._(
    this._owner,
    this.examId,
    this.reportId,
    this.version,
    this.text,
    this.released,
    this.finalizedAt,
    this.releasedAt,
    this.publicationId,
  );
  final RadcSession _owner;
  final int examId, reportId, version;
  final String text, finalizedAt;
  final bool released;
  final String? releasedAt;
  final int? publicationId;
}

class RadcPreview {
  RadcPreview._(
    this._owner,
    this.examId,
    this.publicationId,
    this.version,
    this.text,
    this._hash,
    this.copy,
  );
  final RadcSession _owner;
  final int examId, publicationId, version;
  final String text, _hash;
  final bool copy;
  bool _used = false;
}

class RadcSummary {
  const RadcSummary(this.examId, this.version, this.releasedAt);
  final int examId, version;
  final String releasedAt;
}

class RadcViewer {
  const RadcViewer(this.studyUid, this.instanceCount);
  final String studyUid;
  final int instanceCount;
}

class RadcSource {
  RadcSource({
    required this.apiClient,
    required this.guard,
    required this.loadSession,
    required this.connectivity,
  });
  final ApiClient apiClient;
  final SessionNavigationGuard guard;
  final Future<AuthSessionState> Function() loadSession;
  final DeviceConnectivitySource connectivity;
  final Set<String> _attempts = {};
  bool _uncertain = false, _writing = false;
  bool get writesHeld => _uncertain || _writing;
  Future<AuthTokenResponse> _ready(int rev) async {
    if (!guard.canAccessProtectedRoutes || guard.revision != rev) {
      throw const RadcUnavailable();
    }
    final state = await loadSession();
    final a = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        a == null ||
        a.userId <= 0 ||
        (a.clinicContextId ?? 0) <= 0 ||
        !const {'doctor', 'patient'}.contains(a.role) ||
        a.accessToken.trim().isEmpty ||
        a.accessToken.trim() != a.accessToken ||
        guard.revision != rev ||
        !guard.canAccessProtectedRoutes) {
      throw const RadcUnavailable();
    }
    return a;
  }

  Future<RadcSession> connect() async {
    final rev = guard.revision;
    return RadcSession._(this, await _ready(rev), rev);
  }
}

class RadcSession {
  RadcSession._(this._source, this._actor, this.revision);
  final RadcSource _source;
  final AuthTokenResponse _actor;
  final int revision;
  String get role => _actor.role;
  bool matches(AuthTokenResponse a) =>
      a.userId == _actor.userId &&
      a.role == role &&
      a.clinicContextId == _actor.clinicContextId &&
      a.tenantId == _actor.tenantId &&
      a.accessToken == _actor.accessToken &&
      a.refreshToken == _actor.refreshToken;
  Future<void> check() async {
    if (!matches(await _source._ready(revision))) {
      throw const RadcUnavailable();
    }
  }

  void _role(String r) {
    if (role != r) {
      throw const RadcUnavailable();
    }
  }

  String _exam(int id) => 'api/v1/radiology/exams/${_id(id)}';
  String _report(int e, int p) => '${_exam(e)}/staged-report/${_id(p)}';
  Future<Map<String, dynamic>> _read(String path) async {
    await check();
    final raw = await _source.apiClient.getJson(
      path,
      bearerToken: _actor.accessToken,
    );
    await check();
    return raw;
  }

  Future<T> _write<T>(
    String path,
    Map<String, dynamic> body,
    String key,
    T Function(Map<String, dynamic>) parse,
  ) async {
    _role('doctor');
    await check();
    if (await _source.connectivity.checkStatus() !=
        DeviceNetworkStatus.connected) {
      throw const RadcUnavailable();
    }
    await check();
    if (_source.writesHeld ||
        !_source._attempts.add(
          '${_actor.userId}/${_actor.clinicContextId}/${_actor.tenantId}/$key',
        )) {
      throw const RadcUncertain();
    }
    _source._writing = true;
    try {
      final raw = await _source.apiClient.postJson(
        path,
        body: body,
        bearerToken: _actor.accessToken,
      );
      await check();
      return parse(raw);
    } on Object {
      _source._uncertain = true;
      throw const RadcUncertain();
    } finally {
      _source._writing = false;
    }
  }

  RadcWorklist _work(Map<String, dynamic> raw, int e) {
    final d = rad7Envelope(raw);
    final v = _id(d['exam_version']);
    _id(d['order_version']);
    final state = d['local_state'];
    final p = d['publication_id'];
    if (_id(d['exam_id']) != e ||
        state is! String ||
        !const {
          'not_published',
          'local_published',
          'superseded',
          'cancelled_or_inactive',
        }.contains(state) ||
        d['external_publication_confirmed'] != false ||
        d['external_delivery'] != 'held' ||
        d['archive_verified'] != false ||
        d['diagnostic_report_signed'] != false) {
      _bad();
    }
    if (state == 'not_published') {
      if (p != null ||
          d['published_exam_version'] != null ||
          d['payload_sha256'] != null) {
        _bad();
      }
    } else {
      _id(p);
      _digest(d['payload_sha256']);
      final pv = _id(d['published_exam_version']);
      if (pv > v || (state == 'local_published' && pv != v)) {
        _bad();
      }
    }
    return RadcWorklist._(this, e, v, state, p as int?);
  }

  Future<RadcWorklist> worklist(int e) async {
    _role('doctor');
    return _work(await _read('${_exam(e)}/clinical-worklist'), e);
  }

  Future<RadcWorklist> publish(RadcWorklist w) async {
    _role('doctor');
    if (!identical(w._owner, this) ||
        !const {'not_published', 'superseded'}.contains(w.state)) {
      throw const RadcUnavailable();
    }
    return _write(
      '${_exam(w.examId)}/clinical-worklist',
      {'expected_version': w.examVersion},
      'work/${w.examId}/${w.examVersion}',
      (raw) {
        final r = _work(raw, w.examId);
        if (!r.current || r.examVersion != w.examVersion) {
          _bad();
        }
        return r;
      },
    );
  }

  Future<String> acquire(int e, int p, {required bool ct}) async {
    _role('doctor');
    _id(p);
    return _write(
      '${_exam(e)}/${ct ? 'staged-ct' : 'staged-study'}',
      {'publication_id': p},
      'acq/$e/$p',
      (raw) {
        final d = rad7Envelope(raw);
        final state = d['state'];
        if (_id(d['exam_id']) != e ||
            _id(d['publication_id']) != p ||
            state is! String ||
            !const {'staged_validated', 'quarantined'}.contains(state) ||
            d['archive_verified'] != false ||
            d['diagnostic_report_signed'] != false ||
            d['ohif_browser_authorized'] != false) {
          _bad();
        }
        if (ct) {
          final count = d['instance_count'];
          if (count is! int ||
              count < 0 ||
              count > 16 ||
              (state == 'staged_validated') != (count > 0)) {
            _bad();
          }
          if (count > 0) {
            _uid(d['study_instance_uid']);
          } else if (d['study_instance_uid'] != null) {
            _bad();
          }
        } else if (d['staged_instance_available'] !=
            (state == 'staged_validated')) {
          _bad();
        }
        return state;
      },
    );
  }

  Future<RadcViewer> viewer(int e, int p) async {
    _role('doctor');
    final root = '${_exam(e)}/staged-ct/${_id(p)}/viewer';
    final d = await _read('$root/descriptor');
    final count = _id(d['instance_count']);
    if (count > 16 ||
        d['dicomweb_root'] != '/$root' ||
        d['authorization'] != 'bearer_per_request' ||
        d['browser_handoff_authorized'] != false ||
        d['diagnostic_use_authorized'] != false) {
      _bad();
    }
    return RadcViewer(_uid(d['study_instance_uid']), count);
  }

  RadcReport _parse(
    Map<String, dynamic> d,
    int e, {
    bool patient = false,
    int? publicationId,
  }) {
    if (_id(d['exam_id']) != e ||
        d['staged_finalized'] != true ||
        d['modality'] != 'ct' ||
        d['diagnostic_report_signed'] != false ||
        d['archive_verified'] != false ||
        d['released_to_chart'] != false) {
      _bad();
    }
    final released = _bool(d['staged_patient_released']);
    if (patient && !released) {
      _bad();
    }
    return RadcReport._(
      this,
      e,
      _id(d['report_id']),
      _id(d['record_version']),
      _text(d['report_text']),
      released,
      _date(d['finalized_at']),
      patient ? _date(d['released_at']) : null,
      publicationId,
    );
  }

  Future<RadcReport> status(int e, int p) async {
    _role('doctor');
    return _parse(_staged(await _read(_report(e, p))), e, publicationId: p);
  }

  Future<RadcPreview> preview(int e, int p, {required bool copy}) async {
    _role('doctor');
    final d = _staged(
      await _read('${_report(e, p)}/${copy ? 'copy-preview' : 'preview'}'),
    );
    if (copy && _parse(d, e, publicationId: p).released) {
      _bad();
    }
    if (d['diagnostic_report_signed'] != false) {
      _bad();
    }
    return RadcPreview._(
      this,
      e,
      p,
      _id(d[copy ? 'record_version' : 'draft_revision']),
      _text(d['report_text']),
      _digest(d['preview_sha256']),
      copy,
    );
  }

  Future<RadcReport> approve(RadcPreview v) async {
    _role('doctor');
    if (!identical(v._owner, this) || v._used) {
      throw const RadcUnavailable();
    }
    v._used = true;
    return _write(
      '${_report(v.examId, v.publicationId)}/${v.copy ? 'copy' : 'freeze'}',
      {'preview_sha256': v._hash, if (!v.copy) 'expected_version': v.version},
      '${v.copy ? 'copy' : 'freeze'}/${v.examId}/${v.publicationId}/${v.version}',
      (raw) {
        final r = _parse(
          _staged(raw),
          v.examId,
          publicationId: v.publicationId,
        );
        if (r.text != v.text ||
            r.version != (v.copy ? v.version : 1) ||
            r.released != v.copy) {
          _bad();
        }
        return r;
      },
    );
  }

  Future<RadcReport> amend(
    RadcReport r,
    int p,
    String text,
    String reason,
  ) async {
    _role('doctor');
    if (!identical(r._owner, this) ||
        r.publicationId != p ||
        r.released ||
        text == r.text ||
        reason.trim().length < 5 ||
        reason.length > 1000) {
      throw const RadcUnavailable();
    }
    _text(text);
    return _write(
      '${_report(r.examId, p)}/amend',
      {
        'expected_version': r.version,
        'report_text': text,
        'reason': reason.trim(),
      },
      'amend/${r.examId}/$p/${r.version}',
      (raw) {
        final n = _parse(_staged(raw), r.examId, publicationId: p);
        if (n.reportId != r.reportId ||
            n.version != r.version + 1 ||
            n.released ||
            n.text != text) {
          _bad();
        }
        return n;
      },
    );
  }

  Future<void> withdraw(int e, int p, int version) async {
    _role('doctor');
    _id(version);
    await _write(
      '${_report(e, p)}/withdraw',
      {'expected_version': version},
      'withdraw/$e/$p/$version',
      (raw) {
        final d = _staged(raw);
        if (_id(d['exam_id']) != e ||
            _id(d['record_version']) != version ||
            d['staged_patient_released'] != false ||
            d['diagnostic_report_signed'] != false ||
            d['released_to_chart'] != false) {
          _bad();
        }
        return true;
      },
    );
  }

  Future<List<RadcSummary>> patientList() async {
    _role('patient');
    final d = _staged(await _read('api/v1/radiology/me/staged-reports'));
    final rows = d['items'];
    if (rows is! List || rows.length > 50) {
      _bad();
    }
    final out = <RadcSummary>[];
    final seen = <int>{};
    for (final v in rows) {
      final r = _map(v);
      final e = _id(r['exam_id']);
      if (r['synthetic_only'] != true || !seen.add(e)) {
        _bad();
      }
      out.add(
        RadcSummary(e, _id(r['record_version']), _date(r['released_at'])),
      );
    }
    return List.unmodifiable(out);
  }

  Future<RadcReport> patientDetail(int e) async {
    _role('patient');
    return _parse(
      _staged(await _read('api/v1/radiology/me/staged-reports/${_id(e)}')),
      e,
      patient: true,
    );
  }
}
