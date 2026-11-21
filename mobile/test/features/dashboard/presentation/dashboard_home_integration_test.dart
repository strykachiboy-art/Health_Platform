import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/dashboard/data/dashboard_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';

AuthSessionState _ready({String role = 'doctor', int? clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'test-dashboard-token',
        refreshToken: 'test-refresh',
        userId: 44,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> _envelope({
  String role = 'doctor',
  List<Map<String, dynamic>>? metrics,
}) {
  final overview = role == 'accountant'
      ? <String, dynamic>{
          'outstanding_invoice_count': 2,
          'outstanding_invoice_amount': '9007199254740993.12345678',
          'overdue_invoice_count': 0,
          'overdue_invoice_amount': '0.00',
          'successful_payments_today': 1,
          'successful_payments_today_amount': '21.50',
          'pending_payment_count': 0,
        }
      : <String, dynamic>{
          'appointments_today': 2,
          'confirmed_appointments_today': 1,
          'pending_consultations': 0,
          'pending_lab_orders': 0,
          'active_prescriptions': 0,
          'active_admissions': 0,
        };
  return <String, dynamic>{
    'success': true,
    'data': <String, dynamic>{
      'context': <String, dynamic>{
        'role': role,
        'scope': 'clinic',
        'clinic_id': 3,
        'generated_at': '2026-11-06T12:30:00+00:00',
      },
      'period': <String, dynamic>{
        'date_from': '2026-11-01',
        'date_to': '2026-11-06',
      },
      'overview': overview,
      'chat': <String, dynamic>{
        'unread_messages': 0,
        'unread_conversations': 0,
        'mentions': 0,
        'priority_messages': 0,
        'recent_messages': 0,
      },
      'metrics':
          metrics ??
          <Map<String, dynamic>>[
            <String, dynamic>{
              'key': 'appointments_today',
              'label': 'Appointments today',
              'value': 2,
              'unit': 'appointments',
            },
          ],
      'alerts': <dynamic>[],
      'recent_activity': <dynamic>[],
    },
  };
}

DashboardRemoteDataSource _source(
  MockClient client, {
  required Future<AuthSessionState> Function() loadSession,
  SessionNavigationGuard? guard,
}) => DashboardRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: loadSession,
  sessionNavigationGuard: guard,
);

void main() {
  testWidgets('S03 home renders only real metrics from one authenticated GET', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/dashboard');
      expect(request.headers['authorization'], 'Bearer test-dashboard-token');
      return http.Response(jsonEncode(_envelope()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    Future<AuthSessionState> loader() async => _ready();
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: loader,
          sessionNavigationGuard: guard,
          dashboardDataSource: _source(
            client,
            loadSession: loader,
            guard: guard,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(calls, 1);
    expect(find.byKey(const ValueKey('p22-dashboard-panel')), findsOneWidget);
    expect(find.text('Appointments today'), findsOneWidget);
    expect(find.text('2 appointments'), findsOneWidget);
    expect(find.byKey(const ValueKey('p22-dashboard-period')), findsOneWidget);
    expect(find.textContaining('does not invent counts'), findsNothing);
  });

  testWidgets('WD01 typed 409 gives signed-in patient an enrollment notice', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/dashboard');
      expect(request.headers['authorization'], 'Bearer test-dashboard-token');
      return http.Response(
        jsonEncode(<String, dynamic>{
          'success': false,
          'error': 'Clinical enrollment is pending',
          'code': 'PATIENT_ENROLLMENT_REQUIRED',
        }),
        409,
      );
    });
    addTearDown(client.close);
    Future<AuthSessionState> loader() async =>
        _ready(role: 'patient', clinic: null);
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: loader,
          dashboardDataSource: _source(client, loadSession: loader),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(requests, 1);
    expect(
      find.text(
        'Account created. Clinical enrollment is pending. '
        'Contact your clinic to complete enrollment.',
      ),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('p22-dashboard-unavailable')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('p22-dashboard-period')), findsNothing);
    expect(
      find.byKey(const ValueKey('p22-dashboard-metric-appointments_today')),
      findsNothing,
    );
  });

  for (final variant in <Map<String, String>>[
    <String, String>{'role': 'doctor', 'code': 'PATIENT_ENROLLMENT_REQUIRED'},
    <String, String>{'role': 'patient', 'code': 'UNRECOGNIZED_CONFLICT'},
  ]) {
    testWidgets('WD01 wrong role or wrong 409 code remains generic: $variant', (
      tester,
    ) async {
      final client = MockClient(
        (_) async => http.Response(
          jsonEncode(<String, dynamic>{
            'success': false,
            'error': 'SENSITIVE_SERVER_DIAGNOSTIC_NOT_FOR_UI',
            'code': variant['code'],
          }),
          409,
        ),
      );
      addTearDown(client.close);
      Future<AuthSessionState> loader() async => _ready(
        role: variant['role']!,
        clinic: variant['role'] == 'patient' ? null : 3,
      );
      await tester.pumpWidget(
        MaterialApp(
          home: AuthenticatedHomeScreen(
            loadSession: loader,
            dashboardDataSource: _source(client, loadSession: loader),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.text('Dashboard is unavailable. No current metrics are shown.'),
        findsOneWidget,
      );
      expect(
        find.textContaining('Clinical enrollment is pending'),
        findsNothing,
      );
      expect(find.textContaining('SENSITIVE_SERVER_DIAGNOSTIC'), findsNothing);
      expect(
        find.byKey(const ValueKey('p22-dashboard-metric-appointments_today')),
        findsNothing,
      );
    });
  }

  testWidgets('S03 empty backend metrics do not become fabricated zero cards', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(_envelope(metrics: <Map<String, dynamic>>[])),
        200,
      ),
    );
    addTearDown(client.close);
    Future<AuthSessionState> loader() async => _ready();
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: loader,
          dashboardDataSource: _source(client, loadSession: loader),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p22-dashboard-empty')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('p22-dashboard-metric-appointments_today')),
      findsNothing,
    );
  });

  for (final status in <int>[403, 500]) {
    testWidgets('S03 HTTP $status never exposes stale metrics', (tester) async {
      final client = MockClient((_) async => http.Response('denied', status));
      addTearDown(client.close);
      Future<AuthSessionState> loader() async => _ready();
      await tester.pumpWidget(
        MaterialApp(
          home: AuthenticatedHomeScreen(
            loadSession: loader,
            dashboardDataSource: _source(client, loadSession: loader),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p22-dashboard-unavailable')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('p22-dashboard-metric-appointments_today')),
        findsNothing,
      );
      expect(find.text('denied'), findsNothing);
    });
  }

  testWidgets(
    'S03 invalid response does not render data or raw server content',
    (tester) async {
      final client = MockClient(
        (_) async => http.Response('not valid json', 200),
      );
      addTearDown(client.close);
      Future<AuthSessionState> loader() async => _ready();
      await tester.pumpWidget(
        MaterialApp(
          home: AuthenticatedHomeScreen(
            loadSession: loader,
            dashboardDataSource: _source(client, loadSession: loader),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p22-dashboard-unavailable')),
        findsOneWidget,
      );
      expect(find.textContaining('not valid json'), findsNothing);
    },
  );

  testWidgets('S03 refresh clears old metrics until a new GET succeeds', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    var calls = 0;
    final client = MockClient((_) {
      calls++;
      if (calls == 1) {
        return Future.value(http.Response(jsonEncode(_envelope()), 200));
      }
      return pending.future;
    });
    addTearDown(client.close);
    Future<AuthSessionState> loader() async => _ready();
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: loader,
          dashboardDataSource: _source(client, loadSession: loader),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('2 appointments'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p22-dashboard-refresh')));
    await tester.pump();
    expect(find.byKey(const ValueKey('p22-dashboard-loading')), findsOneWidget);
    expect(find.text('2 appointments'), findsNothing);
    pending.complete(
      http.Response(
        jsonEncode(
          _envelope(
            metrics: <Map<String, dynamic>>[
              <String, dynamic>{
                'key': 'appointments_today',
                'label': 'Appointments today',
                'value': 7,
                'unit': 'appointments',
              },
            ],
          ),
        ),
        200,
      ),
    );
    await tester.pumpAndSettle();
    expect(calls, 2);
    expect(find.text('7 appointments'), findsOneWidget);
    expect(find.text('2 appointments'), findsNothing);
  });

  testWidgets('S03 session invalidation discards an in-flight dashboard GET', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    final started = Completer<void>();
    final client = MockClient((_) {
      if (!started.isCompleted) {
        started.complete();
      }
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    Future<AuthSessionState> loader() async => _ready();
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: loader,
          sessionNavigationGuard: guard,
          dashboardDataSource: _source(
            client,
            loadSession: loader,
            guard: guard,
          ),
        ),
      ),
    );
    await tester.pump();
    await tester.pump();
    expect(started.isCompleted, isTrue);
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_envelope()), 200));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p22-dashboard-panel')), findsNothing);
    expect(
      find.byKey(const ValueKey('p22-home-session-unavailable')),
      findsOneWidget,
    );
    expect(find.text('2 appointments'), findsNothing);
  });

  testWidgets('S03 finance dashboard preserves backend monetary precision', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(
          _envelope(
            role: 'accountant',
            metrics: <Map<String, dynamic>>[
              <String, dynamic>{
                'key': 'outstanding_invoice_amount',
                'label': 'Outstanding invoices',
                'value': '9007199254740993.12345678',
                'unit': 'currency',
              },
            ],
          ),
        ),
        200,
      ),
    );
    addTearDown(client.close);
    Future<AuthSessionState> loader() async => _ready(role: 'accountant');
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: loader,
          dashboardDataSource: _source(client, loadSession: loader),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('9007199254740993.12345678 currency'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('p22-home-clinical_safety')),
      findsNothing,
    );
  });

  for (final width in <double>[360, 1100]) {
    testWidgets('S03 live metric tiles fit ${width.toInt()}px viewport', (
      tester,
    ) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_envelope()), 200),
      );
      addTearDown(client.close);
      Future<AuthSessionState> loader() async => _ready();
      await tester.pumpWidget(
        MaterialApp(
          home: AuthenticatedHomeScreen(
            loadSession: loader,
            dashboardDataSource: _source(client, loadSession: loader),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.byKey(const ValueKey('p22-dashboard-panel')), findsOneWidget);
    });
  }

  testWidgets('S10B paramedic does not display generic doctor KPI cards', (
    tester,
  ) async {
    final response = _envelope(
      role: 'paramedic',
      metrics: <Map<String, dynamic>>[
        <String, dynamic>{
          'key': 'appointments',
          'label': 'Appointments',
          'value': 4,
          'unit': 'appointments',
        },
        <String, dynamic>{
          'key': 'my_patients',
          'label': 'My patients',
          'value': 0,
          'unit': 'patients',
        },
        <String, dynamic>{
          'key': 'unread_chat',
          'label': 'Unread chat messages',
          'value': 0,
          'unit': 'messages',
        },
      ],
    );
    final client = MockClient(
      (_) async => http.Response(jsonEncode(response), 200),
    );
    addTearDown(client.close);
    Future<AuthSessionState> loader() async => _ready(role: 'paramedic');
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: loader,
          dashboardDataSource: _source(client, loadSession: loader),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Emergency response workspace'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('wd07b-s10b-no-role-metrics')),
      findsOneWidget,
    );
    expect(find.textContaining('Ambulance & trips'), findsWidgets);
    expect(
      find.byKey(const ValueKey('p22-dashboard-metric-my_patients')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('p22-dashboard-metric-unread_chat')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('p22-dashboard-metric-appointments')),
      findsNothing,
    );
  });

  testWidgets('S10B pharmacist sees backend medication KPI, not staff chat', (
    tester,
  ) async {
    final response = _envelope(
      role: 'pharmacist',
      metrics: <Map<String, dynamic>>[
        <String, dynamic>{
          'key': 'my_patients',
          'label': 'My patients',
          'value': 0,
          'unit': 'patients',
        },
        <String, dynamic>{
          'key': 'unread_chat',
          'label': 'Unread chat messages',
          'value': 0,
          'unit': 'messages',
        },
        <String, dynamic>{
          'key': 'draft_medication_reconciliations',
          'label': 'Draft medication reconciliations',
          'value': 7,
          'unit': 'reconciliations',
        },
      ],
    );
    final client = MockClient(
      (_) async => http.Response(jsonEncode(response), 200),
    );
    addTearDown(client.close);
    Future<AuthSessionState> loader() async => _ready(role: 'pharmacist');
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: loader,
          dashboardDataSource: _source(client, loadSession: loader),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Pharmacy workspace'), findsOneWidget);
    expect(
      find.byKey(
        const ValueKey('p22-dashboard-metric-draft_medication_reconciliations'),
      ),
      findsOneWidget,
    );
    expect(find.text('7 reconciliations'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('p22-dashboard-metric-my_patients')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('p22-dashboard-metric-unread_chat')),
      findsNothing,
    );
  });

  testWidgets('S03 no authenticated session means zero dashboard requests', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_envelope()), 200);
    });
    addTearDown(client.close);
    Future<AuthSessionState> loader() async =>
        const AuthSessionState.signedOut();
    await tester.pumpWidget(
      MaterialApp(
        home: AuthenticatedHomeScreen(
          loadSession: loader,
          dashboardDataSource: _source(client, loadSession: loader),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(find.byKey(const ValueKey('p22-dashboard-panel')), findsNothing);
    expect(
      find.byKey(const ValueKey('p22-home-session-unavailable')),
      findsOneWidget,
    );
  });
}
