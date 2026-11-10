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
import 'package:clinic_system_pro/features/lab/data/lab_result_finalization_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/presentation/lab_result_finalization_screen.dart';

AuthSessionState _ready({String role = 'lab_technician'}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'result-screen-token',
        refreshToken: 'result-screen-refresh',
        userId: 41,
        clinicContextId: 3,
        role: role,
      ),
    );

Map<String, dynamic> _order({bool resulted = false}) => <String, dynamic>{
  'id': 31,
  'clinic_id': 3,
  'patient_id': 7,
  'consultation_id': null,
  'ordered_by_id': 18,
  'status': 'in_progress',
  'qr_code': 'QR-31',
  'collected_by_id': 12,
  'sample_collected_at': '2026-11-10T09:05:00+00:00',
  'processed_by_id': 13,
  'processed_at': '2026-11-10T09:10:00+00:00',
  'verified_by_id': null,
  'verified_at': null,
  'completed_at': null,
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
        'critical_low': '5.0',
        'sample_type': 'blood',
        'is_active': true,
      },
      'result_value': resulted ? '4.2' : null,
      'flag': resulted ? 'critical' : null,
      'result_notes': null,
      'resulted_at': resulted ? '2026-11-10T09:20:00+00:00' : null,
      'result_file_url': null,
    },
  ],
};

LabResultFinalizationRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'lab_technician',
}) => LabResultFinalizationRemoteDataSource(
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

Widget _host(LabResultFinalizationRemoteDataSource source) => MaterialApp(
  home: LabResultFinalizationScreen(
    patientId: 7,
    orderId: 31,
    orderItemId: 131,
    action: LabResultAction.enterResult,
    dataSource: source,
  ),
);

Future<void> _show(WidgetTester tester, String key) async {
  final target = find.byKey(ValueKey(key));
  await tester.ensureVisible(target);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('empty result never issues clinical request', (tester) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.tap(find.byKey(const ValueKey('lab-result-review')));
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(find.byKey(const ValueKey('lab-result-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('lab-result-submit')), findsNothing);
  });

  testWidgets(
    'explicit confirmation precedes one result POST and critical readback',
    (tester) async {
      final paths = <String>[];
      var posts = 0;
      final client = MockClient((request) async {
        paths.add('${request.method} ${request.url.path}');
        if (request.method == 'POST') {
          posts++;
          return http.Response(
            jsonEncode(<String, dynamic>{
              'success': true,
              'data': _order(resulted: true)['items'][0],
            }),
            200,
          );
        }
        return http.Response(
          jsonEncode(<String, dynamic>{
            'success': true,
            'data': _order(resulted: posts > 0),
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(_host(_source(client, guard)));
      await tester.enterText(
        find.byKey(const ValueKey('lab-result-value')),
        '4.2',
      );
      await tester.tap(find.byKey(const ValueKey('lab-result-review')));
      await tester.pumpAndSettle();
      await _show(tester, 'lab-result-submit');
      expect(
        tester
            .widget<ElevatedButton>(
              find.byKey(const ValueKey('lab-result-submit')),
            )
            .onPressed,
        isNull,
      );
      expect(posts, 0);
      await tester.tap(find.byKey(const ValueKey('lab-result-ack')));
      await tester.pumpAndSettle();
      await _show(tester, 'lab-result-submit');
      await tester.tap(find.byKey(const ValueKey('lab-result-submit')));
      await tester.pumpAndSettle();
      expect(posts, 1);
      expect(paths, <String>[
        'GET /api/v1/lab/orders/31',
        'GET /api/v1/lab/orders/31',
        'POST /api/v1/lab/order-items/131/result',
        'GET /api/v1/lab/orders/31',
      ]);
      expect(
        find.byKey(const ValueKey('lab-result-confirmed')),
        findsOneWidget,
      );
      expect(find.textContaining('critical'), findsWidgets);
      expect(find.byKey(const ValueKey('lab-result-submit')), findsNothing);
    },
  );

  testWidgets('doctor cannot review laboratory technician result', (
    tester,
  ) async {
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
      find.byKey(const ValueKey('lab-result-value')),
      '4.2',
    );
    await tester.tap(find.byKey(const ValueKey('lab-result-review')));
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(find.byKey(const ValueKey('lab-result-error')), findsOneWidget);
  });

  testWidgets('session invalidation clears the review before POST', (
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
      find.byKey(const ValueKey('lab-result-value')),
      '4.2',
    );
    await tester.tap(find.byKey(const ValueKey('lab-result-review')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('lab-result-ack')), findsOneWidget);
    guard.invalidate();
    await tester.pump();
    expect(find.byKey(const ValueKey('lab-result-ack')), findsNothing);
    expect(posts, 0);
  });
}
