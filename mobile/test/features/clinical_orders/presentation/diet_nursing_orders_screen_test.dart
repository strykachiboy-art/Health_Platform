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
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/presentation/diet_nursing_orders_screen.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

AuthSessionState _ready(String role) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'widget-token',
    refreshToken: 'refresh',
    userId: 42,
    role: role,
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _row({
  String kind = 'diets',
  String status = 'active',
  String frequency = 'once',
}) => <String, dynamic>{
  'id': 17,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 9,
  'ordered_by_staff_id': 8,
  'version': 1,
  'status': status,
  'priority': 'routine',
  'ordered_at': '2026-11-07T08:00:00+00:00',
  'completed_at': null,
  'cancelled_at': null,
  if (kind == 'diets') ...<String, dynamic>{
    'diet_type': 'regular',
    'feeding_route': 'oral',
  } else ...<String, dynamic>{
    'category': 'monitoring',
    'order_text': 'Neuro observations',
    'frequency': frequency,
  },
};

Widget _app(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
  String kind = 'diets',
}) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  Future<AuthSessionState> load() async => _ready(role);
  return MaterialApp(
    home: DietNursingOrdersScreen(
      unifiedSource: ClinicalOrdersRemoteDataSource(
        apiClient: api,
        loadSession: load,
        sessionNavigationGuard: guard,
      ),
      admissionDataSource: PatientAdmissionRemoteDataSource(
        apiClient: api,
        loadSession: load,
        sessionNavigationGuard: guard,
      ),
      patientId: 7,
      kind: kind,
    ),
  );
}

void _wide(WidgetTester tester) {
  tester.view.physicalSize = const Size(1400, 3000);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

http.Response _admission() => http.Response(
  jsonEncode({
    'id': 9,
    'patient_id': 7,
    'bed_id': 2,
    'admitted_by_id': 8,
    'status': 'admitted',
  }),
  200,
);

void main() {
  testWidgets('doctor sees diet orders for verified active admission', (
    tester,
  ) async {
    _wide(tester);
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) {
        return _admission();
      }
      expect(req.url.queryParameters['admission_id'], '9');
      return http.Response(
        jsonEncode({
          'items': [_row()],
          'total': 1,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23d-c-ready')), findsOneWidget);
    expect(find.textContaining('Verified admission #9'), findsOneWidget);
    expect(find.text('Diet: regular'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23d-c-create')))
          .onPressed,
      isNull,
    );
    expect(find.byKey(const ValueKey('p23d-c-complete-17')), findsOneWidget);
  });

  testWidgets('doctor creates one diet order and locks all writes', (
    tester,
  ) async {
    _wide(tester);
    var posts = 0;
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) {
        return _admission();
      }
      if (req.method == 'POST') {
        posts++;
        expect(req.url.path, '/api/v1/clinical-orders/diets');
        return http.Response(
          jsonEncode({'order': _row(), 'message': 'created'}),
          201,
        );
      }
      return http.Response(jsonEncode({'items': [], 'total': 0}), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23d-c-create')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('Server confirmed order #17'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23d-c-create')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('uncertain diet POST remains locked without a retry', (
    tester,
  ) async {
    _wide(tester);
    var posts = 0;
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) {
        return _admission();
      }
      if (req.method == 'POST') {
        posts++;
        throw http.ClientException('unknown outcome');
      }
      return http.Response(jsonEncode({'items': [], 'total': 0}), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23d-c-create')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('Outcome not confirmed'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23d-c-create')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('nurse executes once order but cannot create or complete', (
    tester,
  ) async {
    _wide(tester);
    var posts = 0;
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) {
        return _admission();
      }
      if (req.method == 'POST') {
        posts++;
        expect(
          req.url.path,
          '/api/v1/clinical-orders/nursing-orders/17/execute',
        );
        final complete = _row(kind: 'nursing-orders', status: 'completed');
        complete['completed_at'] = '2026-11-07T09:00:00+00:00';
        complete['version'] = 2;
        return http.Response(
          jsonEncode({
            'order': complete,
            'execution': {
              'id': 90,
              'nursing_order_id': 17,
              'clinic_id': 3,
              'patient_id': 7,
              'admission_id': 9,
              'performed_by_staff_id': 34,
              'performed_at': '2026-11-07T09:00:00+00:00',
              'result_note': 'Vitals taken',
            },
            'message': 'executed',
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'items': [_row(kind: 'nursing-orders')],
          'total': 1,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _app(client, guard, role: 'nurse', kind: 'nursing-orders'),
    );
    await tester.pumpAndSettle();
    expect(find.text('Nursing: Neuro observations'), findsOneWidget);
    expect(find.byKey(const ValueKey('p23d-c-create')), findsNothing);
    expect(find.byKey(const ValueKey('p23d-c-complete-17')), findsNothing);
    await tester.enterText(
      find.byKey(const ValueKey('p23d-c-action-note')),
      'Vitals taken',
    );
    await tester.tap(find.byKey(const ValueKey('p23d-c-execute-17')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('Server confirmed order #17'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23d-c-execute-17')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('admin is read-only for nursing orders', (tester) async {
    _wide(tester);
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) {
        return _admission();
      }
      return http.Response(
        jsonEncode({
          'items': [_row(kind: 'nursing-orders')],
          'total': 1,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _app(client, guard, role: 'admin', kind: 'nursing-orders'),
    );
    await tester.pumpAndSettle();
    expect(find.text('Nursing: Neuro observations'), findsOneWidget);
    expect(find.byKey(const ValueKey('p23d-c-create')), findsNothing);
    expect(find.byKey(const ValueKey('p23d-c-execute-17')), findsNothing);
    expect(find.textContaining('Read-only'), findsOneWidget);
  });

  testWidgets('sign-out clears diet data and all actions', (tester) async {
    _wide(tester);
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) {
        return _admission();
      }
      return http.Response(
        jsonEncode({
          'items': [_row()],
          'total': 1,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await tester.pumpAndSettle();
    expect(find.text('Diet: regular'), findsOneWidget);
    guard.markSignedOut();
    await tester.pumpAndSettle();
    expect(find.text('Diet: regular'), findsNothing);
    expect(find.byKey(const ValueKey('p23d-c-create')), findsNothing);
    expect(find.byKey(const ValueKey('p23d-c-unavailable')), findsOneWidget);
  });
}
