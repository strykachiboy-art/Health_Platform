import 'dart:async';
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
import 'package:clinic_system_pro/features/chat/presentation/clinic_chat_text_conversation_screen.dart';
import 'package:clinic_system_pro/features/nursing/data/nursing_handoff_care_team_remote_data_source.dart';
import 'package:clinic_system_pro/features/nursing/presentation/nursing_handoffs_care_team_screen.dart';

http.Response _json(Object value, [int status = 200]) =>
    http.Response(jsonEncode(value), status);

Map<String, dynamic> _conversation() => {
  'id': 51,
  'clinic_id': 3,
  'conversation_type': 'patient',
  'status': 'active',
  'title': 'Admission 11 clinical case',
  'description': null,
  'created_by_id': 42,
  'patient_id': 7,
  'last_message_at': null,
  'created_at': '2026-11-08T10:00:00Z',
};

http.Response _clinicalGet(http.Request req) {
  final path = req.url.path;
  if (path.endsWith('/current-admission')) {
    return _json({
      'id': 11, 'patient_id': 7, 'bed_id': 9,
      'admitted_by_id': 8, 'status': 'admitted',
    });
  }
  if (path.endsWith('/care-team/assignment-history')) {
    return _json({'items': <Object>[], 'total': 0});
  }
  if (path.endsWith('/care-team')) {
    return _json({
      'clinic_id': 3, 'patient_id': 7, 'admission_id': 11,
      'admission_status': 'admitted',
      'admission_active': true,
      'authoritative_assignment_available': true,
      'responsibility': {
        'primary_doctor_staff_id': null,
        'primary_nurse_staff_id': null,
        'primary_doctor': null,
        'primary_nurse': null,
        'primary_doctor_confirmed': false,
        'primary_nurse_confirmed': false,
        'confirmed': false,
      },
    });
  }
  if (path.endsWith('/admissions/11/handoffs')) {
    return _json({'items': <Object>[], 'total': 0});
  }
  throw StateError('Unexpected clinical GET: $path');
}

http.Response _caseLookup() => _json({
  'success': true,
  'data': {
    'clinic_id': 3,
    'patient_id': 7,
    'admission_id': 11,
    'conversation_id': 51,
    'conversation': _conversation(),
  },
});

NursingHandoffCareTeamRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard,
) => NursingHandoffCareTeamRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => AuthSessionState.ready(
    const AuthTokenResponse(
      accessToken: 'case-widget-token',
      refreshToken: 'refresh',
      userId: 42,
      role: 'nurse',
      clinicContextId: 3,
    ),
  ),
  sessionNavigationGuard: guard,
);

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
  testWidgets('case chat opens only after verified canonical lookup, no writes', (tester) async {
    _wide(tester);
    final paths = <String>[];
    final client = MockClient((req) async {
      expect(req.method, 'GET');
      paths.add(req.url.path);
      if (req.url.path.endsWith('/admissions/11/conversation')) {
        return _caseLookup();
      }
      if (req.url.path.endsWith('/conversations/51')) {
        return _json({'success': true, 'data': _conversation()});
      }
      return _clinicalGet(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(MaterialApp(home: NursingHandoffsCareTeamScreen(dataSource: _source(client, guard))));
    await _review(tester);
    await tester.tap(find.byKey(const ValueKey('chat-e-open-case')));
    for (var i = 0; i < 16 && find.byType(ClinicChatTextConversationScreen).evaluate().isEmpty; i++) {
      await tester.pump(const Duration(milliseconds: 20));
    }
    expect(paths, contains('/api/v1/chat/admissions/11/conversation'));
    expect(paths, contains('/api/v1/chat/conversations/51'));
    expect(find.byType(ClinicChatTextConversationScreen), findsOneWidget);
  });

  testWidgets('404 from canonical endpoint does not navigate or send writes', (tester) async {
    _wide(tester);
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method != 'GET') {
        writes++;
      }
      if (req.url.path.endsWith('/admissions/11/conversation')) {
        return _json({'success': false, 'error': 'Case conversation not found'}, 404);
      }
      return _clinicalGet(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(MaterialApp(home: NursingHandoffsCareTeamScreen(dataSource: _source(client, guard))));
    await _review(tester);
    await tester.tap(find.byKey(const ValueKey('chat-e-open-case')));
    await tester.pumpAndSettle();
    expect(find.byType(ClinicChatTextConversationScreen), findsNothing);
    expect(find.byKey(const ValueKey('chat-e-denied')), findsOneWidget);
    expect(writes, 0);
  });

  testWidgets('wrong patient in link fails closed before navigation', (tester) async {
    _wide(tester);
    final client = MockClient((req) async {
      if (req.url.path.endsWith('/admissions/11/conversation')) {
        return _json({
          'success': true,
          'data': {
            'clinic_id': 3, 'patient_id': 999,
            'admission_id': 11, 'conversation_id': 51,
            'conversation': _conversation(),
          },
        });
      }
      return _clinicalGet(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(MaterialApp(home: NursingHandoffsCareTeamScreen(dataSource: _source(client, guard))));
    await _review(tester);
    await tester.tap(find.byKey(const ValueKey('chat-e-open-case')));
    await tester.pumpAndSettle();
    expect(find.byType(ClinicChatTextConversationScreen), findsNothing);
    expect(find.byKey(const ValueKey('chat-e-denied')), findsOneWidget);
  });

  testWidgets('signout during pending link lookup prevents navigation', (tester) async {
    _wide(tester);
    final pending = Completer<http.Response>();
    final client = MockClient((req) async {
      if (req.url.path.endsWith('/admissions/11/conversation')) {
        return pending.future;
      }
      return _clinicalGet(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(MaterialApp(home: NursingHandoffsCareTeamScreen(dataSource: _source(client, guard))));
    await _review(tester);
    await tester.tap(find.byKey(const ValueKey('chat-e-open-case')));
    await tester.pump(const Duration(milliseconds: 40));
    guard.markSignedOut();
    pending.complete(_caseLookup());
    await tester.pumpAndSettle();
    expect(find.byType(ClinicChatTextConversationScreen), findsNothing);
    expect(find.byKey(const ValueKey('p23e-f-unavailable')), findsOneWidget);
  });
}
