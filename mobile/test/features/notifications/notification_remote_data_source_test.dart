import 'dart:convert';

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

Map<String, dynamic> notification({
  int clinic = 3,
  int user = 41,
  bool read = false,
}) => {
  'id': 5,
  'clinic_id': clinic,
  'user_id': user,
  'title': 'Lab update',
  'message': 'An update is available',
  'notification_type': 'lab',
  'priority': 'high',
  'channel': 'in_app',
  'status': read ? 'read' : 'pending',
  'is_read': read,
  'created_at': '2026-11-11T08:00:00+00:00',
  'read_at': read ? '2026-11-11T08:30:00+00:00' : null,
  'reference_type': null,
  'reference_id': null,
};

AuthSessionState ready({int? clinic = 3, String role = 'patient'}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'p24a-token',
        refreshToken: 'p24a-refresh',
        userId: 41,
        clinicContextId: clinic,
        role: role,
      ),
    );

ApiClient apiFor(http.Client client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

void main() {
  test(
    'GET list, unread, detail; POST read/read-all with exact envelope',
    () async {
      final requests = <String>[];
      final client = MockClient((request) async {
        expect(
          request.headers.entries
              .firstWhere((entry) => entry.key.toLowerCase() == 'authorization')
              .value,
          'Bearer p24a-token',
        );
        requests.add('${request.method} ${request.url.path}');
        if (request.method == 'GET' && request.url.path.endsWith('/unread')) {
          return http.Response(
            jsonEncode({
              'success': true,
              'data': {
                'items': [notification()],
                'page': 1,
                'per_page': 20,
                'total': 1,
                'pages': 1,
                'has_next': false,
                'has_prev': false,
              },
            }),
            200,
          );
        }
        if (request.method == 'GET' &&
            request.url.path.endsWith('/notifications/')) {
          return http.Response(
            jsonEncode({
              'success': true,
              'data': {
                'items': [notification()],
                'page': 1,
                'per_page': 20,
                'total': 1,
                'pages': 1,
                'has_next': false,
                'has_prev': false,
              },
            }),
            200,
          );
        }
        if (request.method == 'POST') {
          expect(jsonDecode(request.body), <String, dynamic>{});
          if (request.url.path.endsWith('/read-all')) {
            return http.Response(
              jsonEncode({
                'success': true,
                'data': {'updated_count': 1},
              }),
              200,
            );
          }
          return http.Response(
            jsonEncode({'success': true, 'data': notification(read: true)}),
            200,
          );
        }
        return http.Response(
          jsonEncode({'success': true, 'data': notification()}),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = NotificationRemoteDataSource(
        apiClient: apiFor(client),
        loadSession: () async => ready(),
        sessionNavigationGuard: guard,
      );
      expect((await source.list()).items.single.id, 5);
      expect((await source.list(unreadOnly: true)).items.single.isRead, false);
      expect((await source.detail(5)).id, 5);
      final actions = NotificationReadActions(source);
      expect((await actions.markOne(5)).isRead, true);
      expect(await actions.markAll(), 1);
      expect(requests, [
        'GET /api/v1/notifications/',
        'GET /api/v1/notifications/unread',
        'GET /api/v1/notifications/5',
        'POST /api/v1/notifications/5/read',
        'POST /api/v1/notifications/read-all',
      ]);
      await expectLater(
        actions.markOne(5),
        throwsA(isA<NotificationWriteOutcomeUnconfirmed>()),
      );
    },
  );

  test(
    'missing clinic, super-admin without context and signed-out block requests',
    () async {
      var count = 0;
      final client = MockClient((request) async {
        count++;
        return http.Response('{"success":true}', 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final missing = NotificationRemoteDataSource(
        apiClient: apiFor(client),
        loadSession: () async => ready(clinic: null, role: 'super_admin'),
        sessionNavigationGuard: guard,
      );
      await expectLater(
        missing.list(),
        throwsA(isA<NotificationSessionUnavailable>()),
      );
      guard.invalidate();
      await expectLater(
        missing.detail(5),
        throwsA(isA<NotificationSessionUnavailable>()),
      );
      expect(count, 0);
    },
  );

  test('rejects cross-clinic payload and concurrent session change', () async {
    AuthSessionState session = ready();
    var count = 0;
    final client = MockClient((request) async {
      count++;
      if (count == 2) session = ready(clinic: 4);
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {
            'items': [notification(clinic: count == 1 ? 4 : 3)],
            'page': 1,
            'per_page': 20,
            'total': 1,
            'pages': 1,
            'has_next': false,
            'has_prev': false,
          },
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = NotificationRemoteDataSource(
      apiClient: apiFor(client),
      loadSession: () async => session,
      sessionNavigationGuard: guard,
    );
    await expectLater(source.list(), throwsFormatException);
    expect(count, 1);
    await expectLater(
      source.list(),
      throwsA(isA<NotificationSessionUnavailable>()),
    );
    expect(count, 2);
  });

  test('ambiguous POST is locked; readback GET is still allowed', () async {
    var writes = 0, reads = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        writes++;
        throw http.ClientException('connection lost after request');
      }
      reads++;
      return http.Response(
        jsonEncode({'success': true, 'data': notification()}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = NotificationRemoteDataSource(
      apiClient: apiFor(client),
      loadSession: () async => ready(),
      sessionNavigationGuard: guard,
    );
    final actions = NotificationReadActions(source);
    await expectLater(
      actions.markOne(5),
      throwsA(isA<NotificationWriteOutcomeUnconfirmed>()),
    );
    await expectLater(
      actions.markOne(5),
      throwsA(isA<NotificationWriteOutcomeUnconfirmed>()),
    );
    expect(writes, 1);
    expect((await source.detail(5)).isRead, isFalse);
    expect(reads, 1);
  });
}
