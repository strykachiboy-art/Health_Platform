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
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_state_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/presentation/clinic_chat_read_state_screen.dart';

void main() {
  testWidgets('read-only screen shows own server marker and no unread count', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      if (request.url.path.endsWith('/participants')) {
        return http.Response(
          jsonEncode({
            'data': [
              {
                'clinic_id': 3,
                'conversation_id': 7,
                'user_id': 41,
                'status': 'accepted',
                'last_read_message_id': 88,
              },
            ],
            'page': 1,
            'per_page': 100,
            'total': 1,
            'pages': 1,
            'has_next': false,
            'has_previous': false,
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'id': 7,
            'clinic_id': 3,
            'conversation_type': 'direct',
            'status': 'active',
            'title': 'Clinic thread',
            'description': null,
            'created_by_id': 41,
            'patient_id': null,
            'last_message_at': null,
            'created_at': '2026-11-10T09:00:00Z',
          },
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    final reader = ClinicChatReadRemoteDataSource(
      apiClient: api,
      loadSession: () async => const AuthSessionState.ready(
        AuthTokenResponse(
          accessToken: 'p24g-access',
          refreshToken: 'p24g-refresh',
          userId: 41,
          role: 'doctor',
          clinicContextId: 3,
        ),
      ),
      sessionNavigationGuard: guard,
    );
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicChatReadStateScreen(
          source: ClinicChatReadStateRemoteDataSource(read: reader),
          conversationId: 7,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Last-read message ID: 88'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('p24g-unread-not-claimed')),
      findsOneWidget,
    );
    expect(calls, 2);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Last-read message ID: 88'), findsNothing);
    expect(
      find.byKey(const ValueKey('p24g-read-state-locked')),
      findsOneWidget,
    );
    expect(calls, 2);
  });
}
