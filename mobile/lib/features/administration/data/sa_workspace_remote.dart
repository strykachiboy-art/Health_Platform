import 'dart:convert';

import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

import 'sa_workspace_contract.dart';
import 'wd09cf_operations_remote_data_source.dart';

class SaUnavailable implements Exception {
  const SaUnavailable();
}

class SaResult {
  const SaResult(this.rows, {this.total, this.page, this.note = ''});
  final List<Map<String, dynamic>> rows;
  final int? total;
  final int? page;
  final String note;
}

class SaSession {
  const SaSession._(this.actor, this.revision);
  final AuthTokenResponse actor;
  final int revision;
  int? get clinic => actor.clinicContextId;
}

class SaReview {
  const SaReview._(this.session, this.request, this.preview);
  final SaSession session;
  final SaRequest request;
  final List<Map<String, dynamic>> preview;
}

class SaWorkspaceRemote {
  const SaWorkspaceRemote({
    required this.apiClient,
    required this.loadSession,
    required this.guard,
    required this.gate,
    this.staging = false,
  });
  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard guard;
  final Wd09cfOneShotGate gate;
  final bool staging;

  bool current(SaSession s) =>
      guard.canAccessProtectedRoutes && guard.revision == s.revision;
  Future<SaSession> session() async {
    final revision = guard.revision;
    if (!guard.canAccessProtectedRoutes) throw const SaUnavailable();
    final state = await loadSession();
    final a = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        a == null ||
        a.role.trim().toLowerCase() != 'super_admin' ||
        a.userId <= 0 ||
        a.accessToken.isEmpty ||
        a.accessToken.trim() != a.accessToken ||
        !guard.canAccessProtectedRoutes ||
        guard.revision != revision) {
      throw const SaUnavailable();
    }
    return SaSession._(a, revision);
  }

  Future<void> _check(SaSession s, SaOperation op) async {
    if (!current(s) || !op.allowed(s.clinic, staging)) {
      throw const SaUnavailable();
    }
    final fresh = await session();
    final a = s.actor;
    final b = fresh.actor;
    if (!current(s) ||
        fresh.revision != s.revision ||
        a.userId != b.userId ||
        a.role != b.role ||
        a.clinicContextId != b.clinicContextId ||
        a.tenantId != b.tenantId ||
        a.accessToken != b.accessToken ||
        a.refreshToken != b.refreshToken) {
      throw const SaUnavailable();
    }
  }

  static Map<String, dynamic> _object(Object? value) {
    if (value is! Map<String, dynamic>) {
      throw const FormatException('Invalid server object.');
    }
    return value;
  }

  static int _positive(Object? value) {
    if (value is! int || value <= 0) {
      throw const FormatException('Invalid server identifier.');
    }
    return value;
  }

  static List<Map<String, dynamic>> _rows(Object? value) {
    if (value is! List || value.length > 500) {
      throw const FormatException('Invalid server list.');
    }
    return value.map(_object).toList(growable: false);
  }

  static void _require(bool condition) {
    if (!condition) {
      throw const FormatException('Server identity, scope or state mismatch.');
    }
  }

  static Map<String, dynamic> _project(
    Map<String, dynamic> row,
    Set<String> keys,
  ) => {
    for (final k in keys)
      if (row.containsKey(k)) k: row[k],
  };
  static const _ruleKeys = {
    'id',
    'clinic_id',
    'rule_code',
    'name',
    'description',
    'scope',
    'rule_type',
    'severity',
    'action',
    'conditions',
    'configuration',
    'priority',
    'enabled',
    'is_hard_rule',
    'version',
    'effective_from',
    'effective_until',
  };
  static const _membershipKeys = {
    'id',
    'clinic_id',
    'staff_id',
    'department_id',
    'is_primary',
    'status',
    'assigned_at',
    'ended_at',
  };
  static const _excuseKeys = {
    'id',
    'staff_id',
    'leave_request_id',
    'excuse_type',
    'description',
    'document_url',
    'status',
    'rejection_reason',
    'reviewed_by_user_id',
    'reviewed_at',
    'created_at',
  };
  static const _leaveKeys = {
    'id',
    'clinic_id',
    'staff_id',
    'leave_type',
    'status',
    'start_date',
    'end_date',
    'reason',
    'reviewed_by_user_id',
    'reviewed_at',
  };
  static const _payrollKeys = {
    'id',
    'clinic_id',
    'staff_id',
    'pay_period_start',
    'pay_period_end',
    'base_salary',
    'bonuses',
    'deductions',
    'net_pay',
    'paid_at',
  };
  static const _staffKeys = {
    'id',
    'clinic_id',
    'first_name',
    'last_name',
    'status',
  };
  static const _departmentKeys = {'id', 'clinic_id', 'name', 'status'};
  static const _alertKeys = {
    'code',
    'severity',
    'component',
    'resource',
    'observed_value',
    'threshold',
    'occurrence_count',
    'first_seen_at',
    'last_seen_at',
  };
  static const _eventKeys = {
    'event_id',
    'timestamp',
    'event_type',
    'component',
    'severity',
    'request_id',
    'trace_id',
    'method',
    'route',
    'status',
    'error_type',
    'task_name',
    'task_id',
  };
  static const _metricKeys = {
    'available',
    'healthy',
    'latency_ms',
    'redis_version',
    'connected_clients',
    'blocked_clients',
    'used_memory_bytes',
    'cache_hit_ratio',
    'instantaneous_ops_per_sec',
    'rejected_connections',
    'evicted_keys',
    'expired_keys',
    'cpu_percent',
    'memory_percent',
    'swap_percent',
    'load_average',
    'broker_healthy',
    'workers_healthy',
    'worker_count',
    'total_concurrency',
    'active_tasks',
    'reserved_tasks',
    'scheduled_tasks',
    'worker_utilization_percent',
    'queue_depths',
    'long_running_active_count',
    'server_available',
    'redis_coordination_healthy',
    'active_connections',
    'room_count',
    'message_queue_enabled',
  };

  Future<Map<String, dynamic>> _get(
    SaSession s,
    SaOperation op,
    String path,
  ) async {
    await _check(s, op);
    final raw = await apiClient.getJson(path, bearerToken: s.actor.accessToken);
    await _check(s, op);
    _require(raw['success'] != false);
    return raw;
  }

  Map<String, dynamic> _rule(Map<String, dynamic> row) {
    _positive(row['id']);
    _positive(row['version']);
    _require(
      row['scope'] == 'global' &&
          row.containsKey('clinic_id') &&
          row['clinic_id'] == null &&
          row['is_hard_rule'] is bool &&
          row['enabled'] is bool &&
          row['rule_code'] is String,
    );
    return _project(row, _ruleKeys);
  }

  Map<String, dynamic> _clinicRow(
    SaSession s,
    Map<String, dynamic> row,
    Set<String> keys,
  ) {
    _positive(row['id']);
    _require(row['clinic_id'] == s.clinic && s.clinic != null);
    return _project(row, keys);
  }

  Map<String, dynamic> _staffRecord(
    SaSession s,
    Map<String, dynamic> row,
    Set<String> keys,
  ) {
    _positive(row['staff_id']);
    if (keys == _excuseKeys) {
      // Flask excuse responses have no clinic_id. The signed, selected-clinic
      // route/service owns scope; an unexpected explicit mismatched ID is denied.
      _positive(row['id']);
      _require(!row.containsKey('clinic_id') || row['clinic_id'] == s.clinic);
      return _project(row, keys);
    }
    return _clinicRow(s, row, keys);
  }

  Map<String, dynamic> _membership(SaSession s, Map<String, dynamic> row) {
    _positive(row['staff_id']);
    _positive(row['department_id']);
    _require(
      row['is_primary'] is bool &&
          const {'active', 'ended'}.contains(row['status']),
    );
    return _clinicRow(s, row, _membershipKeys);
  }

  SaResult _page(
    Map<String, dynamic> raw,
    List<Map<String, dynamic>> rows, {
    int? requested,
  }) {
    final total = raw['total'];
    final page = raw['page'];
    final per = raw['per_page'];
    _require(
      total is int &&
          total >= rows.length &&
          page is int &&
          page > 0 &&
          per is int &&
          per > 0 &&
          rows.length <= per &&
          (requested == null || page == requested),
    );
    return SaResult(
      rows,
      total: total as int,
      page: page as int,
      note: 'Showing ${rows.length} records; server total $total.',
    );
  }

  Future<SaResult> read(
    SaSession s,
    SaOperation op,
    Map<String, String> fields,
  ) async {
    if (op.writes) throw const SaUnavailable();
    final q = op.prepare(fields);
    final raw = await _get(s, op, q.path);
    final id = q.values['id'];
    switch (op) {
      case SaOperation.rules:
        return _page(
          raw,
          _rows(raw['items']).map(_rule).toList(),
          requested: 1,
        );
      case SaOperation.ruleDetail:
        final row = _rule(_object(raw['rule']));
        _require(row['id'] == id);
        return SaResult([row]);
      case SaOperation.operations:
      case SaOperation.failures:
        final data = _object(raw['data']);
        _require(
          raw['success'] == true &&
              data['schema_version'] == 'v1' &&
              data['scope'] == 'platform',
        );
        if (op == SaOperation.operations) {
          final components = _object(data['components']);
          final alerts = _object(data['alerts']);
          final rows = <Map<String, dynamic>>[
            {
              'scope': 'platform',
              'timestamp': data['timestamp'],
              'healthy': data['healthy'],
              'state_store_available': alerts['state_store_available'],
            },
          ];
          for (final key in [
            'database',
            'redis',
            'system',
            'celery',
            'socketio',
          ]) {
            rows.add({
              'component': key,
              ..._project(_object(components[key]), _metricKeys),
            });
          }
          rows.add(
            _project(alerts, {
              'current_count',
              'active_count',
              'critical_count',
              'warning_count',
            }),
          );
          for (final key in ['current_conditions', 'active']) {
            rows.addAll(
              _rows(alerts[key])
                  .map((r) => {'alert_state': key, ..._project(r, _alertKeys)}),
            );
          }
          return SaResult(
            rows,
            note: 'Platform metrics, not clinic health or readiness certification.',
          );
        }
        return SaResult([
          {
            'scope': 'platform',
            'timestamp': data['timestamp'],
            'state_store_available': data['state_store_available'],
            ..._project(_object(data['summary']), {
              'recent_failure_count',
              'by_component',
              'by_event_type',
              'by_severity',
              'latest_failure_at',
            }),
          },
          ..._rows(data['recent_failures']).map((r) => _project(r, _eventKeys)),
        ]);
      case SaOperation.staffMemberships:
      case SaOperation.departmentMembers:
        final rows = _rows(raw['data']).map((r) => _membership(s, r)).toList();
        final key = op == SaOperation.staffMemberships
            ? 'staff_id'
            : 'department_id';
        _require(rows.every((r) => r[key] == q.values[key]));
        return SaResult(rows);
      case SaOperation.excuses:
      case SaOperation.myExcuses:
      case SaOperation.leaves:
      case SaOperation.payroll:
        final data = _object(raw['data']);
        final keys = op == SaOperation.leaves
            ? _leaveKeys
            : op == SaOperation.payroll
            ? _payrollKeys
            : _excuseKeys;
        final rows = _rows(data['items'])
            .map((r) => _staffRecord(s, r, keys))
            .toList();
        _require(
          rows.every(
            (r) =>
                (q.values['staff_id'] == null ||
                    r['staff_id'] == q.values['staff_id']) &&
                (q.values['status'] == null ||
                    r['status'] == q.values['status']),
          ),
        );
        return _page(data, rows, requested: q.values['page'] as int? ?? 1);
      case SaOperation.excuseDetail:
      case SaOperation.leaveDetail:
      case SaOperation.payrollDetail:
        final keys = op == SaOperation.excuseDetail
            ? _excuseKeys
            : op == SaOperation.leaveDetail
            ? _leaveKeys
            : _payrollKeys;
        final row = _staffRecord(s, _object(raw['data']), keys);
        _require(row['id'] == id);
        return SaResult([row]);
      default:
        throw const SaUnavailable();
    }
  }

  Future<List<Map<String, dynamic>>> _preflight(
    SaSession s,
    SaRequest q,
  ) async {
    final op = q.operation;
    final id = q.values['id'];
    await _check(s, op);
    switch (op) {
      case SaOperation.ruleUpdate:
      case SaOperation.ruleDisable:
        final row = _rule(
          _object(
            (await _get(s, op, 'api/v1/clinical-safety/rules/$id'))['rule'],
          ),
        );
        _require(row['id'] == id && row['is_hard_rule'] == false);
        if (op == SaOperation.ruleDisable) _require(row['enabled'] == true);
        return [row];
      case SaOperation.excuseApprove:
      case SaOperation.excuseReject:
      case SaOperation.leaveApprove:
      case SaOperation.leaveReject:
        final leave =
            op == SaOperation.leaveApprove || op == SaOperation.leaveReject;
        final row = _staffRecord(
          s,
          _object(
            (await _get(
              s,
              op,
              'api/v1/staff/${leave ? 'leave' : 'excuses'}/$id',
            ))['data'],
          ),
          leave ? _leaveKeys : _excuseKeys,
        );
        _require(row['id'] == id && row['status'] == 'pending');
        return [row];
      case SaOperation.assignMembership:
      case SaOperation.primaryMembership:
      case SaOperation.endMembership:
        final staff = q.values['staff_id'];
        final department = q.values['department_id'];
        final a = _clinicRow(
          s,
          _object((await _get(s, op, 'api/v1/staff/$staff'))['data']),
          _staffKeys,
        );
        final b = _clinicRow(
          s,
          _object(
            (await _get(s, op, 'api/v1/departments/$department'))['data'],
          ),
          _departmentKeys,
        );
        _require(a['id'] == staff && b['id'] == department);
        if (op != SaOperation.endMembership) {
          _require(a['status'] == 'active' && b['status'] == 'active');
        }
        final memberships = _rows(
          (await _get(s, op, 'api/v1/staff-departments/staff/$staff'))['data'],
        ).map((r) => _membership(s, r)).toList();
        _require(memberships.every((r) => r['staff_id'] == staff));
        final matching = memberships
            .where(
              (r) =>
                  r['department_id'] == department && r['status'] == 'active',
            )
            .toList();
        _require(
          op == SaOperation.assignMembership
              ? matching.isEmpty
              : matching.length == 1,
        );
        return [a, b, ...memberships];
      case SaOperation.payrollGenerate:
        final salaries = _object(q.body['salary_lookup']);
        final rows = <Map<String, dynamic>>[];
        for (final staff in salaries.keys) {
          final row = _clinicRow(
            s,
            _object((await _get(s, op, 'api/v1/staff/$staff'))['data']),
            _staffKeys,
          );
          _require(row['id'].toString() == staff && row['status'] == 'active');
          rows.add(row);
        }
        return rows;
      case SaOperation.ruleCreate:
      case SaOperation.excuseCreate:
        return [];
      default:
        throw const SaUnavailable();
    }
  }

  Future<SaReview> review(
    SaSession s,
    SaOperation op,
    Map<String, String> fields,
  ) async {
    if (!op.writes) throw const SaUnavailable();
    final q = op.prepare(fields);
    return SaReview._(
      s,
      q,
      List<Map<String, dynamic>>.unmodifiable(
        (await _preflight(
          s,
          q,
        )).map((r) => Map<String, dynamic>.unmodifiable(r)),
      ),
    );
  }

  String _key(SaSession s, SaRequest q) {
    final op = q.operation;
    final v = q.values;
    final target = switch (op) {
      SaOperation.leaveApprove ||
      SaOperation.leaveReject => 'leave-review:${v['id']}',
      SaOperation.excuseApprove ||
      SaOperation.excuseReject => 'excuse-review:${v['id']}',
      SaOperation.ruleUpdate ||
      SaOperation.ruleDisable => 'rule-review:${v['id']}',
      SaOperation.ruleCreate => 'rule-create:${v['rule_code']}',
      SaOperation.payrollGenerate =>
        'payroll-batch:${v['pay_period_start']}:${v['pay_period_end']}',
      SaOperation.assignMembership ||
      SaOperation.primaryMembership ||
      SaOperation.endMembership =>
        '${op.name}:${v['staff_id']}:${v['department_id']}',
      _ => op.name,
    };
    return 'sa-workspace|${s.actor.userId}|${op.clinicRequired ? s.clinic : 'platform'}|$target';
  }

  static String _money(Object? value) {
    if (value is! String || !RegExp(r'^\d+(?:\.\d{1,2})?$').hasMatch(value)) {
      throw const FormatException('Invalid payroll amount.');
    }
    final parts = value.split('.');
    return '${BigInt.parse(parts[0])}.${parts.length == 1 ? '00' : parts[1].padRight(2, '0')}';
  }

  Future<SaResult> submit(SaReview review) async {
    final s = review.session;
    final q = review.request;
    final op = q.operation;
    if (!op.writes) throw const SaUnavailable();
    await _check(s, op);
    final key = _key(s, q);
    if (gate.contains(key)) throw const Wd09cfAlreadyAttempted();
    final latest = await _preflight(s, q);
    _require(jsonEncode(latest) == jsonEncode(review.preview));
    await _check(s, op);
    if (!gate.claim(key)) throw const Wd09cfAlreadyAttempted();
    // No retries, queue entries, idempotency guesses or offline replay.
    final raw = switch (q.operation.method) {
      'POST' => await apiClient.postJson(
        q.path,
        body: q.body,
        bearerToken: s.actor.accessToken,
      ),
      'PATCH' => await apiClient.patchJson(
        q.path,
        body: q.body,
        bearerToken: s.actor.accessToken,
      ),
      'DELETE' => await apiClient.deleteJson(
        q.path,
        bearerToken: s.actor.accessToken,
      ),
      _ => throw const SaUnavailable(),
    };
    await _check(s, op);
    _require(raw['success'] != false);
    final id = q.values['id'];
    switch (op) {
      case SaOperation.ruleCreate:
      case SaOperation.ruleUpdate:
      case SaOperation.ruleDisable:
        final row = _rule(_object(raw['rule']));
        _require(row['is_hard_rule'] == false);
        if (op == SaOperation.ruleCreate) {
          _require(
            row['rule_code'] == q.body['rule_code'] &&
                row['version'] == 1 &&
                row['enabled'] == q.body['enabled'],
          );
        } else {
          final prior = latest.single;
          _require(
            row['rule_code'] == prior['rule_code'] &&
                row['version'] == (prior['version'] as int) + 1 &&
                row['id'] != id,
          );
          if (op == SaOperation.ruleDisable) _require(row['enabled'] == false);
        }
        return SaResult(
          [row],
          note: 'Server returned the rule version. Clinical governance/release review remains required.',
        );
      case SaOperation.assignMembership:
      case SaOperation.primaryMembership:
      case SaOperation.endMembership:
        final row = _membership(s, _object(raw['data']));
        _require(
          row['staff_id'] == q.values['staff_id'] &&
              row['department_id'] == q.values['department_id'] &&
              row['status'] ==
                  (op == SaOperation.endMembership ? 'ended' : 'active'),
        );
        if (op == SaOperation.primaryMembership || q.body['is_primary'] == true) {
          _require(row['is_primary'] == true);
        }
        return SaResult([row]);
      case SaOperation.excuseCreate:
      case SaOperation.excuseApprove:
      case SaOperation.excuseReject:
      case SaOperation.leaveApprove:
      case SaOperation.leaveReject:
        final leave =
            op == SaOperation.leaveApprove || op == SaOperation.leaveReject;
        final row = _staffRecord(
          s,
          _object(raw['data']),
          leave ? _leaveKeys : _excuseKeys,
        );
        if (op == SaOperation.excuseCreate) {
          _require(
            row['status'] == 'pending' &&
                row['excuse_type'] == q.body['excuse_type'],
          );
        } else {
          _require(
            row['id'] == id &&
                row['staff_id'] == latest.single['staff_id'] &&
                row['reviewed_by_user_id'] == s.actor.userId &&
                row['status'] ==
                    (op == SaOperation.leaveApprove ||
                            op == SaOperation.excuseApprove
                        ? 'approved'
                        : 'rejected'),
          );
        }
        return SaResult([row]);
      case SaOperation.payrollGenerate:
        final rows = _rows(raw['data'])
            .map((r) => _staffRecord(s, r, _payrollKeys))
            .toList();
        final salaries = _object(q.body['salary_lookup']);
        final staff = rows.map((r) => r['staff_id']).toSet();
        _require(
          staff.length == rows.length &&
              rows.every(
                (r) =>
                    salaries.containsKey(r['staff_id'].toString()) &&
                    r['pay_period_start'] == q.body['pay_period_start'] &&
                    r['pay_period_end'] == q.body['pay_period_end'] &&
                    _money(r['base_salary']) ==
                        _money(salaries[r['staff_id'].toString()]) &&
                    _money(r['net_pay']) == _money(r['base_salary']) &&
                    _money(r['bonuses']) == '0.00' &&
                    _money(r['deductions']) == '0.00' &&
                    r['paid_at'] == null,
              ),
        );
        return SaResult(
          rows,
          note:
              '${rows.length} new records returned. Existing or otherwise ineligible records may be skipped by Flask; reconcile in payroll list. No payment was sent.',
        );
      default:
        throw const SaUnavailable();
    }
  }
}
