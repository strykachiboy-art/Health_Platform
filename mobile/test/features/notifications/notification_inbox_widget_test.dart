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
import 'package:clinic_system_pro/features/notifications/data/notification_read_actions.dart';
import 'package:clinic_system_pro/features/notifications/data/notification_remote_data_source.dart';
import 'package:clinic_system_pro/features/notifications/presentation/notification_inbox_screen.dart';

Map<String, dynamic> record({bool read = false}) => {
  'id': 5,
  'clinic_id': 3,
  'user_id': 41,
  'title': 'Appointment reminder',
  'message': 'Appointment at clinic on Thursday',
  'notification_type': 'appointment',
  'priority': 'normal',
  'channel': 'in_app',
  'status': read ? 'read' : 'pending',
  'is_read': read,
  'read_at': read ? '2026-11-11T12:00:00+00:00' : null,
  'created_at': '2026-11-11T08:00:00+00:00',
  'reference_type': null,
  'reference_id': null,
};

Map<String, dynamic> listing({bool read = false}) => {
  'success': true,
  'data': {
    'items': [record(read: read)],
    'page': 1,
    'per_page': 20,
    'total': 1,
    'pages': 1,
    'has_next': false,
    'has_prev': false,
  },
};

AuthSessionState ready() => const AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'p24a-token',
    refreshToken: 'p24a-refresh',
    userId: 41,
    clinicContextId: 3,
    role: 'patient',
  ),
);

NotificationRemoteDataSource sourceFor(
  http.Client client,
  SessionNavigationGuard guard,
) => NotificationRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => ready(),
  sessionNavigationGuard: guard,
);

void main() {
  testWidgets('inbox opens verified detail and marks one read online', (
    tester,
  ) async {
    var read = false;
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        writes++;
        read = true;
        return http.Response(
          jsonEncode({'success': true, 'data': record(read: true)}),
          200,
        );
      }
      if (request.url.path.endsWith('/notifications/')) {
        return http.Response(jsonEncode(listing(read: read)), 200);
      }
      return http.Response(
        jsonEncode({'success': true, 'data': record(read: read)}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = sourceFor(client, guard);
    await tester.pumpWidget(
      MaterialApp(
        home: NotificationInboxScreen(
          source: source,
          actions: NotificationReadActions(source),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p24a-notification-5')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p24a-notification-5')));
    await tester.pumpAndSettle();
    expect(find.text('Appointment at clinic on Thursday'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('p24a-mark-one')));
    await tester.pumpAndSettle();
    expect(writes, 1);
    expect(find.byKey(const ValueKey('p24a-read-confirmed')), findsOneWidget);
  });

  testWidgets('read-all requires backend result and refreshes inbox', (
    tester,
  ) async {
    var writes = 0;
    var read = false;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        writes++;
        read = true;
        return http.Response(
          '{"success":true,"data":{"updated_count":1}}',
          200,
        );
      }
      return http.Response(jsonEncode(listing(read: read)), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = sourceFor(client, guard);
    await tester.pumpWidget(
      MaterialApp(
        home: NotificationInboxScreen(
          source: source,
          actions: NotificationReadActions(source),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p24a-read-all')));
    await tester.pumpAndSettle();
    expect(writes, 1);
    expect(find.textContaining('Read'), findsWidgets);
  });

  testWidgets('session invalidation clears PHI and blocks stale data', (
    tester,
  ) async {
    final client = MockClient((request) async {
      return http.Response(jsonEncode(listing()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = sourceFor(client, guard);
    await tester.pumpWidget(
      MaterialApp(
        home: NotificationInboxScreen(
          source: source,
          actions: NotificationReadActions(source),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Appointment reminder'), findsOneWidget);
    guard.invalidate();
    await tester.pump();
    expect(find.text('Appointment reminder'), findsNothing);
    expect(find.byKey(const ValueKey('p24a-error')), findsOneWidget);
  });
}
