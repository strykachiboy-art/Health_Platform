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
import 'package:clinic_system_pro/features/lab/data/lab_order_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/presentation/lab_order_mutation_screen.dart';

AuthSessionState _ready({String role = 'doctor'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's04b-widget-token',
    refreshToken: 's04b-widget-refresh',
    userId: 42,
    clinicContextId: 3,
    role: role,
  ),
);

Map<String, dynamic> _test() => <String, dynamic>{
  'id': 10,
  'clinic_id': 3,
  'name': 'Hemoglobin',
  'sample_type': 'blood',
  'is_active': true,
};

Map<String, dynamic> _order({String status = 'ordered'}) => <String, dynamic>{
  'id': 31,
  'clinic_id': 3,
  'patient_id': 7,
  'consultation_id': null,
  'ordered_by_id': 18,
  'status': status,
  'created_at': '2026-11-10T08:00:00+00:00',
  'items': <Map<String, dynamic>>[
    <String, dynamic>{
      'id': 131,
      'order_id': 31,
      'test_id': 10,
      'test': _test(),
      'result_value': null,
      'flag': null,
      'result_notes': null,
    },
  ],
};

Map<String, dynamic> _ok(Map<String, dynamic> data) => <String, dynamic>{
  'success': true,
  'data': data,
};

LabOrderMutationRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => LabOrderMutationRemoteDataSource(
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

Widget _host(LabOrderMutationRemoteDataSource source) =>
    MaterialApp(home: LabOrderMutationScreen(patientId: 7, dataSource: source));

Future<void> _view(WidgetTester tester, String key) async {
  final finder = find.byKey(ValueKey(key));
  await tester.scrollUntilVisible(
    finder,
    180,
    scrollable: find.byType(Scrollable).first,
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('empty test selection cannot produce a clinical POST', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
      }
      return http.Response('{}', 404);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await _view(tester, 'lab-mutation-review-create');
    await tester.tap(find.byKey(const ValueKey('lab-mutation-review-create')));
    await tester.pumpAndSettle();
    expect(posts, 0);
    expect(find.byKey(const ValueKey('lab-mutation-error')), findsOneWidget);
    expect(find.byKey(const ValueKey('lab-mutation-submit')), findsNothing);
  });

  testWidgets('doctor review requires explicit check before one POST and GET', (
    tester,
  ) async {
    final paths = <String>[];
    final client = MockClient((request) async {
      paths.add('${request.method} ${request.url.path}');
      if (request.url.path.endsWith('/tests/10')) {
        return http.Response(jsonEncode(_ok(_test())), 200);
      }
      if (request.method == 'POST') {
        return http.Response(jsonEncode(_ok(_order())), 201);
      }
      return http.Response(jsonEncode(_ok(_order())), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.enterText(
      find.byKey(const ValueKey('lab-mutation-test-ids')),
      '10',
    );
    await _view(tester, 'lab-mutation-review-create');
    await tester.tap(find.byKey(const ValueKey('lab-mutation-review-create')));
    await tester.pumpAndSettle();
    await _view(tester, 'lab-mutation-submit');
    final button = tester.widget<ElevatedButton>(
      find.byKey(const ValueKey('lab-mutation-submit')),
    );
    expect(button.onPressed, isNull);
    expect(paths, <String>['GET /api/v1/lab/tests/10']);
    await tester.tap(
      find.byKey(const ValueKey('lab-mutation-confirm-checkbox')),
    );
    await tester.pumpAndSettle();
    await _view(tester, 'lab-mutation-submit');
    await tester.tap(find.byKey(const ValueKey('lab-mutation-submit')));
    await tester.pumpAndSettle();
    expect(paths, <String>[
      'GET /api/v1/lab/tests/10',
      'GET /api/v1/lab/tests/10',
      'POST /api/v1/lab/orders',
      'GET /api/v1/lab/orders/31',
    ]);
    await _view(tester, 'lab-mutation-confirmed');
    expect(find.textContaining('Server-confirmed order #31'), findsOneWidget);
  });

  testWidgets('lab technician cannot review clinical write', (tester) async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _host(_source(client, guard, role: 'lab_technician')),
    );
    await tester.enterText(
      find.byKey(const ValueKey('lab-mutation-test-ids')),
      '10',
    );
    await _view(tester, 'lab-mutation-review-create');
    await tester.tap(find.byKey(const ValueKey('lab-mutation-review-create')));
    await tester.pumpAndSettle();
    expect(requests, 0);
    expect(find.byKey(const ValueKey('lab-mutation-error')), findsOneWidget);
  });

  testWidgets('session invalidation clears review without a POST', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
      }
      return http.Response(jsonEncode(_ok(_test())), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.enterText(
      find.byKey(const ValueKey('lab-mutation-test-ids')),
      '10',
    );
    await _view(tester, 'lab-mutation-review-create');
    await tester.tap(find.byKey(const ValueKey('lab-mutation-review-create')));
    await tester.pumpAndSettle();
    await _view(tester, 'lab-mutation-submit');
    expect(
      find.byKey(const ValueKey('lab-mutation-confirm-checkbox')),
      findsOneWidget,
    );
    guard.invalidate();
    await tester.pump();
    expect(find.byKey(const ValueKey('lab-mutation-submit')), findsNothing);
    expect(posts, 0);
  });
}
