import 'package:flutter/foundation.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

const problemStatuses = [
  'active',
  'inactive',
  'resolved',
  'remission',
  'recurrence',
  'relapse',
];
const verificationStatuses = [
  'unconfirmed',
  'provisional',
  'differential',
  'confirmed',
  'refuted',
  'entered_in_error',
];
const problemCategories = ['acute', 'chronic', 'symptom', 'other'];
const problemSeverities = ['mild', 'moderate', 'severe', 'unknown'];
const problemCodingSystems = [
  'http://hl7.org/fhir/sid/icd-10',
  'http://hl7.org/fhir/sid/icd-10-cm',
  'http://snomed.info/sct',
];
const currentProblemStatuses = {'active', 'recurrence', 'relapse'};
const terminalProblemVerifications = {'refuted', 'entered_in_error'};
const problemTransitions = <String, Set<String>>{
  'active': {'inactive', 'resolved', 'remission'},
  'inactive': {'active', 'resolved', 'recurrence'},
  'resolved': {'recurrence'},
  'remission': {'resolved', 'relapse', 'inactive'},
  'recurrence': {'inactive', 'resolved', 'remission'},
  'relapse': {'inactive', 'resolved', 'remission'},
};

Never _bad() => throw const FormatException('Invalid structured problem data.');
Map<String, dynamic> _object(Object? v) =>
    v is Map<String, dynamic> ? v : _bad();
List<dynamic> _array(Object? v) => v is List<dynamic> ? v : _bad();
int _id(Object? v) => v is int && v > 0 && v <= 2147483647 ? v : _bad();
String _string(Object? v, int max, {bool empty = false}) =>
    v is String && (empty || v.isNotEmpty) && v.trim() == v && v.length <= max
    ? v
    : _bad();
String? _optional(Object? v, int max, {bool empty = false}) =>
    v == null ? null : _string(v, max, empty: empty);
String _choice(Object? v, List<String> choices) =>
    v is String && choices.contains(v) ? v : _bad();
String? _date(Object? v) {
  if (v == null) {
    return null;
  }
  final s = _string(v, 10);
  if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(s)) {
    _bad();
  }
  final d = DateTime.tryParse(s);
  if (d == null || d.year < 1 || d.toIso8601String().substring(0, 10) != s) {
    _bad();
  }
  return s;
}

String _stamp(Object? v) {
  final s = _string(v, 64);
  if (!RegExp(r'(Z|[+-]\d\d:\d\d)$').hasMatch(s) ||
      DateTime.tryParse(s) == null) {
    _bad();
  }
  return s;
}

String? _optionalStamp(Object? v) => v == null ? null : _stamp(v);
String _uuid(Object? v) {
  final s = _string(v, 36);
  if (!RegExp(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')
      .hasMatch(s)) {
    _bad();
  }
  return s.toLowerCase();
}

class ProblemContent {
  ProblemContent.fromJson(Map<String, dynamic> json)
    : name = _string(json['name'], 255),
      code = _optional(json['code'], 64),
      codingSystem = json['coding_system'] == null
          ? null
          : _choice(json['coding_system'], problemCodingSystems),
      category = _choice(json['category'], problemCategories),
      clinicalStatus = _choice(json['clinical_status'], problemStatuses),
      verificationStatus = _choice(
        json['verification_status'],
        verificationStatuses,
      ),
      severity = json['severity'] == null
          ? null
          : _choice(json['severity'], problemSeverities),
      onsetDate = _date(json['onset_date']),
      onsetDescription = _optional(json['onset_description'], 255),
      resolutionDate = _date(json['resolution_date']),
      notes = _optional(json['notes'], 4000, empty: true) {
    if ((code == null) != (codingSystem == null) ||
        (code != null &&
            !RegExp(r'^[A-Za-z0-9][A-Za-z0-9.\-]*$').hasMatch(code!)) ||
        (codingSystem == problemCodingSystems.last &&
            !RegExp(r'^\d+$').hasMatch(code!)) ||
        ((clinicalStatus == 'resolved') != (resolutionDate != null)) ||
        (onsetDate != null &&
            resolutionDate != null &&
            onsetDate!.compareTo(resolutionDate!) > 0)) {
      _bad();
    }
  }
  final String name, category, clinicalStatus, verificationStatus;
  final String? code,
      codingSystem,
      severity,
      onsetDate,
      onsetDescription,
      resolutionDate,
      notes;
  bool get terminal =>
      terminalProblemVerifications.contains(verificationStatus);
  bool get current =>
      currentProblemStatuses.contains(clinicalStatus) && !terminal;
  Map<String, dynamic> toJson() => {
    'name': name,
    'code': codingSystem == problemCodingSystems.last
        ? code
        : code?.toUpperCase(),
    'coding_system': codingSystem,
    'category': category,
    'clinical_status': clinicalStatus,
    'verification_status': verificationStatus,
    'severity': severity,
    'onset_date': onsetDate,
    'onset_description': onsetDescription,
    'resolution_date': resolutionDate,
    'notes': notes,
  };
}

class ProblemRecord {
  ProblemRecord._(
    this._session,
    Map<String, dynamic> json,
    int patient, {
    int? expectedId,
  }) : id = _id(json['id']),
       patientId = _id(json['patient_id']),
       clinicId = _id(json['clinic_id']),
       organizationId = _uuid(json['organization_id']),
       version = _id(json['version']),
       content = ProblemContent.fromJson(_object(json['content'])),
       consultationIds = List<int>.unmodifiable(
         _array(json['consultation_ids']).map(_id),
       ),
       recordedBy = _id(json['recorded_by_staff_id']),
       recordedAt = _stamp(json['recorded_at']),
       reviewedBy = _id(json['reviewed_by_staff_id']),
       reviewedAt = _stamp(json['reviewed_at']),
       confirmedBy = json['confirmed_by_staff_id'] == null
           ? null
           : _id(json['confirmed_by_staff_id']),
       confirmedAt = _optionalStamp(json['confirmed_at']) {
    if (patientId != patient ||
        clinicId != _session._auth.clinicContextId ||
        organizationId != _session._auth.tenantId!.toLowerCase() ||
        (expectedId != null && expectedId != id) ||
        consultationIds.toSet().length != consultationIds.length ||
        ((confirmedBy != null) != (confirmedAt != null)) ||
        ((confirmedBy != null) !=
            (content.verificationStatus == 'confirmed'))) {
      _bad();
    }
  }
  final ProblemSession _session;
  final int id, patientId, clinicId, version, recordedBy, reviewedBy;
  final String organizationId, recordedAt, reviewedAt;
  final int? confirmedBy;
  final String? confirmedAt;
  final ProblemContent content;
  final List<int> consultationIds;
}

class ProblemPage {
  const ProblemPage(this.items, this.nextAfter);
  final List<ProblemRecord> items;
  final int? nextAfter;
}

class ProblemAmendment {
  const ProblemAmendment(
    this.snapshot,
    this.reason,
    this.actor,
    this.at,
    this.sha256,
  );
  final ProblemRecord snapshot;
  final String reason, at, sha256;
  final int actor;
}

class ProblemHistory {
  const ProblemHistory(this.original, this.amendments);
  final ProblemRecord original;
  final List<ProblemAmendment> amendments;
}

class ProblemCondition {
  ProblemCondition._(Map<String, dynamic> j)
    : id = _id(j['problem_id']),
      version = _id(j['version']),
      name = _string(j['name'], 255),
      code = _optional(j['code'], 64),
      codingSystem = j['coding_system'] == null
          ? null
          : _choice(j['coding_system'], problemCodingSystems),
      clinicalStatus = _choice(j['clinical_status'], problemStatuses) {
    if (j['verification_status'] != 'confirmed' ||
        !currentProblemStatuses.contains(clinicalStatus) ||
        (code == null) != (codingSystem == null)) {
      _bad();
    }
  }
  final int id, version;
  final String name, clinicalStatus;
  final String? code, codingSystem;
}

class ProblemReview {
  const ProblemReview(this.evaluatedAt, this.conditions);
  final String evaluatedAt;
  final List<ProblemCondition> conditions;
}

class ProblemUnavailable implements Exception {
  const ProblemUnavailable();
}

class ProblemOutcomeUnconfirmed implements Exception {
  const ProblemOutcomeUnconfirmed();
}

/// One online-only adapter per router lifetime. No durable PHI, queue or retry.
class ProblemSource {
  ProblemSource({
    required this.apiClient,
    required this.guard,
    required this.loadSession,
    required this.connectivity,
  });
  final ApiClient apiClient;
  final SessionNavigationGuard guard;
  final Future<AuthSessionState> Function() loadSession;
  final DeviceConnectivitySource connectivity;
  final Expando<bool> _used = Expando<bool>();
  bool _writing = false, _uncertain = false;
  bool get writesHeld => _writing || _uncertain;
  bool get outcomeUnconfirmed => _uncertain;

  Future<ProblemSession> open({bool medicationReview = false}) async {
    final revision = guard.revision;
    final auth = await _ready(revision, medicationReview: medicationReview);
    return ProblemSession._(this, auth, revision, medicationReview);
  }

  Future<AuthTokenResponse> _ready(
    int revision, {
    required bool medicationReview,
  }) async {
    if (!guard.canAccessProtectedRoutes || guard.revision != revision) {
      throw const ProblemUnavailable();
    }
    final state = await loadSession();
    final a = state.session;
    if (!guard.canAccessProtectedRoutes ||
        guard.revision != revision ||
        state.disposition != AuthSessionDisposition.ready ||
        a == null ||
        a.userId <= 0 ||
        (a.clinicContextId ?? 0) <= 0 ||
        a.tenantId == null ||
        a.accessToken.isEmpty ||
        a.accessToken.trim() != a.accessToken ||
        a.refreshToken.isEmpty ||
        a.refreshToken.trim() != a.refreshToken ||
        !(medicationReview
                ? const {'doctor', 'nurse', 'pharmacist'}
                : const {'doctor', 'nurse'})
            .contains(a.role.trim().toLowerCase())) {
      throw const ProblemUnavailable();
    }
    _uuid(a.tenantId);
    return a;
  }

  Future<void> _check(ProblemSession s) async {
    if (!identical(s._source, this) || !s.current) {
      throw const ProblemUnavailable();
    }
    final a = await _ready(s._revision, medicationReview: s._medicationReview);
    final b = s._auth;
    if (a.userId != b.userId ||
        a.clinicContextId != b.clinicContextId ||
        a.tenantId != b.tenantId ||
        a.role != b.role ||
        a.accessToken != b.accessToken ||
        a.refreshToken != b.refreshToken) {
      throw const ProblemUnavailable();
    }
  }

  Future<T> _read<T>(
    ProblemSession s,
    String path,
    T Function(Map<String, dynamic>) decode,
  ) async {
    await _check(s);
    final r = await apiClient.getJson(path, bearerToken: s._auth.accessToken);
    await _check(s);
    if (r['success'] != true) {
      _bad();
    }
    return decode(_object(r['data']));
  }

  Future<ProblemRecord> _write(
    ProblemSession s,
    int patient,
    String path,
    Map<String, dynamic> body, {
    required bool create,
    required ProblemRecord Function(Map<String, dynamic>) decode,
  }) async {
    _id(patient);
    if (!s.canWrite || writesHeld) {
      throw const ProblemUnavailable();
    }
    _writing = true;
    var sent = false;
    try {
      await _check(s);
      if (await connectivity.checkStatus() != DeviceNetworkStatus.connected) {
        throw const ProblemUnavailable();
      }
      await _check(s);
      sent = true;
      final raw = create
          ? await apiClient.postJson(
              path,
              body: body,
              bearerToken: s._auth.accessToken,
            )
          : await apiClient.patchJson(
              path,
              body: body,
              bearerToken: s._auth.accessToken,
            );
      await _check(s);
      if (raw['success'] != true) {
        _bad();
      }
      return decode(_object(raw['data']));
    } on Object catch (e) {
      if (sent &&
          !(e is ApiException &&
              e.kind == ApiFailureKind.httpStatus &&
              const {400, 401, 403, 404, 409, 422}.contains(e.statusCode))) {
        _uncertain = true;
        throw const ProblemOutcomeUnconfirmed();
      }
      rethrow;
    } finally {
      _writing = false;
    }
  }
}

class ProblemSession {
  const ProblemSession._(
    this._source,
    this._auth,
    this._revision,
    this._medicationReview,
  );
  final ProblemSource _source;
  final AuthTokenResponse _auth;
  final int _revision;
  final bool _medicationReview;
  bool get current =>
      _source.guard.canAccessProtectedRoutes &&
      _source.guard.revision == _revision;
  bool get canWrite =>
      !_medicationReview &&
      current &&
      _auth.role.trim().toLowerCase() == 'doctor';
  void _general() {
    if (_medicationReview || !current) {
      throw const ProblemUnavailable();
    }
  }

  String _root(int patient) => 'api/v1/patients/${_id(patient)}/problems';
  Future<ProblemPage> list(
    int patient, {
    String filter = 'current',
    int after = 0,
    int limit = 50,
  }) async {
    _general();
    if (!const {'all', 'current', 'historical', 'resolved'}.contains(filter) ||
        after < 0 ||
        limit < 1 ||
        limit > 100) {
      _bad();
    }
    final path = Uri(
      path: _root(patient),
      queryParameters: {'status': filter, 'after': '$after', 'limit': '$limit'},
    ).toString();
    return _source._read(this, path, (j) {
      final rows = _array(j['items'])
          .map((r) => ProblemRecord._(this, _object(r), patient))
          .toList();
      var last = after;
      for (final row in rows) {
        final c = row.content;
        if (row.id <= last ||
            (filter == 'current' && !c.current) ||
            (filter == 'historical' && c.current) ||
            (filter == 'resolved' &&
                (c.clinicalStatus != 'resolved' || c.terminal))) {
          _bad();
        }
        last = row.id;
      }
      final next = j['next_after'] == null ? null : _id(j['next_after']);
      if (rows.length > limit ||
          (next != null && (rows.length != limit || next != last))) {
        _bad();
      }
      return ProblemPage(List.unmodifiable(rows), next);
    });
  }

  Future<ProblemRecord> detail(int patient, int id) async {
    _general();
    return _source._read(
      this,
      '${_root(patient)}/${_id(id)}',
      (j) => ProblemRecord._(this, j, patient, expectedId: id),
    );
  }

  Future<String> localCondition(ProblemRecord prior) async {
    _general();
    if (!identical(prior._session, this)) {
      _bad();
    }
    final fresh = await detail(prior.patientId, prior.id);
    if (fresh.version != prior.version) {
      _bad();
    }
    return _source._read(this, '${_root(prior.patientId)}/${prior.id}/fhir-condition', (j) {
      if (j['resourceType'] != 'Condition' || j['id'] != '${prior.id}' ||
          _object(j['meta'])['versionId'] != '${prior.version}' ||
          _object(j['subject'])['reference'] != 'Patient/${prior.patientId}' ||
          _object(j['code'])['text'] != prior.content.name) {
        _bad();
      }
      final verification = _array(_object(j['verificationStatus'])['coding']);
      if (verification.length != 1 || _object(verification.single)['system'] != 'http://terminology.hl7.org/CodeSystem/condition-ver-status' ||
          _object(verification.single)['code'] != prior.content.verificationStatus.replaceAll('_', '-')) {
        _bad();
      }
      if (prior.content.verificationStatus != 'entered_in_error') {
        final clinical = _array(_object(j['clinicalStatus'])['coding']);
        if (clinical.length != 1 || _object(clinical.single)['system'] != 'http://terminology.hl7.org/CodeSystem/condition-clinical' ||
            _object(clinical.single)['code'] != prior.content.clinicalStatus) {
          _bad();
        }
      } else if (j.containsKey('clinicalStatus')) {
        _bad();
      }
      final coding = _object(j['code'])['coding'];
      if (prior.content.code != null) {
        final entries = _array(coding);
        if (entries.length != 1 || _object(entries.single)['code'] != prior.content.code ||
            _object(entries.single)['system'] != prior.content.codingSystem) {
          _bad();
        }
      } else if (coding != null) {
        _bad();
      }
      return 'Local FHIR R4 Condition #${prior.id}, version ${prior.version}\nSubject: Patient/${prior.patientId}\nCode: ${prior.content.name}${prior.content.code == null ? '' : ' (${prior.content.code})'}\nVerification: ${prior.content.verificationStatus}\nNo external transmission or import.';
    });
  }

  Future<ProblemHistory> history(ProblemRecord prior) async {
    _general();
    if (!identical(prior._session, this)) {
      _bad();
    }
    return _source._read(
      this,
      '${_root(prior.patientId)}/${prior.id}/history',
      (j) {
        final original = ProblemRecord._(
          this,
          _object(j['original']),
          prior.patientId,
          expectedId: prior.id,
        );
        if (original.version != 1) {
          _bad();
        }
        var version = 1;
        var latest = original;
        final changes = <ProblemAmendment>[];
        for (final row in _array(j['amendments'])) {
          final a = _object(row);
          final snap = ProblemRecord._(
            this,
            _object(a['content']),
            prior.patientId,
            expectedId: prior.id,
          );
          final hash = _string(a['sha256'], 64);
          if (_id(a['version']) != ++version ||
              snap.version != version ||
              !RegExp(r'^[0-9a-f]{64}$').hasMatch(hash)) {
            _bad();
          }
          changes.add(
            ProblemAmendment(
              snap,
              _string(a['reason'], 1000),
              _id(a['actor_staff_id']),
              _stamp(a['amended_at']),
              hash,
            ),
          );
          latest = snap;
        }
        if (latest.version != prior.version ||
            !mapEquals(latest.content.toJson(), prior.content.toJson()) ||
            !listEquals(latest.consultationIds, prior.consultationIds)) {
          _bad();
        }
        return ProblemHistory(original, List.unmodifiable(changes));
      },
    );
  }

  Future<ProblemRecord> create(
    int patient,
    ProblemContent content, {
    int? consultationId,
  }) async {
    _general();
    if (content.terminal) {
      _bad();
    }
    final body = content.toJson();
    if (consultationId != null) {
      body['consultation_id'] = _id(consultationId);
    }
    return _source._write(
      this,
      patient,
      _root(patient),
      body,
      create: true,
      decode: (j) {
        final r = ProblemRecord._(this, j, patient);
        if (r.version != 1 ||
            !mapEquals(r.content.toJson(), content.toJson()) ||
            !listEquals(r.consultationIds, [
              ?consultationId,
            ])) {
          _bad();
        }
        return r;
      },
    );
  }

  Future<ProblemRecord> change(
    ProblemRecord prior,
    String action,
    String reason, {
    ProblemContent? content,
    int? consultationId,
  }) async {
    _general();
    if (!identical(prior._session, this) ||
        prior.content.terminal ||
        !const {
          'review',
          'associate',
          'update',
          'transition',
          'correct',
        }.contains(action)) {
      _bad();
    }
    final body = <String, dynamic>{
      'expected_version': prior.version,
      'action': action,
      'reason': _string(reason.trim(), 1000),
    };
    final expectedLinks = [...prior.consultationIds];
    if (action == 'associate') {
      if (content != null ||
          consultationId == null ||
          expectedLinks.contains(consultationId)) {
        _bad();
      }
      body['consultation_id'] = _id(consultationId);
      expectedLinks.add(consultationId);
    } else if (action == 'review') {
      if (content != null || consultationId != null) {
        _bad();
      }
    } else {
      if (content == null || consultationId != null) {
        _bad();
      }
      final old = prior.content.toJson();
      final next = content.toJson();
      if (mapEquals(old, next)) {
        _bad();
      }
      if (action == 'update' &&
          [
            'name',
            'code',
            'coding_system',
            'clinical_status',
            'verification_status',
          ].any((k) => old[k] != next[k])) {
        _bad();
      }
      if (action == 'transition' &&
          (old.keys.any(
                (k) =>
                    !const {
                      'clinical_status',
                      'verification_status',
                      'resolution_date',
                    }.contains(k) &&
                    old[k] != next[k],
              ) ||
              content.verificationStatus == 'entered_in_error' ||
              (prior.content.clinicalStatus != content.clinicalStatus &&
                  !problemTransitions[prior.content.clinicalStatus]!.contains(
                    content.clinicalStatus,
                  )))) {
        _bad();
      }
      body['content'] = next;
    }
    if (_source._used[prior] == true) {
      throw const ProblemUnavailable();
    }
    _source._used[prior] = true;
    return _source._write(
      this,
      prior.patientId,
      '${_root(prior.patientId)}/${prior.id}',
      body,
      create: false,
      decode: (j) {
        final r = ProblemRecord._(
          this,
          j,
          prior.patientId,
          expectedId: prior.id,
        );
        if (r.version != prior.version + 1 ||
            !mapEquals(
              r.content.toJson(),
              (content ?? prior.content).toJson(),
            ) ||
            !listEquals(r.consultationIds, expectedLinks)) {
          _bad();
        }
        return r;
      },
    );
  }

  Future<ProblemReview> workflow(
    int patient,
    int resource, {
    required bool medication,
  }) async {
    _id(patient);
    _id(resource);
    if (medication != _medicationReview || !current) {
      throw const ProblemUnavailable();
    }
    final path = medication
        ? 'api/v1/medication-reconciliations/$resource/problem-context'
        : 'api/v1/wards/admissions/$resource/problem-context';
    return _source._read(this, path, (j) {
      if (_id(j['patient_id']) != patient ||
          _id(j[medication ? 'reconciliation_id' : 'admission_id']) !=
              resource) {
        _bad();
      }
      final rows = _array(j['confirmed_current_problems'])
          .map((r) => ProblemCondition._(_object(r)))
          .toList();
      if (rows.map((r) => r.id).toSet().length != rows.length) {
        _bad();
      }
      return ProblemReview(_stamp(j['evaluated_at']), List.unmodifiable(rows));
    });
  }
}
