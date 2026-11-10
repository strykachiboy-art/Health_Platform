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
import 'package:clinic_system_pro/features/lab/data/lab_technician_lifecycle_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/presentation/lab_technician_lifecycle_screen.dart';

AuthSessionState _ready({String role = 'lab_technician'}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'tech-screen-token',
        refreshToken: 'tech-screen-refresh',
        userId: 41,
        clinicContextId: 3,
        role: role,
      ),
    );

Map<String, dynamic> _order({bool collected = false}) => <String, dynamic>{
  'id': 31,
  'clinic_id': 3,
  'patient_id': 7,
  'consultation_id': null,
  'ordered_by_id': 18,
  'status': collected ? 'sample_collected' : 'ordered',
  'qr_code': 'ORDER-QR-31',
  'collected_by_id': collected ? 12 : null,
  'sample_collected_at': collected ? '2026-11-10T09:10:00+00:00' : null,
  'processed_by_id': null,
  'processed_at': null,
  'equipment_reference_id': null,
  'created_at': '2026-11-10T09:00:00+00:00',
  'items': <Map<String, dynamic>>[
    <String, dynamic>{
      'id': 131,
      'order_id': 31,
      'test_id': 10,
      'test': <String, dynamic>{
        'id': 10,
        'clinic_id': 3,
        'name': 'Hemoglobin',
        'sample_type': 'blood',
        'is_active': true,
      },
      'result_value': null,
      'flag': null,
      'result_notes': null,
    },
  ],
};

LabTechnicianLifecycleRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'lab_technician',
}) => LabTechnicianLifecycleRemoteDataSource(
  reader: LabOrderReadRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: () async => _ready(role: role),
    sessionNavigationGuard: guard,
  ),
);

Widget _host(LabTechnicianLifecycleRemoteDataSource source) => MaterialApp(
  home: LabTechnicianLifecycleScreen(
    patientId: 7,
    orderId: 31,
    action: LabTechnicianAction.collect,
    dataSource: source,
  ),
);

Future<void> _visible(WidgetTester tester, String key) async {
  final target = find.byKey(ValueKey(key));
  await tester.ensureVisible(target);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('blank QR cannot trigger a clinical POST', (tester) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 404);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.tap(find.byKey(const ValueKey('lab-tech-review')));
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(find.byKey(const ValueKey('lab-tech-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('lab-tech-submit')), findsNothing);
  });

  testWidgets('explicit acknowledgement precedes exactly one POST', (
    tester,
  ) async {
    final paths = <String>[];
    var posts = 0;
    final client = MockClient((request) async {
      paths.add('${request.method} ${request.url.path}');
      if (request.method == 'POST') {
        posts++;
      }
      return http.Response(
        jsonEncode(<String, dynamic>{
          'success': true,
          'data': _order(collected: posts > 0),
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.enterText(
      find.byKey(const ValueKey('lab-tech-qr')),
      'ORDER-QR-31',
    );
    await tester.tap(find.byKey(const ValueKey('lab-tech-review')));
    await tester.pumpAndSettle();
    await _visible(tester, 'lab-tech-submit');
    final before = tester.widget<ElevatedButton>(
      find.byKey(const ValueKey('lab-tech-submit')),
    );
    expect(before.onPressed, isNull);
    expect(posts, 0);
    await tester.tap(find.byKey(const ValueKey('lab-tech-ack')));
    await tester.pumpAndSettle();
    await _visible(tester, 'lab-tech-submit');
    await tester.tap(find.byKey(const ValueKey('lab-tech-submit')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(paths, <String>[
      'GET /api/v1/lab/orders/31',
      'GET /api/v1/lab/orders/31',
      'POST /api/v1/lab/orders/31/collect-sample',
      'GET /api/v1/lab/orders/31',
    ]);
    expect(find.byKey(const ValueKey('lab-tech-confirmed')), findsOneWidget);
    expect(find.byKey(const ValueKey('lab-tech-submit')), findsNothing);
  });

  testWidgets('doctor cannot review technician action', (tester) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard, role: 'doctor')));
    await tester.enterText(
      find.byKey(const ValueKey('lab-tech-qr')),
      'ORDER-QR-31',
    );
    await tester.tap(find.byKey(const ValueKey('lab-tech-review')));
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(find.byKey(const ValueKey('lab-tech-error')), findsOneWidget);
  });

  testWidgets('logout clears confirmation without sending POST', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
      }
      return http.Response(
        jsonEncode(<String, dynamic>{'success': true, 'data': _order()}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.enterText(
      find.byKey(const ValueKey('lab-tech-qr')),
      'ORDER-QR-31',
    );
    await tester.tap(find.byKey(const ValueKey('lab-tech-review')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('lab-tech-ack')), findsOneWidget);
    guard.invalidate();
    await tester.pump();
    expect(find.byKey(const ValueKey('lab-tech-ack')), findsNothing);
    expect(find.byKey(const ValueKey('lab-tech-submit')), findsNothing);
    expect(posts, 0);
  });
}
