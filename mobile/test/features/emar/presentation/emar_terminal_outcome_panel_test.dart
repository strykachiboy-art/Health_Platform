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
import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/emar_terminal_outcome_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';
import 'package:clinic_system_pro/features/emar/presentation/emar_terminal_outcome_panel.dart';

AuthSessionState ready() => AuthSessionState.ready(
  const AuthTokenResponse(
    accessToken: 's05-panel-token',
    refreshToken: 's05-panel-refresh',
    userId: 41,
    role: 'nurse',
    clinicContextId: 3,
  ),
);

Map<String, dynamic> row({String status = 'due', String? reason}) => {
  'id': 21,
  'clinic_id': 3,
  'patient_id': 5,
  'admission_id': 7,
  'prescription_id': 17,
  'prescription_item_id': 19,
  'status': status,
  'record_status': status == 'due' ? 'draft' : 'final',
  'record_version': 1,
  'source_order_version': 2,
  'dose_value': '1.25',
  'dose_unit': 'mg',
  'route_code': 'oral',
  'scheduled_at': '2026-11-09T11:00:00Z',
  'held_at': status == 'held' ? '2026-11-09T11:05:00Z' : null,
  'hold_reason': status == 'held' ? reason : null,
};

Widget app(MockClient client, SessionNavigationGuard guard) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  loader() async => ready();
  return MaterialApp(
    home: EmarTerminalOutcomePanel(
      reviewed: EmarAdministration.fromJson(row(), expectedClinicId: 3),
      adapter: EmarTerminalOutcomeRemoteDataSource(
        apiClient: api,
        readSource: EmarReadRemoteDataSource(
          apiClient: api,
          loadSession: loader,
          sessionNavigationGuard: guard,
        ),
        loadSession: loader,
        sessionNavigationGuard: guard,
      ),
    ),
  );
}

void wide(WidgetTester tester) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(1100, 2000);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

void main() {
  testWidgets('terminal action stays disabled without reason and attestation', (
    tester,
  ) async {
    wide(tester);
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await tester.pumpAndSettle();
    final button = tester.widget<FilledButton>(
      find.byKey(const ValueKey('p23f-s05-submit')),
    );
    expect(button.onPressed, isNull);
    expect(requests, 0);
    expect(find.textContaining('late-dose status'), findsNothing);
  });

  testWidgets('one hold with reason and attestation, no second POST', (
    tester,
  ) async {
    wide(tester);
    var posts = 0;
    var gets = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        gets++;
        return http.Response(
          jsonEncode({
            'administration': gets == 1
                ? row()
                : row(status: 'held', reason: 'Clinical hold'),
          }),
          200,
        );
      }
      posts++;
      expect(request.url.path, '/api/v1/emar/21/hold');
      expect(jsonDecode(request.body), {'reason': 'Clinical hold'});
      return http.Response(
        jsonEncode({
          'administration': row(status: 'held', reason: 'Clinical hold'),
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await tester.enterText(
      find.byKey(const ValueKey('p23f-s05-reason')),
      'Clinical hold',
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23f-s05-attestation')));
    await tester.pumpAndSettle();
    final submit = find.byKey(const ValueKey('p23f-s05-submit'));
    await tester.ensureVisible(submit);
    await tester.tap(submit);
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(gets, 2);
    expect(
      find.textContaining('Server confirmed administration'),
      findsOneWidget,
    );
    final button = tester.widget<FilledButton>(submit);
    expect(button.onPressed, isNull);
  });

  testWidgets('session invalidation removes actionable clinical context', (
    tester,
  ) async {
    wide(tester);
    final client = MockClient((_) async => http.Response('{}', 500));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await tester.enterText(
      find.byKey(const ValueKey('p23f-s05-reason')),
      'Clinical hold',
    );
    guard.markSignedOut();
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('p23f-s05-session-unavailable')),
      findsOneWidget,
    );
    expect(find.text('Clinical hold'), findsNothing);
    expect(find.byKey(const ValueKey('p23f-s05-submit')), findsNothing);
  });

  testWidgets('unknown POST closes confirmation UI, no automatic retry', (
    tester,
  ) async {
    wide(tester);
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return http.Response(jsonEncode({'administration': row()}), 200);
      }
      posts++;
      throw http.ClientException('uncertain terminal POST');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await tester.enterText(
      find.byKey(const ValueKey('p23f-s05-reason')),
      'Clinical hold',
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23f-s05-attestation')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23f-s05-submit')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('Do NOT repeat this operation'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23f-s05-submit')))
          .onPressed,
      isNull,
    );
  });
}
