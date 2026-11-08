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
import 'package:clinic_system_pro/features/nursing/data/nursing_note_workflow_remote_data_source.dart';
import 'package:clinic_system_pro/features/nursing/presentation/nursing_notes_screen.dart';

Map<String, dynamic> _note({
  String status = 'draft',
  String record = 'draft',
}) => {
  'id': 20,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 11,
  'author_staff_id': 8,
  'note_type': 'progress',
  'status': status,
  'record_status': record,
  'record_version': 1,
  'version': 1,
  'note_text': 'Documented patient progress',
  'observed_at': '2026-11-08T10:00:00+00:00',
  'signed_at': status == 'signed' ? '2026-11-08T10:30:00+00:00' : null,
};

http.Response _get(http.Request request, {bool existing = true}) {
  if (request.url.path.endsWith('/current-admission')) {
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
  if (request.url.path.endsWith('/admissions/11/notes')) {
    return http.Response(
      jsonEncode({
        'items': existing ? [_note()] : <Object>[],
        'total': existing ? 1 : 0,
      }),
      200,
    );
  }
  return http.Response('{}', 404);
}

Widget _app(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'nurse',
}) {
  final source = NursingNoteWorkflowRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: () async => AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'widget-note-token',
        refreshToken: 'refresh',
        userId: 42,
        role: role,
        clinicContextId: 3,
      ),
    ),
    sessionNavigationGuard: guard,
  );
  return MaterialApp(home: NursingNotesScreen(dataSource: source));
}

void _wide(WidgetTester tester) {
  tester.view.devicePixelRatio = 1.0;
  tester.view.physicalSize = const Size(1200, 3500);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

Future<void> _review(WidgetTester tester) async {
  await tester.enterText(find.byKey(const ValueKey('p23e-e-patient')), '7');
  await tester.tap(find.byKey(const ValueKey('p23e-e-review')));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('nurse sees notes and no writes occur on review', (tester) async {
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
    expect(find.byKey(const ValueKey('p23e-e-note-20')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23e-e-create')), findsOneWidget);
    expect(writes, 0);
  });

  testWidgets('doctor can read but cannot create a nursing draft', (
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
    await tester.pumpWidget(_app(client, guard, role: 'doctor'));
    await _review(tester);
    final button = tester.widget<FilledButton>(
      find.byKey(const ValueKey('p23e-e-create')),
    );
    expect(button.onPressed, isNull);
    expect(find.textContaining('Read-only'), findsOneWidget);
    expect(writes, 0);
  });

  testWidgets('signout clears nursing notes and editor contents', (
    tester,
  ) async {
    _wide(tester);
    final client = MockClient((req) async => _get(req));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await _review(tester);
    await tester.tap(find.byKey(const ValueKey('p23e-e-note-20')));
    await tester.pumpAndSettle();
    expect(find.text('Documented patient progress'), findsWidgets);
    guard.markSignedOut();
    await tester.pumpAndSettle();
    expect(find.text('Documented patient progress'), findsNothing);
    expect(find.byKey(const ValueKey('p23e-e-unavailable')), findsOneWidget);
  });

  testWidgets(
    'lost create POST locks further writes and shows reconciliation',
    (tester) async {
      _wide(tester);
      var posts = 0;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          posts++;
          throw http.ClientException('lost server response');
        }
        return _get(req, existing: false);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(_app(client, guard));
      await _review(tester);
      await tester.enterText(
        find.byKey(const ValueKey('p23e-e-text')),
        'Recorded clinical observation',
      );
      await tester.tap(find.byKey(const ValueKey('p23e-e-create')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23e-e-confirm')), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('p23e-e-confirm')));
      await tester.pumpAndSettle();
      expect(posts, 1);
      expect(find.byKey(const ValueKey('p23e-e-write-locked')), findsOneWidget);
      expect(find.textContaining('Outcome not confirmed'), findsOneWidget);
    },
  );

  testWidgets('patient changes invalidate prior note list', (tester) async {
    _wide(tester);
    final client = MockClient((req) async => _get(req));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await _review(tester);
    expect(find.byKey(const ValueKey('p23e-e-note-20')), findsOneWidget);
    await tester.enterText(find.byKey(const ValueKey('p23e-e-patient')), '8');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23e-e-note-20')), findsNothing);
    expect(
      find.textContaining('Review the active admission again'),
      findsOneWidget,
    );
  });
}
