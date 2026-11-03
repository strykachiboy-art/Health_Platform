import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_remote_data_source.dart';
import 'package:clinic_system_pro/features/emergency_access/presentation/emergency_access_review_screen.dart';

const _config = BackendConfig(
  baseUrl: 'http://127.0.0.1:5000',
  allowInsecureHttp: true,
);

Map<String, dynamic> _grantJson() {
  return <String, dynamic>{
    'id': 17,
    'clinic_id': 3,
    'patient_id': 7,
    'requester_user_id': 42,
    'requester_role': 'DOCTOR',
    'reason': 'Emergency treatment',
    'purpose': 'Immediate clinical review',
    'scope': <String>['patient:read'],
    'status': 'active',
    'requested_at': '2026-11-03T12:00:00+00:00',
    'granted_at': '2026-11-03T12:05:00+00:00',
    'expires_at': '2026-11-03T13:00:00+00:00',
    'revoked_at': null,
    'reviewed_at': '2026-11-03T12:05:00+00:00',
    'reviewed_by_user_id': 99,
    'review_notes': 'Approved',
  };
}

http.Response _activeResponse() {
  return http.Response(
    jsonEncode(<String, dynamic>{'success': true, 'data': _grantJson()}),
    200,
  );
}

EmergencyAccessRemoteDataSource _source(http.Client client) {
  return EmergencyAccessRemoteDataSource(
    apiClient: ApiClient(config: _config, client: client),
    loadAccessToken: () async => 'test-token',
  );
}

Future<void> _tap(WidgetTester tester, String key) async {
  final finder = find.byKey(ValueKey<String>(key));

  expect(finder, findsOneWidget);

  await tester.ensureVisible(finder);
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

Future<EmergencyAccessContext> _activate(
  WidgetTester tester,
  MockClient client,
) async {
  final context = EmergencyAccessContext(
    nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
  );

  await tester.pumpWidget(
    MaterialApp(
      home: EmergencyAccessReviewScreen(
        dataSource: _source(client),
        emergencyAccessId: 17,
        emergencyAccessContext: context,
      ),
    ),
  );

  await tester.pumpAndSettle();

  await _tap(tester, 'emergency-access-review-use-button');

  expect(context.activeEmergencyAccessId, 17);

  return context;
}

void main() {
  testWidgets('unknown revoke outcome clears active emergency context', (
    tester,
  ) async {
    var revokeAttempts = 0;

    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return _activeResponse();
      }

      if (request.method == 'POST' && request.url.path.endsWith('/revoke')) {
        revokeAttempts++;

        throw http.ClientException(
          'connection lost after request',
          request.url,
        );
      }

      throw StateError(
        'Unexpected request: '
        '${request.method} ${request.url}',
      );
    });
    addTearDown(client.close);

    final context = await _activate(tester, client);
    addTearDown(context.dispose);

    await tester.enterText(
      find.byKey(const ValueKey('emergency-access-review-revoke-reason')),
      'Emergency episode ended',
    );

    await _tap(tester, 'emergency-access-review-revoke-button');

    expect(revokeAttempts, 1);
    expect(context.activeEmergencyAccessId, isNull);
  });

  testWidgets(
    'HTTP 409 stale transition clears active context and requires reconciliation',
    (tester) async {
      var revokeAttempts = 0;

      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return _activeResponse();
        }

        if (request.method == 'POST' && request.url.path.endsWith('/revoke')) {
          revokeAttempts++;

          return http.Response(
            jsonEncode(<String, dynamic>{
              'success': false,
              'error': 'Emergency access request cannot be revoked from current status',
            }),
            409,
          );
        }

        throw StateError(
          'Unexpected request: '
          '${request.method} ${request.url}',
        );
      });
      addTearDown(client.close);

      final context = await _activate(tester, client);
      addTearDown(context.dispose);

      await tester.enterText(
        find.byKey(const ValueKey('emergency-access-review-revoke-reason')),
        'Emergency episode ended',
      );

      await _tap(tester, 'emergency-access-review-revoke-button');

      expect(revokeAttempts, 1);
      expect(context.activeEmergencyAccessId, isNull);
    },
  );
}
