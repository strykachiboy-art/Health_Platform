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
import 'package:clinic_system_pro/features/clinical_orders/presentation/blood_product_orders_screen.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

AuthSessionState _ready(String role) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'token',
    refreshToken: 'refresh',
    userId: 41,
    role: role,
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _row({
  String compatibility = 'pending',
  int version = 1,
  String status = 'active',
}) => <String, dynamic>{
  'id': 17,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 9,
  'ordered_by_staff_id': 8,
  'product_type': 'packed_red_blood_cells',
  'units_requested': 2,
  'units_issued': status == 'completed' ? 1 : 0,
  'indication': 'Symptomatic bleeding',
  'priority': 'stat',
  'status': status,
  'version': version,
  'patient_blood_type_at_order': 'O+',
  'compatibility_status': compatibility,
  'patient_blood_type_verified': compatibility == 'pending' ? null : 'O+',
  'compatibility_verified_by_staff_id': compatibility == 'pending' ? null : 11,
  'compatibility_verified_at': compatibility == 'pending'
      ? null
      : '2026-11-07T09:00:00+00:00',
  'crossmatch_reference': compatibility == 'compatible' ? 'CROSS-88' : null,
  'ordered_at': '2026-11-07T08:00:00+00:00',
  'completed_at': status == 'completed' ? '2026-11-07T09:30:00+00:00' : null,
  'cancelled_at': null,
  'fulfilled_by_staff_id': status == 'completed' ? 11 : null,
};

Widget _app(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
  int? patientId = 7,
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
    home: BloodProductOrdersScreen(
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
      patientId: patientId,
    ),
  );
}

void _wide(WidgetTester tester) {
  tester.view.devicePixelRatio = 1.0;
  tester.view.physicalSize = const Size(1400, 3200);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
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
  testWidgets('doctor sees admission-verified blood products', (tester) async {
    _wide(tester);
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) return _admission();
      expect(req.url.path, '/api/v1/clinical-orders/blood-products');
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
    expect(find.byKey(const ValueKey('p23d-d-ready')), findsOneWidget);
    expect(find.textContaining('Verified admission #9'), findsOneWidget);
    expect(find.byKey(const ValueKey('p23d-d-create')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23d-d-verify-17')), findsNothing);
  });

  testWidgets('doctor sends one create and disables further mutations', (
    tester,
  ) async {
    _wide(tester);
    var posts = 0;
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) return _admission();
      if (req.method == 'POST') {
        posts++;
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
    await tester.enterText(
      find.byKey(const ValueKey('p23d-d-request-units')),
      '2',
    );
    await tester.enterText(
      find.byKey(const ValueKey('p23d-d-indication')),
      'Symptomatic bleeding',
    );
    await tester.tap(find.byKey(const ValueKey('p23d-d-create')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('Server confirmed order #17'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23d-d-create')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('uncertain create remains locked without automatic retry', (
    tester,
  ) async {
    _wide(tester);
    var posts = 0;
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) return _admission();
      if (req.method == 'POST') {
        posts++;
        return http.Response('unavailable', 503);
      }
      return http.Response(jsonEncode({'items': [], 'total': 0}), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('p23d-d-request-units')),
      '2',
    );
    await tester.enterText(
      find.byKey(const ValueKey('p23d-d-indication')),
      'Symptomatic bleeding',
    );
    await tester.tap(find.byKey(const ValueKey('p23d-d-create')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('Outcome not confirmed'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23d-d-create')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('lab technician uses order detail without admission GET', (
    tester,
  ) async {
    _wide(tester);
    var detail = 0;
    final client = MockClient((req) async {
      expect(req.url.path, '/api/v1/clinical-orders/blood-products/17');
      detail++;
      return http.Response(jsonEncode(_row()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _app(client, guard, role: 'lab_technician', patientId: null),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23d-d-create')), findsNothing);
    await tester.enterText(find.byKey(const ValueKey('p23d-d-order-id')), '17');
    await tester.tap(find.byKey(const ValueKey('p23d-d-lookup')));
    await tester.pumpAndSettle();
    expect(detail, 1);
    expect(find.byKey(const ValueKey('p23d-d-verify-17')), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23d-d-verify-17')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('lab attestation enables verification; one write locks UI', (
    tester,
  ) async {
    _wide(tester);
    var posts = 0;
    final client = MockClient((req) async {
      if (req.method == 'GET') return http.Response(jsonEncode(_row()), 200);
      posts++;
      expect(
        req.url.path,
        '/api/v1/clinical-orders/blood-products/17/verify-compatibility',
      );
      return http.Response(
        jsonEncode({'order': _row(compatibility: 'compatible', version: 2)}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _app(client, guard, role: 'lab_technician', patientId: null),
    );
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('p23d-d-order-id')), '17');
    await tester.tap(find.byKey(const ValueKey('p23d-d-lookup')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('p23d-d-crossmatch-17')),
      'CROSS-88',
    );
    await tester.tap(
      find.byKey(const ValueKey('p23d-d-verify-attestation-17')),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23d-d-verify-17')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('Server confirmed order #17'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23d-d-verify-17')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('lab can issue only after recorded compatibility', (
    tester,
  ) async {
    _wide(tester);
    var posts = 0;
    final client = MockClient((req) async {
      if (req.method == 'GET') {
        return http.Response(
          jsonEncode(_row(compatibility: 'compatible', version: 2)),
          200,
        );
      }
      posts++;
      expect(req.url.path, '/api/v1/clinical-orders/blood-products/17/fulfill');
      return http.Response(
        jsonEncode({
          'order': <String, dynamic>{
            ..._row(
              compatibility: 'compatible',
              version: 3,
              status: 'completed',
            ),
          },
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _app(client, guard, role: 'lab_technician', patientId: null),
    );
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('p23d-d-order-id')), '17');
    await tester.tap(find.byKey(const ValueKey('p23d-d-lookup')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('p23d-d-issue-note-17')),
      'Issued by laboratory',
    );
    await tester.tap(find.byKey(const ValueKey('p23d-d-issue-attestation-17')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23d-d-fulfill-17')));
    await tester.pumpAndSettle();
    expect(posts, 1);
  });

  testWidgets('invalidated session clears displayed order and write controls', (
    tester,
  ) async {
    _wide(tester);
    final client = MockClient((req) async {
      return http.Response(jsonEncode(_row()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _app(client, guard, role: 'lab_technician', patientId: null),
    );
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('p23d-d-order-id')), '17');
    await tester.tap(find.byKey(const ValueKey('p23d-d-lookup')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Patient #7'), findsWidgets);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.textContaining('Patient #7'), findsNothing);
    expect(find.byKey(const ValueKey('p23d-d-verify-17')), findsNothing);
  });
}
