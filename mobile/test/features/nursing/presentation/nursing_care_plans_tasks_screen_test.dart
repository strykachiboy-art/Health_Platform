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
import 'package:clinic_system_pro/features/nursing/data/nursing_care_plan_task_remote_data_source.dart';
import 'package:clinic_system_pro/features/nursing/presentation/nursing_care_plans_tasks_screen.dart';

Map<String, dynamic> plan() => {
  'id': 20,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 11,
  'created_by_staff_id': 8,
  'title': 'Mobility plan',
  'problem': 'Limited mobility',
  'goal': 'Safe assisted walking',
  'status': 'active',
  'started_at': '2026-11-08T09:00:00+00:00',
  'version': 1,
};

Map<String, dynamic> task() => {
  'id': 30,
  'care_plan_id': 20,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 11,
  'created_by_staff_id': 8,
  'description': 'Assess walking',
  'priority': 'routine',
  'status': 'pending',
  'version': 1,
};

http.Response getData(http.Request req) {
  final path = req.url.path;
  if (path.endsWith('/current-admission')) {
    return http.Response(
      jsonEncode({
        'id': 11,
        'patient_id': 7,
        'bed_id': 9,
        'admitted_by_id': 8,
        'status': 'admitted',
      }),
      200,
    );
  }
  if (path.endsWith('/admissions/11/care-plans')) {
    return http.Response(
      jsonEncode({
        'items': [plan()],
        'total': 1,
      }),
      200,
    );
  }
  if (path.endsWith('/admissions/11/tasks')) {
    return http.Response(
      jsonEncode({
        'items': [task()],
        'total': 1,
      }),
      200,
    );
  }
  if (path.endsWith('/care-plans/20')) {
    return http.Response(jsonEncode(plan()), 200);
  }
  return http.Response('{}', 404);
}

Widget app(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'nurse',
}) {
  final source = NursingCarePlanTaskRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: () async => AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'screen-token',
        refreshToken: 'refresh',
        userId: 42,
        role: role,
        clinicContextId: 3,
      ),
    ),
    sessionNavigationGuard: guard,
  );
  return MaterialApp(home: NursingCarePlansTasksScreen(dataSource: source));
}

void wide(WidgetTester tester) {
  tester.view.devicePixelRatio = 1.0;
  tester.view.physicalSize = const Size(1200, 3400);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

Future<void> review(WidgetTester tester) async {
  await tester.enterText(find.byKey(const ValueKey('p23e-d-patient')), '7');
  await tester.tap(find.byKey(const ValueKey('p23e-d-review')));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('nurse sees clinical plans and tasks from live GET only', (
    tester,
  ) async {
    wide(tester);
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method != 'GET') {
        writes++;
      }
      return getData(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await review(tester);
    expect(find.text('Mobility plan'), findsOneWidget);
    expect(find.text('Assess walking'), findsOneWidget);
    expect(find.byKey(const ValueKey('p23e-d-create-plan')), findsOneWidget);
    expect(writes, 0);
  });

  testWidgets('doctor can review but is not offered active writes', (
    tester,
  ) async {
    wide(tester);
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method != 'GET') {
        writes++;
      }
      return getData(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard, role: 'doctor'));
    await review(tester);
    final button = tester.widget<FilledButton>(
      find.byKey(const ValueKey('p23e-d-create-plan')),
    );
    expect(button.onPressed, isNull);
    expect(find.textContaining('Read only'), findsOneWidget);
    expect(writes, 0);
  });

  testWidgets('signout immediately removes all patient nursing content', (
    tester,
  ) async {
    wide(tester);
    final client = MockClient((req) async => getData(req));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await review(tester);
    expect(find.text('Mobility plan'), findsOneWidget);
    guard.markSignedOut();
    await tester.pumpAndSettle();
    expect(find.text('Mobility plan'), findsNothing);
    expect(find.text('Assess walking'), findsNothing);
    expect(find.byKey(const ValueKey('p23e-d-unavailable')), findsOneWidget);
  });

  testWidgets('unknown POST outcome locks future clinical actions', (
    tester,
  ) async {
    wide(tester);
    var posts = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        posts++;
        throw http.ClientException('response unavailable');
      }
      return getData(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await review(tester);
    await tester.enterText(
      find.byKey(const ValueKey('p23e-d-title')),
      'New plan',
    );
    await tester.enterText(
      find.byKey(const ValueKey('p23e-d-problem')),
      'Problem',
    );
    await tester.enterText(find.byKey(const ValueKey('p23e-d-goal')), 'Goal');
    await tester.tap(find.byKey(const ValueKey('p23e-d-create-plan')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23e-d-confirm')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p23e-d-confirm')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.byKey(const ValueKey('p23e-d-write-locked')), findsOneWidget);
    expect(find.textContaining('Outcome not confirmed'), findsOneWidget);
  });

  testWidgets('patient ID change invalidates previous clinical review', (
    tester,
  ) async {
    wide(tester);
    final client = MockClient((req) async => getData(req));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(app(client, guard));
    await review(tester);
    expect(find.text('Mobility plan'), findsOneWidget);
    await tester.enterText(find.byKey(const ValueKey('p23e-d-patient')), '8');
    await tester.pumpAndSettle();
    expect(find.text('Mobility plan'), findsNothing);
    expect(find.textContaining('Review live admission again'), findsOneWidget);
  });
}
