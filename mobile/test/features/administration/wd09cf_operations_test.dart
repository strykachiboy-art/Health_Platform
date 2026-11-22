import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/administration/data/wd09cf_operation_contract.dart';
import 'package:clinic_system_pro/features/administration/data/wd05_report_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/administration/data/wd09cf_operations_remote_data_source.dart';
import 'package:clinic_system_pro/features/administration/presentation/wd09cf_operations_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _ready(
  String role, {
  int? clinic = 7,
  String token = 'test-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'refresh-token',
    userId: 55,
    role: role,
    clinicContextId: clinic,
    tenantId: '11111111-1111-1111-1111-111111111111',
  ),
);

Wd09cfOperationsRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard,
  Future<AuthSessionState> Function() state,
  Wd09cfOneShotGate gate,
) => Wd09cfOperationsRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: state,
  sessionNavigationGuard: guard,
  gate: gate,
);

http.Response _response(
  Map<String, dynamic> data, {
  bool isAsset = false,
  bool isAssignment = false,
}) {
  final payload = <String, dynamic>{'success': true};
  if (isAsset) {
    payload['asset'] = data;
  } else if (isAssignment) {
    payload['assignment'] = data;
  } else {
    payload['data'] = data;
  }
  return http.Response(jsonEncode(payload), 200);
}

Map<String, String> _staff() => <String, String>{
  'first_name': 'Ada',
  'last_name': 'Obi',
};

void main() {
  test('WD09C-F action matrix includes all 15 roles without escalation', () {
    expect(Wd09cfAction.values, hasLength(19));
    const roles = <String>{
      'super_admin',
      'admin',
      'doctor',
      'nurse',
      'patient',
      'pharmacist',
      'lab_technician',
      'receptionist',
      'accountant',
      'paramedic',
      'emt',
      'driver',
      'ambulance_dispatcher',
      'ambulance_coordinator',
      'other',
    };
    expect(roles, hasLength(15));
    for (final role in roles) {
      if (role == 'patient') {
        expect(Wd09cfAction.values.any((a) => a.allowedFor(role, 7)), isFalse);
      } else {
        expect(Wd09cfAction.leaveRequest.allowedFor(role, 7), isTrue);
      }
      expect(Wd09cfAction.values.any((a) => a.allowedFor(role, null)), isFalse);
    }
    expect(Wd09cfAction.assetDispose.allowedFor('doctor', 7), isFalse);
    expect(Wd09cfAction.assetDispose.allowedFor('accountant', 7), isTrue);
    expect(Wd09cfAction.settingsUpdate.allowedFor('accountant', 7), isFalse);
    expect(Wd09cfAction.payrollMarkPaid.allowedFor('nurse', 7), isFalse);
    expect(Wd09cfAction.staffCreate.allowedFor('admin', 7), isTrue);
    expect(Wd09cfAction.staffCreate.allowedFor('patient', 7), isFalse);
  });

  test('WD09C-F strict schemas exclude arbitrary IDs and dangerous values', () {
    final body = Wd09cfAction.staffCreate.parseBody(_staff());
    expect(body.keys, containsAll(<String>['first_name', 'last_name']));
    expect(body.keys, isNot(contains('clinic_id')));
    expect(
      () => Wd09cfAction.staffCreate.parseBody({
        'first_name': 'Ada',
        'last_name': 'Obi',
        'clinic_id': '8',
      }),
      throwsFormatException,
    );
    expect(
      () => Wd09cfAction.staffCreate.parseBody({'first_name': 'Ada'}),
      throwsFormatException,
    );
    expect(
      () => Wd09cfAction.assetDispose.parseBody({'disposal_reason': ' '}),
      throwsFormatException,
    );
    expect(
      () => Wd09cfAction.payrollCreate.parseBody({
        'staff_id': '5',
        'pay_period_start': '2026-02-30',
        'pay_period_end': '2026-03-02',
        'base_salary': '100',
      }),
      throwsFormatException,
    );
    expect(
      () => Wd09cfAction.inventoryCreate.parseBody({
        'name': 'Gauze',
        'category': 'medical_supply',
        'initial_quantity': '-1',
        'reorder_level': '10',
      }),
      throwsFormatException,
    );
    expect(() => Wd09cfAction.departmentStatus.path(0), throwsArgumentError);
  });

  test(
    'WD09C create staff sends one validated POST, never client clinic ID',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final gate = Wd09cfOneShotGate();
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/staff');
        expect(request.headers['authorization'], 'Bearer test-token');
        final json = jsonDecode(request.body) as Map<String, dynamic>;
        expect(json, {'first_name': 'Ada', 'last_name': 'Obi'});
        expect(json.keys, isNot(contains('clinic_id')));
        return _response({'id': 18, 'clinic_id': 7});
      });
      addTearDown(client.close);
      final source = _source(client, guard, () async => _ready('admin'), gate);
      final receipt = await source.execute(
        action: Wd09cfAction.staffCreate,
        fields: _staff(),
      );
      expect(receipt.recordId, 18);
      expect(calls, 1);
      await expectLater(
        source.execute(action: Wd09cfAction.staffCreate, fields: _staff()),
        throwsA(isA<Wd09cfAlreadyAttempted>()),
      );
      expect(calls, 1);
    },
  );

  test('WD09C existing department must match clinic before PATCH', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        expect(request.url.path, '/api/v1/departments/12');
        return _response({'id': 12, 'clinic_id': 8});
      }
      fail('No mutation may be sent after cross-clinic pre-read.');
    });
    addTearDown(client.close);
    final source = _source(
      client,
      guard,
      () async => _ready('admin'),
      Wd09cfOneShotGate(),
    );
    await expectLater(
      source.execute(
        action: Wd09cfAction.departmentUpdate,
        recordId: 12,
        fields: {'name': 'New department'},
      ),
      throwsFormatException,
    );
  });

  test('WD09D asset assignment receipt matches selected asset', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var patchCalls = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        expect(request.url.path, '/api/v1/assets/31');
        return _response({'id': 31, 'clinic_id': 7}, isAsset: true);
      }
      patchCalls++;
      expect(request.method, 'POST');
      expect(request.url.path, '/api/v1/assets/31/assign');
      expect(jsonDecode(request.body), {'staff_id': 8});
      return _response({
        'id': 100,
        'clinic_id': 7,
        'asset_id': 31,
      }, isAssignment: true);
    });
    addTearDown(client.close);
    final source = _source(
      client,
      guard,
      () async => _ready('accountant'),
      Wd09cfOneShotGate(),
    );
    final receipt = await source.execute(
      action: Wd09cfAction.assetAssign,
      recordId: 31,
      fields: {'staff_id': '8'},
    );
    expect(receipt.recordId, 100);
    expect(patchCalls, 1);
  });

  test(
    'WD09D settings PATCH requires server version and exact receipt',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var patches = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          expect(request.url.path, '/api/v1/settings/clinic');
          return _response({'id': 44, 'clinic_id': 7, 'version': 3});
        }
        patches++;
        expect(request.method, 'PATCH');
        expect(
          request.url.queryParameters,
          containsPair('expected_version', '3'),
        );
        expect(jsonDecode(request.body), {
          'language': 'en',
          'date_format': 'YYYY-MM-DD',
          'time_format': '24h',
        });
        return _response({'id': 44, 'clinic_id': 7, 'version': 4});
      });
      addTearDown(client.close);
      final source = _source(
        client,
        guard,
        () async => _ready('admin'),
        Wd09cfOneShotGate(),
      );
      final receipt = await source.execute(
        action: Wd09cfAction.settingsUpdate,
        fields: {
          'expected_version': '3',
          'language': 'en',
          'date_format': 'YYYY-MM-DD',
          'time_format': '24h',
        },
      );
      expect(receipt.recordId, 44);
      expect(patches, 1);
    },
  );

  test('WD09E report generation uses only backend-authorized clinic', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((request) async {
      expect(request.method, 'POST');
      expect(request.url.path, '/api/v1/reports');
      expect(jsonDecode(request.body), {
        'report_type': 'patients',
        'report_format': 'csv',
      });
      return _response({'id': 23, 'clinic_id': 7});
    });
    addTearDown(client.close);
    final source = _source(
      client,
      guard,
      () async => _ready('nurse'),
      Wd09cfOneShotGate(),
    );
    final receipt = await source.execute(
      action: Wd09cfAction.reportGenerate,
      fields: {'report_type': 'patients', 'report_format': 'csv'},
    );
    expect(receipt.clinicId, 7);
  });

  test('WD09F nurse can read report metadata within selected clinic', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/reports');
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'page': 1,
            'per_page': 20,
            'total': 1,
            'items': [
              {
                'id': 23,
                'clinic_id': 7,
                'report_type': 'patients',
                'report_format': 'csv',
                'created_at': '2026-11-22T08:00:00Z',
              },
            ],
          },
        }),
        200,
      );
    });
    addTearDown(client.close);
    final reportSource = Wd05ReportReadRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
      loadSession: () async => _ready('nurse'),
      sessionNavigationGuard: guard,
    );
    final result = await reportSource.listReports();
    expect(result.items, hasLength(1));
    expect(result.items.single.clinicId, 7);
  });

  test('WD09E unauthorized payroll mutation sends zero HTTP', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return _response({'id': 1, 'clinic_id': 7});
    });
    addTearDown(client.close);
    final source = _source(
      client,
      guard,
      () async => _ready('doctor'),
      Wd09cfOneShotGate(),
    );
    await expectLater(
      source.execute(
        action: Wd09cfAction.payrollMarkPaid,
        recordId: 7,
        fields: {},
      ),
      throwsA(isA<Wd09cfUnavailable>()),
    );
    expect(calls, 0);
  });

  test('WD09C/F session switch during GET blocks subsequent PATCH', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var actor = _ready('admin');
    final started = Completer<void>();
    final respond = Completer<http.Response>();
    var posts = 0;
    final client = MockClient((request) {
      if (request.method == 'GET') {
        started.complete();
        return respond.future;
      }
      posts++;
      return Future<http.Response>.value(_response({'id': 9, 'clinic_id': 7}));
    });
    addTearDown(client.close);
    final source = _source(
      client,
      guard,
      () async => actor,
      Wd09cfOneShotGate(),
    );
    final pending = source.execute(
      action: Wd09cfAction.staffStatus,
      recordId: 9,
      fields: {'status': 'on_leave'},
    );
    await started.future;
    actor = _ready('admin', clinic: 8);
    respond.complete(_response({'id': 9, 'clinic_id': 7}));
    await expectLater(pending, throwsA(isA<Wd09cfUnavailable>()));
    expect(posts, 0);
  });

  test(
    'WD09E uncertain transport prevents a second mutation attempt',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final gate = Wd09cfOneShotGate();
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return http.Response('{"error":"backend unavailable"}', 500);
      });
      addTearDown(client.close);
      final source = _source(
        client,
        guard,
        () async => _ready('accountant'),
        gate,
      );
      await expectLater(
        source.execute(
          action: Wd09cfAction.payrollCreate,
          fields: {
            'staff_id': '8',
            'pay_period_start': '2026-11-01',
            'pay_period_end': '2026-11-30',
            'base_salary': '500',
          },
        ),
        throwsA(isA<Exception>()),
      );
      await expectLater(
        source.execute(
          action: Wd09cfAction.payrollCreate,
          fields: {
            'staff_id': '8',
            'pay_period_start': '2026-11-01',
            'pay_period_end': '2026-11-30',
            'base_salary': '500',
          },
        ),
        throwsA(isA<Wd09cfAlreadyAttempted>()),
      );
      expect(calls, 1);
    },
  );

  testWidgets(
    'WD09C/F UI requires explicit confirmation and clears on logout',
    (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return _response({'id': 12, 'clinic_id': 7});
      });
      addTearDown(client.close);
      final source = _source(
        client,
        guard,
        () async => _ready('admin'),
        Wd09cfOneShotGate(),
      );
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(body: Wd09cfOperationsScreen(source: source)),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('wd09cf-action-select')),
        findsOneWidget,
      );
      await tester.enterText(
        find.byKey(const ValueKey('wd09cf-field-first_name')),
        'Ada',
      );
      await tester.enterText(
        find.byKey(const ValueKey('wd09cf-field-last_name')),
        'Obi',
      );
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('wd09cf-review-submit')),
        180.0,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.byKey(const ValueKey('wd09cf-review-submit')));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('wd09cf-confirm-submit')),
        findsOneWidget,
      );
      expect(calls, 0);
      await tester.tap(find.byKey(const ValueKey('wd09cf-confirm-submit')));
      await tester.pumpAndSettle();
      expect(calls, 1);
      guard.invalidate();
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('wd09cf-action-select')), findsNothing);
    },
  );
}
