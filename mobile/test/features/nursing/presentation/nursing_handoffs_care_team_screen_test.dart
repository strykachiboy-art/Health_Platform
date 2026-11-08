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
import 'package:clinic_system_pro/features/nursing/data/nursing_handoff_care_team_remote_data_source.dart';
import 'package:clinic_system_pro/features/nursing/presentation/nursing_handoffs_care_team_screen.dart';

Map<String, dynamic> _assignment() => {
  'assignment_id': 1,
  'assignment_kind': 'primary_nurse',
  'staff_id': 9,
  'staff_user_id': 48,
  'staff_display_name': 'Current primary nurse',
  'staff_active': true,
  'assigned_at': '2026-11-08T08:00:00Z',
  'ended_at': null,
};

Map<String, dynamic> _handoff({String status = 'draft'}) => {
  'id': 20,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 11,
  'outgoing_staff_id': 9,
  'incoming_staff_id': 10,
  'status': status,
  'version': 1,
  'shift_start_at': '2026-11-08T08:00:00Z',
  'shift_end_at': '2026-11-08T16:00:00Z',
  'situation': 'Patient status stable',
  'background': 'Admitted yesterday',
  'assessment': 'Monitoring required',
  'recommendation': 'Continue observations',
  'safety_concerns': 'Check allergies',
  'pending_actions': 'Await blood results',
};

http.Response _get(
  http.Request request, {
  bool includeHandoff = true,
  String handoffStatus = 'draft',
}) {
  final path = request.url.path;
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
  if (path.endsWith('/care-team/assignment-history')) {
    return http.Response(
      jsonEncode({
        'items': [_assignment()],
        'total': 1,
      }),
      200,
    );
  }
  if (path.endsWith('/care-team')) {
    return http.Response(
      jsonEncode({
        'clinic_id': 3,
        'patient_id': 7,
        'admission_id': 11,
        'admission_status': 'admitted',
        'admission_active': true,
        'authoritative_assignment_available': true,
        'responsibility': {
          'primary_doctor_staff_id': null,
          'primary_nurse_staff_id': 9,
          'primary_doctor': null,
          'primary_nurse': _assignment(),
          'primary_doctor_confirmed': false,
          'primary_nurse_confirmed': true,
          'confirmed': false,
        },
      }),
      200,
    );
  }
  if (path.endsWith('/admissions/11/handoffs')) {
    final items = includeHandoff
        ? [_handoff(status: handoffStatus)]
        : <Object>[];
    return http.Response(
      jsonEncode({'items': items, 'total': items.length}),
      200,
    );
  }
  if (path.endsWith('/handoffs/20/summary')) {
    return http.Response(
      jsonEncode({
        'handoff': _handoff(status: handoffStatus),
        'critical_alerts': <Object>[],
        'pending_lab_orders': <Object>[],
        'due_medication_administrations': <Object>[],
        'tasks': <Object>[],
      }),
      200,
    );
  }
  throw StateError('Unexpected GET: $path');
}

Widget _app(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'nurse',
}) {
  final source = NursingHandoffCareTeamRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: () async => AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'handoff-widget-token',
        refreshToken: 'refresh',
        userId: 42,
        role: role,
        clinicContextId: 3,
      ),
    ),
    sessionNavigationGuard: guard,
  );
  return MaterialApp(home: NursingHandoffsCareTeamScreen(dataSource: source));
}

void _wide(WidgetTester tester) {
  tester.view.devicePixelRatio = 1.0;
  tester.view.physicalSize = const Size(1200, 3700);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

Future<void> _review(WidgetTester tester) async {
  await tester.enterText(find.byKey(const ValueKey('p23e-f-patient')), '7');
  await tester.tap(find.byKey(const ValueKey('p23e-f-load')));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
    'nurse can review care team and existing handoffs without writes',
    (tester) async {
      _wide(tester);
      var writes = 0;
      final client = MockClient((req) async {
        if (req.method != 'GET') {
          writes++;
        }
        return _get(req);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(_app(client, guard));
      await _review(tester);
      expect(find.textContaining('Current primary nurse'), findsWidgets);
      expect(find.byKey(const ValueKey('p23e-f-handoff-20')), findsOneWidget);
      expect(
        tester
            .widget<FilledButton>(find.byKey(const ValueKey('p23e-f-assign')))
            .onPressed,
        isNull,
      );
      expect(writes, 0);
    },
  );

  testWidgets('admin sees care team but cannot create a nursing handoff', (
    tester,
  ) async {
    _wide(tester);
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method != 'GET') {
        writes++;
      }
      return _get(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard, role: 'admin'));
    await _review(tester);
    expect(
      tester
          .widget<OutlinedButton>(find.byKey(const ValueKey('p23e-f-new')))
          .onPressed,
      isNull,
    );
    expect(writes, 0);
  });

  testWidgets('nurse cannot acknowledge before live summary preview', (
    tester,
  ) async {
    _wide(tester);
    final client = MockClient((req) async => _get(req, handoffStatus: 'ready'));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await _review(tester);
    await tester.tap(find.byKey(const ValueKey('p23e-f-handoff-20')));
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23e-f-ack')))
          .onPressed,
      isNull,
    );
    await tester.tap(find.byKey(const ValueKey('p23e-f-summary')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Clinical summary:'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23e-f-ack')))
          .onPressed,
      isNotNull,
    );
  });

  testWidgets('signout clears clinical handoff content immediately', (
    tester,
  ) async {
    _wide(tester);
    final client = MockClient((req) async => _get(req));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await _review(tester);
    await tester.tap(find.byKey(const ValueKey('p23e-f-handoff-20')));
    await tester.pumpAndSettle();
    expect(find.text('Patient status stable'), findsWidgets);
    guard.markSignedOut();
    await tester.pumpAndSettle();
    expect(find.text('Patient status stable'), findsNothing);
    expect(find.byKey(const ValueKey('p23e-f-unavailable')), findsOneWidget);
  });

  testWidgets(
    'switching patient clears previous handoff and care-team details',
    (tester) async {
      _wide(tester);
      final client = MockClient((req) async => _get(req));
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(_app(client, guard));
      await _review(tester);
      await tester.tap(find.byKey(const ValueKey('p23e-f-handoff-20')));
      await tester.pumpAndSettle();
      expect(find.text('Patient status stable'), findsWidgets);
      await tester.enterText(find.byKey(const ValueKey('p23e-f-patient')), '8');
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23e-f-handoff-20')), findsNothing);
      expect(find.text('Patient status stable'), findsNothing);
    },
  );

  testWidgets(
    'lost handoff POST locks UI and displays reconciliation warning',
    (tester) async {
      _wide(tester);
      var posts = 0;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          posts++;
          throw http.ClientException('server response lost');
        }
        return _get(req, includeHandoff: false);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(_app(client, guard));
      await _review(tester);
      await tester.enterText(
        find.byKey(const ValueKey('p23e-f-incoming')),
        '10',
      );
      await tester.enterText(
        find.byKey(const ValueKey('p23e-f-shift-start')),
        '2026-11-08T08:00:00Z',
      );
      await tester.enterText(
        find.byKey(const ValueKey('p23e-f-shift-end')),
        '2026-11-08T16:00:00Z',
      );
      await tester.enterText(
        find.byKey(const ValueKey('p23e-f-situation')),
        'Situation',
      );
      await tester.enterText(
        find.byKey(const ValueKey('p23e-f-assessment')),
        'Assessment',
      );
      await tester.enterText(
        find.byKey(const ValueKey('p23e-f-recommendation')),
        'Recommendation',
      );
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const ValueKey('p23e-f-create')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('p23e-f-create')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23e-f-confirm')), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('p23e-f-confirm')));
      await tester.pumpAndSettle();
      expect(posts, 1);
      expect(find.textContaining('Write outcome unconfirmed'), findsOneWidget);
      expect(find.textContaining('Further writes disabled'), findsOneWidget);
    },
  );
}
