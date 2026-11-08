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
import 'package:clinic_system_pro/features/nursing/data/ward_admission_workflow_remote_data_source.dart';
import 'package:clinic_system_pro/features/nursing/presentation/ward_admissions_transfers_screen.dart';

AuthSessionState _ready([String role = 'nurse']) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'token',
    refreshToken: 'refresh',
    userId: 41,
    role: role,
    clinicContextId: 3,
  ),
);

void _wide(WidgetTester tester) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(1400, 3000);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

Widget _app(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'nurse',
}) => MaterialApp(
  home: WardAdmissionsTransfersScreen(
    dataSource: WardAdmissionWorkflowRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
      loadSession: () async => _ready(role),
      sessionNavigationGuard: guard,
    ),
  ),
);

Map<String, dynamic> _admission() => {
  'id': 11,
  'patient_id': 7,
  'bed_id': 9,
  'admitted_by_id': 41,
  'reservation_id': null,
  'status': 'admitted',
};

void main() {
  testWidgets('read-only review exposes supported actions but sends no write', (
    tester,
  ) async {
    _wide(tester);
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        return http.Response('{}', 500);
      }
      if (req.url.path.endsWith('/current-admission')) {
        return http.Response('{"message":"No active admission found"}', 404);
      }
      if (req.url.path.endsWith('/reservation')) {
        return http.Response('{"message":"No active reservation found"}', 404);
      }
      if (req.url.path.endsWith('/beds/9')) {
        return http.Response(
          jsonEncode({
            'id': 9,
            'ward_id': 3,
            'bed_number': '09',
            'status': 'available',
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'id': 3,
          'clinic_id': 3,
          'name': 'General',
          'ward_type': 'general',
          'capacity': 20,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await tester.enterText(find.byKey(const ValueKey('p23e-c-patient')), '7');
    await tester.enterText(find.byKey(const ValueKey('p23e-c-bed')), '9');
    await tester.tap(find.byKey(const ValueKey('p23e-c-review')));
    await tester.pumpAndSettle();
    expect(find.text('Bed 09: available'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('p23e-c-action-admitDirect')),
      findsOneWidget,
    );
    expect(writes, 0);
  });

  testWidgets('one confirmed clinical action locks subsequent writes', (
    tester,
  ) async {
    _wide(tester);
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        return http.Response(jsonEncode({'admission': _admission()}), 201);
      }
      if (req.url.path.endsWith('/current-admission')) {
        return writes > 0
            ? http.Response(jsonEncode(_admission()), 200)
            : http.Response('{"message":"No active admission found"}', 404);
      }
      if (req.url.path.endsWith('/reservation')) {
        return http.Response('{"message":"No active reservation found"}', 404);
      }
      if (req.url.path.endsWith('/beds/9')) {
        return http.Response(
          jsonEncode({
            'id': 9,
            'ward_id': 3,
            'bed_number': '09',
            'status': writes > 0 ? 'occupied' : 'available',
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'id': 3,
          'clinic_id': 3,
          'name': 'General',
          'ward_type': 'general',
          'capacity': 20,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await tester.enterText(find.byKey(const ValueKey('p23e-c-patient')), '7');
    await tester.enterText(find.byKey(const ValueKey('p23e-c-bed')), '9');
    await tester.tap(find.byKey(const ValueKey('p23e-c-review')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23e-c-action-admitDirect')));
    await tester.pumpAndSettle();
    expect(writes, 0);
    await tester.tap(find.byKey(const ValueKey('p23e-c-confirm')));
    await tester.pumpAndSettle();
    expect(writes, 1);
    expect(find.byKey(const ValueKey('p23e-c-confirmed')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23e-c-write-locked')), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23e-c-review')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('session invalidation hides clinical context', (tester) async {
    _wide(tester);
    final client = MockClient((req) async {
      if (req.url.path.endsWith('/current-admission')) {
        return http.Response('{"message":"No active admission found"}', 404);
      }
      if (req.url.path.endsWith('/reservation')) {
        return http.Response('{"message":"No active reservation found"}', 404);
      }
      if (req.url.path.endsWith('/beds/9')) {
        return http.Response(
          jsonEncode({
            'id': 9,
            'ward_id': 3,
            'bed_number': '09',
            'status': 'available',
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'id': 3,
          'clinic_id': 3,
          'name': 'General',
          'ward_type': 'general',
          'capacity': 20,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await tester.enterText(find.byKey(const ValueKey('p23e-c-patient')), '7');
    await tester.enterText(find.byKey(const ValueKey('p23e-c-bed')), '9');
    await tester.tap(find.byKey(const ValueKey('p23e-c-review')));
    await tester.pumpAndSettle();
    expect(find.text('Patient 7 · General'), findsOneWidget);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Patient 7 · General'), findsNothing);
    expect(find.byKey(const ValueKey('p23e-c-unavailable')), findsOneWidget);
  });

  testWidgets('receptionist sees no enabled admission writes', (tester) async {
    _wide(tester);
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') writes++;
      if (req.url.path.endsWith('/current-admission')) {
        return http.Response('{"message":"No active admission found"}', 404);
      }
      if (req.url.path.endsWith('/reservation')) {
        return http.Response('{"message":"No active reservation found"}', 404);
      }
      if (req.url.path.endsWith('/beds/9')) {
        return http.Response(
          jsonEncode({
            'id': 9,
            'ward_id': 3,
            'bed_number': '09',
            'status': 'available',
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'id': 3,
          'clinic_id': 3,
          'name': 'General',
          'ward_type': 'general',
          'capacity': 20,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard, role: 'receptionist'));
    await tester.enterText(find.byKey(const ValueKey('p23e-c-patient')), '7');
    await tester.enterText(find.byKey(const ValueKey('p23e-c-bed')), '9');
    await tester.tap(find.byKey(const ValueKey('p23e-c-review')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Viewing only'), findsOneWidget);
    expect(
      tester
          .widget<OutlinedButton>(
            find.byKey(const ValueKey('p23e-c-action-admitDirect')),
          )
          .onPressed,
      isNull,
    );
    expect(writes, 0);
  });
}
