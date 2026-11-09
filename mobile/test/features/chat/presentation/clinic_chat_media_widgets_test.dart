import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_media_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/presentation/clinic_chat_media_widgets.dart';

void main() {
  testWidgets(
    'media buttons advertise camera, gallery, file and voice but remain locked on signout',
    (tester) async {
      final client = MockClient(
        (req) async => throw StateError('No HTTP expected'),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = ClinicChatMediaRemoteDataSource(
        read: ClinicChatReadRemoteDataSource(
          apiClient: ApiClient(
            config: const BackendConfig(
              baseUrl: 'http://127.0.0.1:5000',
              allowInsecureHttp: true,
            ),
            client: client,
          ),
          loadSession: () async => AuthSessionState.ready(
            const AuthTokenResponse(
              accessToken: 'x',
              refreshToken: 'y',
              userId: 11,
              role: 'nurse',
              clinicContextId: 3,
            ),
          ),
          sessionNavigationGuard: guard,
        ),
        client: client,
      );
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ClinicChatMediaComposer(
              source: source,
              conversationId: 10,
              enabled: true,
              onConfirmed: () {},
              onOutcomeUnconfirmed: () {},
            ),
          ),
        ),
      );
      for (final name in ['camera', 'gallery', 'document', 'record']) {
        expect(find.byKey(ValueKey('chat-f-$name')), findsOneWidget);
      }
      guard.markSignedOut();
      await tester.pump();
      for (final name in ['camera', 'gallery', 'document', 'record']) {
        final button = tester.widget<IconButton>(
          find.byKey(ValueKey('chat-f-$name')),
        );
        expect(button.onPressed, isNull);
      }
      source.dispose();
    },
  );
}
