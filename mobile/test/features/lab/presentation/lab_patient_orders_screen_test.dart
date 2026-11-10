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
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/presentation/lab_patient_orders_screen.dart';

AuthSessionState _ready({String role = 'doctor'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'lab-widget-token',
    refreshToken: 'lab-widget-refresh',
    userId: 42,
    clinicContextId: 3,
    role: role,
  ),
);

Map<String, dynamic> _order({int patientId = 7, int id = 12}) => {
  'id': id,
  'clinic_id': 3,
  'patient_id': patientId,
  'consultation_id': null,
  'ordered_by_id': 18,
  'status': 'completed',
  'created_at': '2026-11-10T08:00:00+00:00',
  'items': [
    {
      'id': id + 100,
      'order_id': id,
      'test_id': 10,
      'test': {
        'id': 10,
        'clinic_id': 3,
        'name': 'Hemoglobin',
        'sample_type': 'blood',
        'is_active': true,
      },
      'result_value': '5.4',
      'flag': 'critical',
      'result_notes': 'Critical threshold crossed',
    },
  ],
};

Map<String, dynamic> _page({
  List<Map<String, dynamic>>? rows,
  int? total,
  int page = 1,
  int perPage = 20,
}) {
  final result = rows ?? [_order()];
  return {
    'success': true,
    'data': {
      'items': result,
      'total': total ?? result.length,
      'page': page,
      'per_page': perPage,
    },
  };
}

LabOrderReadRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => LabOrderReadRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _ready(role: role),
  sessionNavigationGuard: guard,
);

Widget _host(
  LabOrderReadRemoteDataSource source, {
  int patientId = 7,
  int pageSize = 20,
}) => MaterialApp(
  home: LabPatientOrdersScreen(
    patientId: patientId,
    dataSource: source,
    pageSize: pageSize,
  ),
);

void main() {
  testWidgets('GET displays backend critical result without acknowledgement', (
    tester,
  ) async {
    final requests = <String>[];
    final client = MockClient((request) async {
      requests.add(request.url.path);
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(requests, ['/api/v1/lab/orders']);
    expect(find.text('Status: Completed'), findsOneWidget);
    expect(find.text('Recorded result: 5.4'), findsOneWidget);
    expect(find.byKey(const ValueKey('lab-critical-result')), findsOneWidget);
    expect(find.textContaining('no acknowledgement recorded'), findsOneWidget);
  });

  testWidgets('explicit empty page does not claim absent history', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(rows: [], total: 0)), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('lab-orders-empty')), findsOneWidget);
  });

  testWidgets('on-demand detail refresh uses second GET and checks scope', (
    tester,
  ) async {
    final paths = <String>[];
    final client = MockClient((request) async {
      paths.add(request.url.path);
      if (request.url.path.endsWith('/12')) {
        return http.Response(
          jsonEncode({'success': true, 'data': _order()}),
          200,
        );
      }
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    final detailButton = find.byKey(const ValueKey('lab-view-detail-12'));
    await tester.ensureVisible(detailButton);
    await tester.pumpAndSettle();
    await tester.tap(detailButton);
    await tester.pumpAndSettle();
    expect(paths, ['/api/v1/lab/orders', '/api/v1/lab/orders/12']);
    expect(find.text('Refreshed server order detail'), findsOneWidget);
    expect(find.byKey(const ValueKey('lab-detail-12')), findsOneWidget);
  });

  testWidgets('unauthorized pharmacist makes no lab GET', (tester) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard, role: 'pharmacist')));
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(find.byKey(const ValueKey('lab-read-error')), findsOneWidget);
    expect(find.text('Recorded result: 5.4'), findsNothing);
  });

  testWidgets('HTTP 403 never leaves stale lab records visible', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      if (calls == 2) {
        return http.Response('{"error":"Forbidden"}', 403);
      }
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.text('Recorded result: 5.4'), findsOneWidget);
    final refresh = find.byKey(const ValueKey('lab-orders-refresh'));
    await tester.ensureVisible(refresh);
    await tester.pumpAndSettle();
    await tester.tap(refresh);
    await tester.pumpAndSettle();
    expect(calls, 2);
    expect(find.text('Recorded result: 5.4'), findsNothing);
    expect(find.byKey(const ValueKey('lab-read-error')), findsOneWidget);
  });

  testWidgets('logout purges displayed lab result PHI', (tester) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page()), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.text('Recorded result: 5.4'), findsOneWidget);
    guard.invalidate();
    await tester.pump();
    expect(find.text('Recorded result: 5.4'), findsNothing);
    expect(find.byKey(const ValueKey('lab-read-error')), findsOneWidget);
  });

  testWidgets('patient switch ignores older pending response', (tester) async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
    final client = MockClient((request) {
      if (request.url.queryParameters['patient_id'] == '7') {
        started.complete();
        return pending.future;
      }
      return Future.value(
        http.Response(
          jsonEncode(_page(rows: [_order(patientId: 8, id: 22)])),
          200,
        ),
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await tester.pumpWidget(_host(source));
    await started.future;
    await tester.pumpWidget(_host(source, patientId: 8));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('lab-order-22')), findsOneWidget);
    pending.complete(http.Response(jsonEncode(_page()), 200));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('lab-order-12')), findsNothing);
    expect(find.byKey(const ValueKey('lab-order-22')), findsOneWidget);
  });
}
