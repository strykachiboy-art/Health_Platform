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

Map<String, dynamic> _grantJson({required String status}) {
  final active = status == 'active';
  final reviewed = status != 'requested';

  return <String, dynamic>{
    'id': 17,
    'clinic_id': 3,
    'patient_id': 7,
    'requester_user_id': 42,
    'requester_role': 'DOCTOR',
    'reason': 'Emergency treatment',
    'purpose': 'Immediate clinical review',
    'scope': <String>['patient:read'],
    'status': status,
    'requested_at': '2026-11-03T12:00:00+00:00',
    'granted_at': active ? '2026-11-03T12:05:00+00:00' : null,
    'expires_at': active ? '2026-11-03T12:35:00+00:00' : null,
    'revoked_at': status == 'revoked' ? '2026-11-03T12:20:00+00:00' : null,
    'reviewed_at': reviewed ? '2026-11-03T12:05:00+00:00' : null,
    'reviewed_by_user_id': reviewed ? 99 : null,
    'review_notes': reviewed ? 'Reviewed' : null,
  };
}

http.Response _response(String status, {int code = 200}) {
  return http.Response(
    jsonEncode(<String, dynamic>{
      'success': true,
      'data': _grantJson(status: status),
    }),
    code,
  );
}

EmergencyAccessRemoteDataSource _source(MockClient client) {
  return EmergencyAccessRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadAccessToken: () async => 'test-token',
  );
}

Future<void> _pump(
  WidgetTester tester,
  EmergencyAccessRemoteDataSource source,
) async {
  await tester.pumpWidget(
    MaterialApp(
      home: EmergencyAccessReviewScreen(
        dataSource: source,
        emergencyAccessId: 17,
      ),
    ),
  );

  await tester.pumpAndSettle();
}

Future<void> _tapVisible(WidgetTester tester, Finder finder) async {
  await tester.scrollUntilVisible(
    finder,
    200,
    scrollable: find.byType(Scrollable).first,
  );
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('requested status exposes grant and deny but not revoke', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/emergency-access/requests/17');
      return _response('requested');
    });
    addTearDown(client.close);

    await _pump(tester, _source(client));

    expect(find.text('Status: Requested'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('emergency-access-review-grant-button')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('emergency-access-review-deny-button')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('emergency-access-review-revoke-button')),
      findsNothing,
    );
  });

  testWidgets('grant sends exact review payload and transitions to active', (
    tester,
  ) async {
    var requests = 0;

    final client = MockClient((request) async {
      requests++;

      if (request.method == 'GET') {
        return _response('requested');
      }

      expect(request.method, 'POST');
      expect(request.url.path, '/api/v1/emergency-access/requests/17/grant');
      expect(jsonDecode(request.body), <String, dynamic>{
        'duration_minutes': 30,
        'review_notes': 'Approved for emergency treatment',
      });
      expect(request.headers.containsKey('idempotency-key'), isFalse);

      return _response('active');
    });
    addTearDown(client.close);

    await _pump(tester, _source(client));

    await tester.enterText(
      find.byKey(const ValueKey('emergency-access-review-notes')),
      'Approved for emergency treatment',
    );

    await _tapVisible(
      tester,
      find.byKey(const ValueKey('emergency-access-review-grant-button')),
    );

    expect(requests, 2);
    expect(find.text('Status: Active'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('emergency-access-review-revoke-button')),
      findsOneWidget,
    );
  });

  testWidgets('deny requires notes and transitions requested to denied', (
    tester,
  ) async {
    var posts = 0;

    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return _response('requested');
      }

      posts++;

      expect(request.url.path, '/api/v1/emergency-access/requests/17/deny');
      expect(jsonDecode(request.body), <String, dynamic>{
        'duration_minutes': 30,
        'review_notes': 'Insufficient justification',
      });

      return _response('denied');
    });
    addTearDown(client.close);

    await _pump(tester, _source(client));

    await _tapVisible(
      tester,
      find.byKey(const ValueKey('emergency-access-review-deny-button')),
    );

    expect(posts, 0);
    expect(
      find.text('Review notes are required when denying access.'),
      findsOneWidget,
    );

    await tester.enterText(
      find.byKey(const ValueKey('emergency-access-review-notes')),
      'Insufficient justification',
    );

    await _tapVisible(
      tester,
      find.byKey(const ValueKey('emergency-access-review-deny-button')),
    );

    expect(posts, 1);
    expect(find.text('Status: Denied'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('emergency-access-review-terminal-state')),
      findsOneWidget,
    );
  });

  testWidgets('active request can be revoked and then becomes terminal', (
    tester,
  ) async {
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return _response('active');
      }

      expect(request.url.path, '/api/v1/emergency-access/requests/17/revoke');
      expect(jsonDecode(request.body), <String, dynamic>{
        'reason': 'Emergency treatment completed',
      });

      return _response('revoked');
    });
    addTearDown(client.close);

    await _pump(tester, _source(client));

    await tester.enterText(
      find.byKey(const ValueKey('emergency-access-review-revoke-reason')),
      'Emergency treatment completed',
    );

    await _tapVisible(
      tester,
      find.byKey(const ValueKey('emergency-access-review-revoke-button')),
    );

    expect(find.text('Status: Revoked'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('emergency-access-review-terminal-state')),
      findsOneWidget,
    );
  });

  testWidgets(
    'unknown grant outcome blocks writes until GET reconciliation succeeds',
    (tester) async {
      var getCount = 0;
      var postCount = 0;

      final client = MockClient((request) async {
        if (request.method == 'GET') {
          getCount++;
          return _response(getCount == 1 ? 'requested' : 'active');
        }

        postCount++;
        throw http.ClientException('simulated unknown review outcome');
      });
      addTearDown(client.close);

      await _pump(tester, _source(client));

      await _tapVisible(
        tester,
        find.byKey(const ValueKey('emergency-access-review-grant-button')),
      );

      expect(postCount, 1);
      expect(
        find.byKey(
          const ValueKey('emergency-access-review-reconciliation-warning'),
        ),
        findsOneWidget,
      );

      final grantButton = tester.widget<ElevatedButton>(
        find.byKey(const ValueKey('emergency-access-review-grant-button')),
      );

      expect(grantButton.onPressed, isNull);

      await _tapVisible(
        tester,
        find.byKey(const ValueKey('emergency-access-review-refresh-button')),
      );

      expect(getCount, 2);
      expect(find.text('Status: Active'), findsOneWidget);
      expect(
        find.byKey(
          const ValueKey('emergency-access-review-reconciliation-warning'),
        ),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('emergency-access-review-revoke-button')),
        findsOneWidget,
      );
    },
  );

  testWidgets('expired status exposes no review mutation control', (
    tester,
  ) async {
    final client = MockClient((request) async {
      return _response('expired');
    });
    addTearDown(client.close);

    await _pump(tester, _source(client));

    expect(find.text('Status: Expired'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('emergency-access-review-terminal-state')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('emergency-access-review-grant-button')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('emergency-access-review-deny-button')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('emergency-access-review-revoke-button')),
      findsNothing,
    );
  });

  testWidgets(
    'active request can activate in-memory emergency clinical context',
    (tester) async {
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        return _response('active');
      });
      addTearDown(client.close);

      final accessContext = EmergencyAccessContext(
        nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
      );
      addTearDown(accessContext.dispose);

      await tester.pumpWidget(
        MaterialApp(
          home: EmergencyAccessReviewScreen(
            dataSource: _source(client),
            emergencyAccessId: 17,
            emergencyAccessContext: accessContext,
          ),
        ),
      );

      await tester.pumpAndSettle();

      await _tapVisible(
        tester,
        find.byKey(const ValueKey('emergency-access-review-use-button')),
      );

      expect(accessContext.activeEmergencyAccessId, 17);
      expect(accessContext.emergencyAccessIdForPatient(7), 17);
    },
  );
}
