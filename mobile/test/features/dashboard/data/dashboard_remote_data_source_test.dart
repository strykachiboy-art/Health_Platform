import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/dashboard/data/models/dashboard_snapshot.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _accessToken = 'dashboard-test-token';
const _timestamp = '2026-11-06T12:30:00+00:00';

const _overviewFields = <DashboardFamily, List<String>>{
  DashboardFamily.system: [
    'total_clinics',
    'active_clinics',
    'suspended_clinics',
    'total_users',
    'active_users',
    'total_staff',
    'total_patients',
  ],
  DashboardFamily.management: [
    'total_patients',
    'active_patients',
    'total_staff',
    'active_staff',
    'appointments_today',
    'missed_appointments_today',
    'active_admissions',
    'occupied_beds',
    'pending_lab_orders',
  ],
  DashboardFamily.clinical: [
    'appointments_today',
    'confirmed_appointments_today',
    'pending_consultations',
    'pending_lab_orders',
    'active_prescriptions',
    'active_admissions',
  ],
  DashboardFamily.operations: [
    'appointments_today',
    'missed_appointments_today',
    'scheduled_appointments_today',
    'confirmed_appointments_today',
    'active_ambulance_trips',
    'pending_ambulance_requests',
    'active_staff',
    'active_departments',
    'unassigned_active_staff',
  ],
  DashboardFamily.finance: [
    'outstanding_invoice_count',
    'outstanding_invoice_amount',
    'overdue_invoice_count',
    'overdue_invoice_amount',
    'successful_payments_today',
    'successful_payments_today_amount',
    'pending_payment_count',
  ],
  DashboardFamily.patient: [
    'upcoming_appointments',
    'active_prescriptions',
    'active_admissions',
    'outstanding_balance',
    'unread_notifications',
  ],
};

AuthTokenResponse _session(String role, {int? clinicId = 3}) =>
    AuthTokenResponse(
      accessToken: _accessToken,
      refreshToken: 'not-used',
      userId: 42,
      role: role,
      clinicContextId: clinicId,
    );

Map<String, dynamic> _envelope(String role, {int? clinicId = 3}) {
  final family = DashboardFamily.fromRole(role);
  final overview = <String, dynamic>{
    for (final field in _overviewFields[family]!) field: 4,
  };
  if (family == DashboardFamily.finance) {
    overview['outstanding_invoice_amount'] = '9007199254740993.12345678';
    overview['overdue_invoice_amount'] = '0.00';
    overview['successful_payments_today_amount'] = '12.50';
  }
  if (family == DashboardFamily.patient) {
    overview['outstanding_balance'] = '123.45000001';
  }
  final data = <String, dynamic>{
    'context': <String, dynamic>{
      'role': role,
      'scope': family.expectedScope,
      'clinic_id': family == DashboardFamily.system ? null : clinicId,
      'generated_at': _timestamp,
    },
    'period': <String, dynamic>{
      'date_from': '2026-11-01',
      'date_to': '2026-11-06',
    },
    'overview': overview,
    'metrics': <dynamic>[
      <String, dynamic>{
        'key': 'appointments_today',
        'label': 'Appointments today',
        'value': 3,
        'unit': 'appointments',
        'trend': <String, dynamic>{'direction': 'up', 'percentage': '5.50'},
      },
    ],
  };
  if (family != DashboardFamily.patient) {
    data['alerts'] = <dynamic>[
      <String, dynamic>{
        'key': 'pending_work',
        'severity': 'warning',
        'title': 'Pending work',
        'count': 2,
        'description': null,
      },
    ];
    data['recent_activity'] = <dynamic>[
      <String, dynamic>{
        'entity_type': 'appointment',
        'entity_id': 7,
        'action': 'created',
        'occurred_at': _timestamp,
      },
    ];
  }
  if (family != DashboardFamily.patient && family != DashboardFamily.system) {
    data['chat'] = <String, dynamic>{
      'unread_messages': 0,
      'unread_conversations': 0,
      'mentions': 0,
      'priority_messages': 0,
      'recent_messages': 0,
    };
  }
  return <String, dynamic>{'success': true, 'data': data};
}

DashboardRemoteDataSource _source(
  MockClient mock, {
  Future<AuthSessionState> Function()? loader,
  String role = 'doctor',
  int? clinicId = 3,
  SessionNavigationGuard? guard,
  Duration timeout = const Duration(seconds: 2),
  void Function()? onUnauthorized,
}) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: mock,
    requestTimeout: timeout,
  )..onAuthenticationRejected = onUnauthorized;
  return DashboardRemoteDataSource(
    apiClient: api,
    loadSession:
        loader ??
        () async => AuthSessionState.ready(_session(role, clinicId: clinicId)),
    sessionNavigationGuard: guard,
  );
}

void main() {
  group('P22.S03 read-only role dashboard data source', () {
    for (final role in <String>[
      'super_admin',
      'admin',
      'doctor',
      'nurse',
      'pharmacist',
      'lab_technician',
      'paramedic',
      'emt',
      'receptionist',
      'driver',
      'ambulance_dispatcher',
      'ambulance_coordinator',
      'other',
      'accountant',
      'patient',
    ]) {
      test('$role uses one GET and parses its dashboard family', () async {
        var calls = 0;
        final mock = MockClient((request) async {
          calls++;
          expect(request.method, 'GET');
          expect(request.url.path, '/api/v1/dashboard');
          expect(request.url.hasQuery, isFalse);
          expect(request.headers['authorization'], 'Bearer $_accessToken');
          expect(request.headers.containsKey('idempotency-key'), isFalse);
          return http.Response(jsonEncode(_envelope(role)), 200);
        });
        addTearDown(mock.close);
        final result = await _source(mock, role: role).fetchDashboard();
        expect(calls, 1);
        expect(result.context.family, DashboardFamily.fromRole(role));
        expect(result.context.generatedAt.isUtc, isTrue);
        expect(
          result.overview.values.length,
          _overviewFields[DashboardFamily.fromRole(role)]!.length,
        );
        expect(result.metrics.single.value.text, '3');
        expect(result.metrics.single.trend!.percentage!.text, '5.50');
        expect(result.period.to.isBefore(result.period.from), isFalse);
      });
    }

    test('Decimal money values preserve all digits', () async {
      final mock = MockClient(
        (_) async => http.Response(jsonEncode(_envelope('accountant')), 200),
      );
      addTearDown(mock.close);
      final dashboard = await _source(
        mock,
        role: 'accountant',
      ).fetchDashboard();
      expect(
        dashboard.overview.values['outstanding_invoice_amount']!.text,
        '9007199254740993.12345678',
      );
      expect(dashboard.overview.values['overdue_invoice_amount']!.text, '0.00');
    });

    test('patient snapshot never obtains staff alerts/activity', () async {
      final mock = MockClient(
        (_) async => http.Response(jsonEncode(_envelope('patient')), 200),
      );
      addTearDown(mock.close);
      final snapshot = await _source(mock, role: 'patient').fetchDashboard();
      expect(snapshot.alerts, isEmpty);
      expect(snapshot.recentActivity, isEmpty);
      expect(snapshot.chat, isNull);
      expect(snapshot.context.scope, 'personal');
    });

    test('super admin accepts system scope after clinic selection', () async {
      final mock = MockClient(
        (_) async => http.Response(jsonEncode(_envelope('super_admin')), 200),
      );
      addTearDown(mock.close);
      final snapshot = await _source(
        mock,
        role: 'super_admin',
      ).fetchDashboard();
      expect(snapshot.context.scope, 'system');
      expect(snapshot.context.clinicId, isNull);
    });

    test('missing signed-in session prevents network GET', () async {
      var requests = 0;
      final mock = MockClient((_) async {
        requests++;
        return http.Response(jsonEncode(_envelope('doctor')), 200);
      });
      addTearDown(mock.close);
      for (final state in <AuthSessionState>[
        const AuthSessionState.signedOut(),
        const AuthSessionState.recoveryRequired(),
      ]) {
        final source = _source(mock, loader: () async => state);
        await expectLater(
          source.fetchDashboard(),
          throwsA(isA<DashboardSessionMismatchException>()),
        );
      }
      expect(requests, 0);
    });

    test('unknown local role fails closed before network GET', () async {
      var requests = 0;
      final mock = MockClient((_) async {
        requests++;
        return http.Response(jsonEncode(_envelope('doctor')), 200);
      });
      addTearDown(mock.close);
      await expectLater(
        _source(mock, role: 'root').fetchDashboard(),
        throwsA(isA<DashboardSessionMismatchException>()),
      );
      expect(requests, 0);
    });

    test('guard blocks GET before an available session', () async {
      var requests = 0;
      final mock = MockClient((_) async {
        requests++;
        return http.Response(jsonEncode(_envelope('doctor')), 200);
      });
      addTearDown(mock.close);
      final guard = SessionNavigationGuard();
      addTearDown(guard.dispose);
      await expectLater(
        _source(mock, guard: guard).fetchDashboard(),
        throwsA(isA<DashboardSessionMismatchException>()),
      );
      expect(requests, 0);
    });

    test('stale successful GET after logout is rejected', () async {
      final waiting = Completer<http.Response>();
      final requestStarted = Completer<void>();
      final mock = MockClient((_) {
        requestStarted.complete();
        return waiting.future;
      });
      addTearDown(mock.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final future = _source(mock, guard: guard).fetchDashboard();
      await requestStarted.future;
      guard.invalidate();
      waiting.complete(http.Response(jsonEncode(_envelope('doctor')), 200));
      await expectLater(
        future,
        throwsA(isA<DashboardSessionMismatchException>()),
      );
    });

    test('changed account during request cannot reuse old data', () async {
      final waiting = Completer<http.Response>();
      final requestStarted = Completer<void>();
      var current = _session('doctor');
      final mock = MockClient((_) {
        requestStarted.complete();
        return waiting.future;
      });
      addTearDown(mock.close);
      final future = _source(
        mock,
        loader: () async => AuthSessionState.ready(current),
      ).fetchDashboard();
      await requestStarted.future;
      current = AuthTokenResponse(
        accessToken: 'different-user-token',
        refreshToken: 'unused',
        userId: 99,
        role: 'patient',
        clinicContextId: 3,
      );
      waiting.complete(http.Response(jsonEncode(_envelope('doctor')), 200));
      await expectLater(
        future,
        throwsA(isA<DashboardSessionMismatchException>()),
      );
    });

    for (final code in <int>[401, 403, 422, 500, 503]) {
      test('HTTP $code stays a one-request failure, no retry', () async {
        var calls = 0;
        var rejected = 0;
        final mock = MockClient((_) async {
          calls++;
          return http.Response('{"error":"Access denied"}', code);
        });
        addTearDown(mock.close);
        await expectLater(
          _source(mock, onUnauthorized: () => rejected++).fetchDashboard(),
          throwsA(
            isA<ApiException>().having((e) => e.statusCode, 'status', code),
          ),
        );
        expect(calls, 1);
        expect(rejected, code == 401 ? 1 : 0);
      });
    }

    test('connection failure is not automatically retried', () async {
      var calls = 0;
      final mock = MockClient((_) async {
        calls++;
        throw http.ClientException('connection failed');
      });
      addTearDown(mock.close);
      await expectLater(
        _source(mock).fetchDashboard(),
        throwsA(
          isA<ApiException>().having(
            (e) => e.kind,
            'kind',
            ApiFailureKind.connection,
          ),
        ),
      );
      expect(calls, 1);
    });

    test('request timeout is reported without automatic retry', () async {
      var calls = 0;
      final waiting = Completer<http.Response>();
      final mock = MockClient((_) {
        calls++;
        return waiting.future;
      });
      addTearDown(mock.close);
      await expectLater(
        _source(
          mock,
          timeout: const Duration(milliseconds: 25),
        ).fetchDashboard(),
        throwsA(
          isA<ApiException>().having(
            (e) => e.kind,
            'kind',
            ApiFailureKind.timeout,
          ),
        ),
      );
      expect(calls, 1);
      waiting.complete(http.Response(jsonEncode(_envelope('doctor')), 200));
    });

    test('non-JSON HTTP 200 is an invalid response', () async {
      final mock = MockClient((_) async => http.Response('not JSON', 200));
      addTearDown(mock.close);
      await expectLater(
        _source(mock).fetchDashboard(),
        throwsA(
          isA<ApiException>().having(
            (e) => e.kind,
            'kind',
            ApiFailureKind.invalidResponse,
          ),
        ),
      );
    });

    for (final scenario in <String, void Function(Map<String, dynamic>)>{
      'false success envelope': (e) => e['success'] = false,
      'missing envelope data': (e) => e.remove('data'),
      'wrong server role': (e) =>
          (e['data'] as Map<String, dynamic>)['context']['role'] = 'nurse',
      'wrong scope': (e) =>
          (e['data'] as Map<String, dynamic>)['context']['scope'] = 'personal',
      'wrong clinic': (e) =>
          (e['data'] as Map<String, dynamic>)['context']['clinic_id'] = 9,
      'invalid naive timestamp': (e) =>
          (e['data'] as Map<String, dynamic>)['context']['generated_at'] =
              '2026-11-06T12:30:00',
      'missing overview key': (e) =>
          ((e['data'] as Map<String, dynamic>)['overview']
                  as Map<String, dynamic>)
              .remove('active_admissions'),
      'negative overview': (e) =>
          (e['data'] as Map<String, dynamic>)['overview']['active_admissions'] =
              -1,
      'fractional integer count': (e) =>
          (e['data'] as Map<String, dynamic>)['overview']['active_admissions'] =
              1.5,
      'string integer count': (e) =>
          (e['data'] as Map<String, dynamic>)['overview']['active_admissions'] =
              '3',
      'bad dashboard alert severity': (e) =>
          (e['data'] as Map<String, dynamic>)['alerts'][0]['severity'] = 'high',
      'reversed period': (e) =>
          (e['data'] as Map<String, dynamic>)['period']['date_to'] =
              '2026-10-01',
      'missing required chat': (e) =>
          (e['data'] as Map<String, dynamic>).remove('chat'),
      'malformed metrics list': (e) =>
          (e['data'] as Map<String, dynamic>)['metrics'] = <String, dynamic>{},
    }.entries) {
      test('${scenario.key} fails closed', () async {
        final payload = _envelope('doctor');
        scenario.value(payload);
        final mock = MockClient(
          (_) async => http.Response(jsonEncode(payload), 200),
        );
        addTearDown(mock.close);
        await expectLater(
          _source(mock).fetchDashboard(),
          throwsFormatException,
        );
      });
    }

    test(
      'patient cannot receive staff-alert arrays in personal dashboard',
      () async {
        final payload = _envelope('patient');
        (payload['data'] as Map<String, dynamic>)['alerts'] = <dynamic>[];
        final mock = MockClient(
          (_) async => http.Response(jsonEncode(payload), 200),
        );
        addTearDown(mock.close);
        await expectLater(
          _source(mock, role: 'patient').fetchDashboard(),
          throwsFormatException,
        );
      },
    );
  });
}
