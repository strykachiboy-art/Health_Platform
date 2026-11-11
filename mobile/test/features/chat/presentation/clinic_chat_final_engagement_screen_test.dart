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
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/presentation/clinic_chat_final_engagement_screen.dart';

void main() {
  testWidgets('server count visible, session loss clears state, no writes', (
    tester,
  ) async {
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method != 'GET') writes++;
      final data = request.url.path.endsWith('/unread')
          ? {
              'conversation_id': 10,
              'clinic_id': 3,
              'user_id': 42,
              'unread_count': 4,
              'last_read_message_id': null,
            }
          : {
              'id': 10,
              'clinic_id': 3,
              'conversation_type': 'direct',
              'status': 'active',
              'title': 'Team',
              'description': null,
              'created_by_id': 42,
              'patient_id': null,
              'last_message_at': null,
              'created_at': '2026-11-10T09:00:00Z',
            };
      return http.Response(jsonEncode({'success': true, 'data': data}), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final read = ClinicChatReadRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
      sessionNavigationGuard: guard,
      loadSession: () async => const AuthSessionState.ready(
        AuthTokenResponse(
          accessToken: 'a',
          refreshToken: 'b',
          userId: 42,
          role: 'doctor',
          clinicContextId: 3,
        ),
      ),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicChatFinalEngagementScreen(read: read, conversationId: 10),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Unread messages: 4'), findsOneWidget);
    expect(writes, 0);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Unread messages: 4'), findsNothing);
    expect(
      find.byKey(const ValueKey('p24g-final-session-locked')),
      findsOneWidget,
    );
    expect(writes, 0);
  });
}
