import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/administration/data/sa_workspace_contract.dart';
import 'package:clinic_system_pro/features/administration/data/sa_workspace_remote.dart';
import 'package:clinic_system_pro/features/administration/data/wd09cf_operations_remote_data_source.dart';
import 'package:clinic_system_pro/features/administration/presentation/sa_workspace_screen.dart';
import 'package:clinic_system_pro/features/administration/presentation/wd05_administration_hub.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

AuthSessionState _actor({
  String role = 'super_admin',
  int? clinic = 7,
  String token = 'test-access',
  String? tenant,
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'test-refresh',
    userId: 41,
    role: role,
    clinicContextId: clinic,
    tenantId: tenant,
  ),
);
SaWorkspaceRemote _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? load,
  bool staging = true,
  bool adminMode = false,
  Wd09cfOneShotGate? gate,
}) => SaWorkspaceRemote(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: load ?? () async => _actor(),
  guard: guard,
  gate: gate ?? Wd09cfOneShotGate(),
  staging: staging,
  adminMode: adminMode,
);
http.Response _reply(Object data, [int status = 200]) => http.Response(
  jsonEncode(data),
  status,
  headers: {'content-type': 'application/json'},
);
Map<String, dynamic> _rule({
  int id = 12,
  int version = 1,
  bool enabled = true,
  bool hard = false,
  String scope = 'global',
  int? clinic,
}) => {
  'id': id,
  'clinic_id': clinic,
  'rule_code': 'TEST_RULE',
  'scope': scope,
  'is_hard_rule': hard,
  'enabled': enabled,
  'version': version,
  'name': 'Synthetic rule',
  'rule_type': 'duration_limit',
  'severity': 'high',
  'action': 'alert',
  'configuration': {'maximum_days': 3},
  'conditions': <String, dynamic>{},
  'priority': 100,
};
Map<String, dynamic> _membership({
  String status = 'active',
  bool primary = false,
  int clinic = 7,
}) => {
  'id': 12,
  'staff_id': 8,
  'department_id': 9,
  'clinic_id': clinic,
  'status': status,
  'is_primary': primary,
};
Map<String, dynamic> _staff({int clinic = 7}) => {
  'id': 8,
  'clinic_id': clinic,
  'status': 'active',
  'first_name': 'Synthetic',
  'last_name': 'Staff',
};
Map<String, dynamic> _department() => {
  'id': 9,
  'clinic_id': 7,
  'status': 'active',
  'name': 'Synthetic department',
};
Map<String, dynamic> _leave({String status = 'pending', int clinic = 7}) => {
  'id': 12,
  'clinic_id': clinic,
  'staff_id': 8,
  'status': status,
  'leave_type': 'annual',
  'start_date': '2027-01-01',
  'end_date': '2027-01-03',
  'reviewed_by_user_id': status == 'pending' ? null : 41,
};
Map<String, dynamic> _excuse({String status = 'pending'}) => {
  'id': 12,
  'staff_id': 8,
  'status': status,
  'excuse_type': 'personal',
  'description': 'Synthetic excuse',
  'reviewed_by_user_id': status == 'pending' ? null : 41,
};
Map<String, dynamic> _payroll({int clinic = 7}) => {
  'id': 12,
  'clinic_id': clinic,
  'staff_id': 8,
  'pay_period_start': '2027-01-01',
  'pay_period_end': '2027-01-31',
  'base_salary': '500.00',
  'bonuses': '0.00',
  'deductions': '0.00',
  'net_pay': '500.00',
  'paid_at': null,
};
Map<String, dynamic> _page(Map<String, dynamic> row) => {
  'items': [row],
  'total': 1,
  'page': 1,
  'per_page': 50,
};
Map<String, dynamic> _ops() => {
  'success': true,
  'data': {
    'schema_version': 'v1',
    'scope': 'platform',
    'timestamp': '2026-11-26T12:00:00Z',
    'healthy': true,
    'components': {
      for (final k in ['database', 'redis', 'system', 'celery', 'socketio'])
        k: {
          'available': true,
          'healthy': true,
          'unexpected_secret': 'never-render',
        },
    },
    'alerts': {
      'current_conditions': [],
      'active': [],
      'current_count': 0,
      'active_count': 0,
      'critical_count': 0,
      'warning_count': 0,
      'state_store_available': true,
    },
  },
};
Map<String, dynamic> _failures() => {
  'success': true,
  'data': {
    'schema_version': 'v1',
    'scope': 'platform',
    'timestamp': '2026-11-26T12:00:00Z',
    'state_store_available': true,
    'recent_failures': [
      {
        'event_id': 'synthetic',
        'event_type': 'test',
        'component': 'db',
        'severity': 'warning',
        'unexpected_secret': 'never-render',
      },
    ],
    'summary': {
      'recent_failure_count': 1,
      'by_component': {'db': 1},
    },
  },
};

class _Case {
  const _Case(this.path, this.verb, this.fields, this.body);
  final String path;
  final String verb;
  final Map<String, String> fields;
  final Map<String, dynamic> body;
}

const _cases = <SaOperation, _Case>{
  SaOperation.rules: _Case(
    '/api/v1/clinical-safety/rules?scope=global',
    'GET',
    {},
    {},
  ),
  SaOperation.ruleDetail: _Case('/api/v1/clinical-safety/rules/12', 'GET', {
    'id': '12',
  }, {}),
  SaOperation.ruleCreate: _Case(
    '/api/v1/clinical-safety/rules',
    'POST',
    {
      'rule_code': 'test_rule',
      'name': 'Synthetic rule',
      'rule_type': 'duration_limit',
      'severity': 'high',
      'action': 'alert',
      'configuration': '{"maximum_days":3}',
      'enabled': 'false',
    },
    {
      'rule_code': 'TEST_RULE',
      'name': 'Synthetic rule',
      'rule_type': 'duration_limit',
      'severity': 'high',
      'action': 'alert',
      'configuration': {'maximum_days': 3},
      'enabled': false,
      'scope': 'global',
    },
  ),
  SaOperation.ruleUpdate: _Case(
    '/api/v1/clinical-safety/rules/12',
    'PATCH',
    {'id': '12', 'name': 'Changed'},
    {'name': 'Changed'},
  ),
  SaOperation.ruleDisable: _Case(
    '/api/v1/clinical-safety/rules/12/disable',
    'POST',
    {'id': '12'},
    {},
  ),
  SaOperation.operations: _Case('/api/v1/operations', 'GET', {}, {}),
  SaOperation.failures: _Case('/api/v1/operations/failures?limit=20', 'GET', {
    'limit': '20',
  }, {}),
  SaOperation.staffMemberships: _Case(
    '/api/v1/staff-departments/staff/8?include_ended=true',
    'GET',
    {'staff_id': '8', 'include_ended': 'true'},
    {},
  ),
  SaOperation.departmentMembers: _Case(
    '/api/v1/staff-departments/department/9',
    'GET',
    {'department_id': '9'},
    {},
  ),
  SaOperation.assignMembership: _Case(
    '/api/v1/staff-departments/staff/8/department/9',
    'POST',
    {'staff_id': '8', 'department_id': '9', 'is_primary': 'true'},
    {'department_id': 9, 'is_primary': true},
  ),
  SaOperation.primaryMembership: _Case(
    '/api/v1/staff-departments/staff/8/department/9/primary',
    'POST',
    {'staff_id': '8', 'department_id': '9'},
    {},
  ),
  SaOperation.endMembership: _Case(
    '/api/v1/staff-departments/staff/8/department/9',
    'DELETE',
    {'staff_id': '8', 'department_id': '9'},
    {},
  ),
  SaOperation.excuses: _Case(
    '/api/v1/staff/excuses?page=1&staff_id=8&status=pending',
    'GET',
    {'page': '1', 'staff_id': '8', 'status': 'pending'},
    {},
  ),
  SaOperation.excuseDetail: _Case('/api/v1/staff/excuses/12', 'GET', {
    'id': '12',
  }, {}),
  SaOperation.myExcuses: _Case('/api/v1/staff/excuses/me', 'GET', {}, {}),
  SaOperation.excuseCreate: _Case(
    '/api/v1/staff/excuses',
    'POST',
    {'excuse_type': 'personal', 'description': 'Synthetic excuse'},
    {'excuse_type': 'personal', 'description': 'Synthetic excuse'},
  ),
  SaOperation.excuseApprove: _Case('/api/v1/staff/excuses/12/approve', 'POST', {
    'id': '12',
  }, {}),
  SaOperation.excuseReject: _Case(
    '/api/v1/staff/excuses/12/reject',
    'POST',
    {'id': '12', 'reason': 'Synthetic reason'},
    {'reason': 'Synthetic reason'},
  ),
  SaOperation.leaves: _Case(
    '/api/v1/staff/leave?page=1&staff_id=8&status=pending',
    'GET',
    {'page': '1', 'staff_id': '8', 'status': 'pending'},
    {},
  ),
  SaOperation.leaveDetail: _Case('/api/v1/staff/leave/12', 'GET', {
    'id': '12',
  }, {}),
  SaOperation.leaveApprove: _Case('/api/v1/staff/leave/12/approve', 'POST', {
    'id': '12',
  }, {}),
  SaOperation.leaveReject: _Case(
    '/api/v1/staff/leave/12/reject',
    'POST',
    {'id': '12', 'reason': 'Synthetic reason'},
    {'reason': 'Synthetic reason'},
  ),
  SaOperation.payroll: _Case('/api/v1/staff/payroll?page=1&staff_id=8', 'GET', {
    'page': '1',
    'staff_id': '8',
  }, {}),
  SaOperation.payrollDetail: _Case('/api/v1/staff/payroll/12', 'GET', {
    'id': '12',
  }, {}),
  SaOperation.payrollGenerate: _Case(
    '/api/v1/staff/payroll/generate',
    'POST',
    {
      'pay_period_start': '2027-01-01',
      'pay_period_end': '2027-01-31',
      'salary_lookup': '{"8":"500.00"}',
    },
    {
      'pay_period_start': '2027-01-01',
      'pay_period_end': '2027-01-31',
      'salary_lookup': {'8': '500.00'},
    },
  ),
};
Object _final(SaOperation op) => switch (op) {
  SaOperation.rules => _page(_rule()),
  SaOperation.ruleDetail => {'rule': _rule()},
  SaOperation.ruleCreate => {'rule': _rule(id: 13, enabled: false)},
  SaOperation.ruleUpdate => {'rule': _rule(id: 13, version: 2)},
  SaOperation.ruleDisable => {
    'rule': _rule(id: 13, version: 2, enabled: false),
  },
  SaOperation.operations => _ops(),
  SaOperation.failures => _failures(),
  SaOperation.staffMemberships || SaOperation.departmentMembers => {
    'data': [_membership()],
  },
  SaOperation.assignMembership ||
  SaOperation.primaryMembership => {'data': _membership(primary: true)},
  SaOperation.endMembership => {'data': _membership(status: 'ended')},
  SaOperation.excuses || SaOperation.myExcuses => {'data': _page(_excuse())},
  SaOperation.excuseDetail || SaOperation.excuseCreate => {'data': _excuse()},
  SaOperation.excuseApprove => {'data': _excuse(status: 'approved')},
  SaOperation.excuseReject => {'data': _excuse(status: 'rejected')},
  SaOperation.leaves => {'data': _page(_leave())},
  SaOperation.leaveDetail => {'data': _leave()},
  SaOperation.leaveApprove => {'data': _leave(status: 'approved')},
  SaOperation.leaveReject => {'data': _leave(status: 'rejected')},
  SaOperation.payroll => {'data': _page(_payroll())},
  SaOperation.payrollDetail => {'data': _payroll()},
  SaOperation.payrollGenerate => {
    'data': [_payroll()],
  },
  _ => throw StateError('Unexpected Admin-only extension.'),
};
Object _preread(SaOperation op, String path) => switch (path) {
  '/api/v1/clinical-safety/rules/12' => {'rule': _rule()},
  '/api/v1/staff/8' => {'data': _staff()},
  '/api/v1/departments/9' => {'data': _department()},
  '/api/v1/staff-departments/staff/8' => {
    'data': op == SaOperation.assignMembership ? [] : [_membership()],
  },
  '/api/v1/staff/leave/12' => {'data': _leave()},
  '/api/v1/staff/excuses/12' => {'data': _excuse()},
  _ => throw StateError('Unexpected pre-read: $path'),
};
void main() {
  const tenant = '12345678-1234-1234-1234-123456789abc';
  for (final op in SaWorkspaceRemote.adminOperations.where((op) => !op.isExtendedAdmin)) {
    test('admin contract ${op.name}: exact verb path body and response', () async {
      final c = _cases[op]!;
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var finalCalls = 0;
      final client = MockClient((r) async {
        expect(r.headers['authorization'], 'Bearer test-access');
        final path = r.url.path + (r.url.hasQuery ? '?${r.url.query}' : '');
        if (r.method == c.verb && path == c.path) {
          finalCalls++;
          if (op.writes && c.verb != 'DELETE') {
            expect(jsonDecode(r.body), c.body);
          } else {
            expect(r.body, isEmpty);
          }
          return _reply(_final(op));
        }
        expect(r.method, 'GET');
        return _reply(_preread(op, path));
      });
      addTearDown(client.close);
      final source = _source(client, guard,
        adminMode: true,
        load: () async => _actor(role: 'admin', tenant: tenant),
      );
      final s = await source.session();
      final SaResult result;
      if (op.writes) {
        final review = await source.review(s, op, c.fields);
        expect(finalCalls, 0);
        result = await source.submit(review);
        await expectLater(source.submit(review),
          throwsA(isA<Wd09cfAlreadyAttempted>()));
      } else {
        result = await source.read(s, op, c.fields);
      }
      expect(finalCalls, 1);
      expect(result.rows, isNotEmpty);
      expect(jsonEncode(result.rows), isNot(contains('never-render')));
    });
  }
  test('admin workspace rejects seven platform operations, zero HTTP', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var calls = 0;
    final client = MockClient((r) async {
      calls++;
      throw StateError('Unexpected HTTP');
    });
    addTearDown(client.close);
    final source = _source(client, guard, adminMode: true,
      load: () async => _actor(role: 'admin', tenant: tenant));
    final s = await source.session();
    final held = SaOperation.values.where(
      (op) => !SaWorkspaceRemote.adminOperations.contains(op),
    ).toList();
    expect(held, hasLength(7));
    expect(SaWorkspaceRemote.adminOperations.where((op) => !op.isExtendedAdmin), hasLength(18));
    expect(SaWorkspaceRemote.adminOperations.where((op) => op.isExtendedAdmin), hasLength(63));
    for (final op in held) {
      expect(source.allows(s, op), isFalse);
      await expectLater(
        op.writes ? source.review(s, op, _cases[op]!.fields)
          : source.read(s, op, _cases[op]!.fields),
        throwsA(isA<SaUnavailable>()),
      );
    }
    final locked = _source(client, guard, adminMode: true, staging: false,
      load: () async => _actor(role: 'admin', tenant: tenant));
    final a = await locked.session();
    await expectLater(locked.review(a, SaOperation.payrollGenerate,
      _cases[SaOperation.payrollGenerate]!.fields), throwsA(isA<SaUnavailable>()));
    expect(calls, 0);
  });
  test('admin workspace rejects missing tenant, clinic and foreign roles', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var calls = 0;
    final client = MockClient((r) async {
      calls++;
      throw StateError('Unexpected HTTP');
    });
    addTearDown(client.close);
    for (final a in [
      _actor(role: 'admin'),
      _actor(role: 'admin', clinic: null, tenant: tenant),
      _actor(role: 'super_admin', tenant: tenant),
      _actor(role: 'doctor', tenant: tenant),
      _actor(role: 'nurse', tenant: tenant),
    ]) {
      await expectLater(_source(client, guard, adminMode: true,
        load: () async => a).session(), throwsA(isA<SaUnavailable>()));
    }
    expect(calls, 0);
  });
  testWidgets('clinic Admin workspace excludes platform operations', (tester) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var calls = 0;
    final client = MockClient((r) async {
      calls++;
      throw StateError('Unexpected HTTP');
    });
    addTearDown(client.close);
    await tester.pumpWidget(MaterialApp(home: SaWorkspaceScreen(
      source: _source(client, guard, adminMode: true, staging: false,
        load: () async => _actor(role: 'admin', tenant: tenant)),
    )));
    await tester.pumpAndSettle();
    expect(find.text('Clinic administration workspace'), findsOneWidget);
    final dropdown = tester.widget<DropdownButton<SaOperation>>(
      find.byKey(const ValueKey('sa-operation')),
    );
    expect(dropdown.items!.map((e) => e.value).toSet(),
      SaWorkspaceRemote.adminOperations.where((op) => !op.releaseHeld).toSet());
    expect(calls, 0);
  });
  for (final op in _cases.keys) {
    test('contract ${op.name}: exact verb path body and response', () async {
      final c = _cases[op]!;
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var finalCalls = 0;
      final client = MockClient((r) async {
        expect(r.headers['authorization'], 'Bearer test-access');
        final path = r.url.path + (r.url.hasQuery ? '?${r.url.query}' : '');
        if (r.method == c.verb && path == c.path) {
          finalCalls++;
          if (op.writes && c.verb != 'DELETE') {
            expect(jsonDecode(r.body), c.body);
          }
          else {
            expect(r.body, isEmpty);
          }
          return _reply(_final(op));
        }
        expect(r.method, 'GET');
        return _reply(_preread(op, path));
      });
      addTearDown(client.close);
      final source = _source(client, guard);
      final s = await source.session();
      final SaResult result;
      if (op.writes) {
        final review = await source.review(s, op, c.fields);
        expect(finalCalls, 0);
        result = await source.submit(review);
        await expectLater(
          source.submit(review),
          throwsA(isA<Wd09cfAlreadyAttempted>()),
        );
      } else {
        result = await source.read(s, op, c.fields);
      }
      expect(finalCalls, 1);
      expect(result.rows, isNotEmpty);
      expect(jsonEncode(result.rows), isNot(contains('never-render')));
    });
  }
  test('roles, release holds and selected-clinic requirements', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var calls = 0;
    final client = MockClient((r) async {
      calls++;
      throw StateError('Unexpected HTTP');
    });
    addTearDown(client.close);
    for (final role in [
      'admin',
      'doctor',
      'nurse',
      'accountant',
      'patient',
      'invalid',
    ]) {
      await expectLater(
        _source(client, guard, load: () async => _actor(role: role)).session(),
        throwsA(isA<SaUnavailable>()),
      );
    }
    final source = _source(client, guard, staging: false);
    final s = await source.session();
    for (final op in _cases.keys.where((o) => o.releaseHeld)) {
      await expectLater(
        source.review(s, op, _cases[op]!.fields),
        throwsA(isA<SaUnavailable>()),
      );
    }
    final unscoped = _source(
      client,
      guard,
      load: () async => _actor(clinic: null),
    );
    final u = await unscoped.session();
    await expectLater(
      unscoped.read(u, SaOperation.leaves, {}),
      throwsA(isA<SaUnavailable>()),
    );
    expect(calls, 0);
    expect(SaOperation.values.where((op) => !op.isExtendedAdmin).length, 25);
  });
  test('reject injection invalid date and numeric salaries before HTTP', () {
    expect(
      () => SaOperation.leaveApprove.prepare({'id': '12', 'clinic_id': '9'}),
      throwsFormatException,
    );
    expect(
      () => SaOperation.excuseDetail.prepare({'id': '12/approve'}),
      throwsFormatException,
    );
    expect(
      () => SaOperation.payrollGenerate.prepare({
        'pay_period_start': '2027-02-30',
        'pay_period_end': '2027-03-01',
        'salary_lookup': '{"8":"10"}',
      }),
      throwsFormatException,
    );
    expect(
      () => SaOperation.payrollGenerate.prepare({
        'pay_period_start': '2027-01-01',
        'pay_period_end': '2027-01-31',
        'salary_lookup': '{"8":500}',
      }),
      throwsFormatException,
    );
    expect(
      () =>
          SaOperation.ruleUpdate.prepare({'id': '12', 'is_hard_rule': 'true'}),
      throwsFormatException,
    );
  });
  test(
    'foreign record pre-read blocks review; hard rule blocks modification',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var writes = 0;
      final client = MockClient((r) async {
        if (r.method != 'GET') writes++;
        return r.url.path.contains('rules')
            ? _reply({'rule': _rule(hard: true)})
            : _reply({'data': _leave(clinic: 8)});
      });
      addTearDown(client.close);
      final source = _source(client, guard);
      final s = await source.session();
      await expectLater(
        source.review(s, SaOperation.leaveApprove, {'id': '12'}),
        throwsFormatException,
      );
      await expectLater(
        source.review(s, SaOperation.ruleDisable, {'id': '12'}),
        throwsFormatException,
      );
      expect(writes, 0);
    },
  );
  test(
    'foreign membership receipt or payroll response cannot display',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final client = MockClient(
        (r) async => r.url.path.contains('payroll')
            ? _reply({'data': _payroll(clinic: 8)})
            : _reply({
                'data': [_membership(clinic: 8)],
              }),
      );
      addTearDown(client.close);
      final source = _source(client, guard);
      final s = await source.session();
      await expectLater(
        source.read(s, SaOperation.payrollDetail, {'id': '12'}),
        throwsFormatException,
      );
      await expectLater(
        source.read(s, SaOperation.staffMemberships, {'staff_id': '8'}),
        throwsFormatException,
      );
    },
  );
  test('session switch after pre-read blocks confirmation', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var actor = _actor();
    var writes = 0;
    final client = MockClient((r) async {
      if (r.method != 'GET') writes++;
      return _reply({'data': _leave()});
    });
    addTearDown(client.close);
    final source = _source(client, guard, load: () async => actor);
    final s = await source.session();
    final review = await source.review(s, SaOperation.leaveApprove, {
      'id': '12',
    });
    actor = _actor(clinic: 8, token: 'new-token');
    await expectLater(source.submit(review), throwsA(isA<SaUnavailable>()));
    expect(writes, 0);
  });
  test(
    'changed server status between preview and confirmation blocks POST',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var changed = false;
      var writes = 0;
      final client = MockClient((r) async {
        if (r.method != 'GET') writes++;
        return _reply({
          'data': _leave(status: changed ? 'approved' : 'pending'),
        });
      });
      addTearDown(client.close);
      final source = _source(client, guard);
      final s = await source.session();
      final review = await source.review(s, SaOperation.leaveApprove, {
        'id': '12',
      });
      changed = true;
      await expectLater(source.submit(review), throwsFormatException);
      expect(writes, 0);
    },
  );
  test(
    'uncertain write remains blocked across reconstructed adapter',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final gate = Wd09cfOneShotGate();
      var writes = 0;
      final client = MockClient((r) async {
        if (r.method == 'GET') return _reply({'data': _leave()});
        writes++;
        return _reply({'error': 'unavailable'}, 500);
      });
      addTearDown(client.close);
      final source = _source(client, guard, gate: gate);
      final s = await source.session();
      final review = await source.review(s, SaOperation.leaveApprove, {
        'id': '12',
      });
      await expectLater(source.submit(review), throwsA(isA<Exception>()));
      final second = _source(client, guard, gate: gate);
      final ss = await second.session();
      final reject = await second.review(ss, SaOperation.leaveReject, {
        'id': '12',
      });
      await expectLater(
        second.submit(reject),
        throwsA(isA<Wd09cfAlreadyAttempted>()),
      );
      expect(writes, 1);
    },
  );
  test('session invalidation during read discards returned data', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((r) async {
      guard.invalidate();
      return _reply(_ops());
    });
    addTearDown(client.close);
    final source = _source(client, guard);
    final s = await source.session();
    await expectLater(
      source.read(s, SaOperation.operations, {}),
      throwsA(isA<SaUnavailable>()),
    );
  });
  testWidgets(
    'workspace loads through shared guard and clears result on invalidation',
    (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final client = MockClient((r) async => _reply(_ops()));
      addTearDown(client.close);
      await tester.pumpWidget(
        MaterialApp(
          home: SaWorkspaceScreen(
            source: _source(client, guard, staging: false),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('sa-workspace')), findsOneWidget);
      await tester.ensureVisible(find.byKey(const ValueKey('sa-request')));
      await tester.tap(find.byKey(const ValueKey('sa-request')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('sa-result')), findsOneWidget);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('sa-unavailable')), findsOneWidget);
      expect(find.byKey(const ValueKey('sa-result')), findsNothing);
    },
  );
  testWidgets('existing hub opens workspace only for current Super Admin', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var actor = _actor(clinic: null);
    final opened = <String>[];
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Wd05AdministrationHub(
            loadSession: () async => actor,
            sessionNavigationGuard: guard,
            onOpen: opened.add,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    final link = find.byKey(const ValueKey('wd05-admin-sa_workspace'));
    await tester.scrollUntilVisible(
      link,
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.ensureVisible(link);
    await tester.pumpAndSettle();
    expect(link.hitTestable(), findsOneWidget);
    await tester.tap(link);
    await tester.pumpAndSettle();
    expect(opened, ['/administration/sa-workspace']);
    actor = _actor(role: 'admin');
    await tester.ensureVisible(link);
    await tester.pumpAndSettle();
    expect(link.hitTestable(), findsOneWidget);
    await tester.tap(link);
    await tester.pumpAndSettle();
    expect(opened, hasLength(1));
  });
  testWidgets('staff review requires explicit confirmation', (tester) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var writes = 0;
    final client = MockClient((r) async {
      if (r.method == 'GET') return _reply({'data': _leave()});
      writes++;
      return _reply({'data': _leave(status: 'approved')});
    });
    addTearDown(client.close);
    await tester.pumpWidget(
      MaterialApp(home: SaWorkspaceScreen(source: _source(client, guard))),
    );
    await tester.pumpAndSettle();
    final dropdown = tester.widget<DropdownButton<SaOperation>>(
      find.byKey(const ValueKey('sa-operation')),
    );
    dropdown.onChanged!(SaOperation.leaveApprove);
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('sa-field-id')), '12');
    await tester.ensureVisible(find.byKey(const ValueKey('sa-request')));
    await tester.tap(find.byKey(const ValueKey('sa-request')));
    await tester.pumpAndSettle();
    expect(writes, 0);
    final confirm = find.byKey(const ValueKey('sa-confirm'));
    await tester.scrollUntilVisible(
      confirm,
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.ensureVisible(confirm);
    await tester.pumpAndSettle();
    expect(confirm.hitTestable(), findsOneWidget);
    await tester.tap(confirm);
    await tester.pumpAndSettle();
    expect(writes, 1);
  });
}
