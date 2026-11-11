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
import 'package:clinic_system_pro/features/feedback/data/feedback_remote_data_source.dart';
import 'package:clinic_system_pro/features/feedback/presentation/feedback_workspace_screen.dart';

Map<String, dynamic> _feedback() => {
  'id': 11,
  'clinic_id': 3,
  'submitted_by_user_id': 42,
  'feedback_type': 'bug_report',
  'category': 'usability',
  'subject': 'Chart issue',
  'message': 'An authorized chart issue',
  'status': 'open',
  'priority': 'normal',
  'source': 'api',
  'resolution_note': null,
  'created_at': '2026-11-11T12:00:00Z',
};

http.Response _ok(Object payload) =>
    http.Response(jsonEncode({'success': true, 'data': payload}), 200);

FeedbackRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard,
) => FeedbackRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => AuthSessionState.ready(
    const AuthTokenResponse(
      accessToken: 'token',
      refreshToken: 'refresh',
      userId: 42,
      role: 'patient',
      clinicContextId: 3,
    ),
  ),
  sessionNavigationGuard: guard,
);

void main() {
  testWidgets(
    'feedback workspace loads scoped list and opens detail/comments',
    (tester) async {
      final client = MockClient((req) async {
        expect(req.headers['authorization'], 'Bearer token');
        if (req.url.path == '/api/v1/feedback') {
          return _ok({
            'items': [_feedback()],
            'page': 1,
            'per_page': 20,
            'total': 1,
            'pages': 1,
            'has_next': false,
            'has_previous': false,
          });
        }
        if (req.url.path == '/api/v1/feedback/11') {
          return _ok(_feedback());
        }
        if (req.url.path == '/api/v1/feedback/11/comments') {
          return _ok({
            'items': <Object>[],
            'page': 1,
            'per_page': 20,
            'total': 0,
            'pages': 0,
            'has_next': false,
            'has_previous': false,
          });
        }
        throw StateError('Unexpected request: ${req.url}');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: FeedbackWorkspaceScreen(source: _source(client, guard)),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p24h-feedback-workspace')),
        findsOneWidget,
      );
      expect(find.text('Chart issue'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('p24h-feedback-item-11')));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p24h-feedback-detail-screen')),
        findsOneWidget,
      );
      expect(find.text('An authorized chart issue'), findsOneWidget);
      expect(find.byKey(const ValueKey('p24h-comment-send')), findsOneWidget);
      expect(find.byKey(const ValueKey('p24h-status-triaged')), findsNothing);
    },
  );

  testWidgets('feedback workspace hides clinic data after guard invalidation', (
    tester,
  ) async {
    final client = MockClient(
      (req) async => _ok({
        'items': [_feedback()],
        'page': 1,
        'per_page': 20,
        'total': 1,
        'pages': 1,
        'has_next': false,
        'has_previous': false,
      }),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: FeedbackWorkspaceScreen(source: _source(client, guard)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Chart issue'), findsOneWidget);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Chart issue'), findsNothing);
    expect(find.text('Feedback unavailable. Sign in again.'), findsOneWidget);
  });

  testWidgets('feedback create form is online-only and does not auto-submit', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((req) async {
      requests++;
      return _ok({
        'items': <Object>[],
        'page': 1,
        'per_page': 20,
        'total': 0,
        'pages': 0,
        'has_next': false,
        'has_previous': false,
      });
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await tester.pumpWidget(
      MaterialApp(home: FeedbackWorkspaceScreen(source: source)),
    );
    await tester.pumpAndSettle();
    final reads = requests;
    await tester.tap(find.byKey(const ValueKey('p24h-feedback-create')));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('p24h-feedback-create-screen')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('p24h-submit')), findsOneWidget);
    expect(requests, reads);
  });
}
