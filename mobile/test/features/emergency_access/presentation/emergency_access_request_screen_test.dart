import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_remote_data_source.dart';
import 'package:clinic_system_pro/features/emergency_access/presentation/emergency_access_request_screen.dart';

Map<String, dynamic> _grantJson({String status = 'requested'}) {
  return <String, dynamic>{
    'id': 17,
    'clinic_id': 3,
    'patient_id': 7,
    'requester_user_id': 42,
    'requester_role': 'DOCTOR',
    'reason': 'Emergency treatment',
    'purpose': 'Immediate clinical review',
    'scope': <String>['patient:read', 'nursing_note:7:*'],
    'status': status,
    'requested_at': '2026-11-03T12:00:00+00:00',
    'granted_at': null,
    'expires_at': null,
    'revoked_at': null,
    'reviewed_at': null,
    'reviewed_by_user_id': null,
    'review_notes': null,
  };
}

EmergencyAccessRemoteDataSource _source(
  MockClient client, {
  AuthenticatedAccessTokenLoader? loadAccessToken,
}) {
  return EmergencyAccessRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadAccessToken: loadAccessToken ?? () async => 'test-token',
  );
}

Future<void> _pump(
  WidgetTester tester,
  EmergencyAccessRemoteDataSource source,
) async {
  await tester.pumpWidget(
    MaterialApp(home: EmergencyAccessRequestScreen(dataSource: source)),
  );
  await tester.pumpAndSettle();
}

Future<void> _enterValidRequest(WidgetTester tester) async {
  await tester.enterText(
    find.byKey(const ValueKey('emergency-access-request-patient-id')),
    '7',
  );

  await tester.enterText(
    find.byKey(const ValueKey('emergency-access-request-reason')),
    'Emergency treatment',
  );

  await tester.enterText(
    find.byKey(const ValueKey('emergency-access-request-purpose')),
    'Immediate clinical review',
  );

  await tester.enterText(
    find.byKey(const ValueKey('emergency-access-request-scope')),
    'patient:read\nnursing_note:7:*',
  );
}

Future<void> _submit(WidgetTester tester) async {
  FocusManager.instance.primaryFocus?.unfocus();
  await tester.pumpAndSettle();

  final button = find.byKey(
    const ValueKey('emergency-access-request-submit-button'),
  );

  await tester.scrollUntilVisible(
    button,
    200,
    scrollable: find.byType(Scrollable).first,
  );

  await tester.tap(button);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('required fields are validated locally without HTTP', (
    tester,
  ) async {
    var requests = 0;

    final client = MockClient((request) async {
      requests++;
      return http.Response('{}', 201);
    });
    addTearDown(client.close);

    await _pump(tester, _source(client));
    await _submit(tester);

    expect(find.text('Patient ID must be a positive integer.'), findsOneWidget);
    expect(find.text('Emergency reason is required.'), findsOneWidget);
    expect(find.text('Purpose is required.'), findsOneWidget);
    expect(find.text('At least one scope is required.'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets('successful request sends exact contract and shows request ID', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.method, 'POST');
      expect(request.url.path, '/api/v1/emergency-access/requests');
      expect(request.headers['authorization'], 'Bearer test-token');
      expect(request.headers.containsKey('idempotency-key'), isFalse);

      expect(jsonDecode(request.body), <String, dynamic>{
        'patient_id': 7,
        'reason': 'Emergency treatment',
        'purpose': 'Immediate clinical review',
        'scope': <String>['patient:read', 'nursing_note:7:*'],
      });

      return http.Response(
        jsonEncode(<String, dynamic>{'success': true, 'data': _grantJson()}),
        201,
      );
    });
    addTearDown(client.close);

    await _pump(tester, _source(client));
    await _enterValidRequest(tester);
    await _submit(tester);

    expect(
      find.byKey(const ValueKey('emergency-access-request-success')),
      findsOneWidget,
    );
    expect(find.text('Emergency access requested'), findsOneWidget);
    expect(find.text('Request ID: 17'), findsOneWidget);
    expect(find.text('Status: requested'), findsOneWidget);
  });

  testWidgets('422 response shows backend field errors and remains retryable', (
    tester,
  ) async {
    final client = MockClient((request) async {
      return http.Response(
        jsonEncode(<String, dynamic>{
          'success': false,
          'error': 'Validation failed',
          'details': <Map<String, dynamic>>[
            <String, dynamic>{
              'loc': <String>['body', 'scope'],
              'msg': 'Scope is not permitted.',
              'type': 'value_error',
            },
          ],
        }),
        422,
      );
    });
    addTearDown(client.close);

    await _pump(tester, _source(client));
    await _enterValidRequest(tester);
    await _submit(tester);

    expect(
      find.byKey(const ValueKey('emergency-access-request-server-error-scope')),
      findsOneWidget,
    );
    expect(find.text('Scope is not permitted.'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('emergency-access-request-submit-button')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('emergency-access-request-reconcile-warning')),
      findsNothing,
    );
  });

  testWidgets(
    'session unavailable is known pre-HTTP failure and stays retryable',
    (tester) async {
      var requests = 0;

      final client = MockClient((request) async {
        requests++;
        return http.Response('{}', 201);
      });
      addTearDown(client.close);

      final source = _source(
        client,
        loadAccessToken: () async {
          throw const AuthenticatedSessionUnavailableException(
            AuthenticatedSessionAccessFailure.signedOut,
          );
        },
      );

      await _pump(tester, source);
      await _enterValidRequest(tester);
      await _submit(tester);

      expect(requests, 0);
      expect(
        find.text('Sign in before requesting emergency access.'),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('emergency-access-request-submit-button')),
        findsOneWidget,
      );
    },
  );

  testWidgets('connection failure blocks blind repeat submission', (
    tester,
  ) async {
    var requests = 0;

    final client = MockClient((request) async {
      requests++;
      throw http.ClientException('simulated unknown emergency request outcome');
    });
    addTearDown(client.close);

    await _pump(tester, _source(client));
    await _enterValidRequest(tester);
    await _submit(tester);

    expect(requests, 1);
    expect(
      find.textContaining(
        'could not confirm whether the emergency access request was created',
      ),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('emergency-access-request-reconcile-warning')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('emergency-access-request-submit-button')),
      findsNothing,
    );
  });

  testWidgets(
    'HTTP 503 is treated as unknown outcome for non-idempotent creation',
    (tester) async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode(<String, dynamic>{
            'success': false,
            'error': 'temporary unavailable',
          }),
          503,
        );
      });
      addTearDown(client.close);

      await _pump(tester, _source(client));
      await _enterValidRequest(tester);
      await _submit(tester);

      expect(
        find.byKey(
          const ValueKey('emergency-access-request-reconcile-warning'),
        ),
        findsOneWidget,
      );

      expect(
        find.byKey(const ValueKey('emergency-access-request-submit-button')),
        findsNothing,
      );
    },
  );
}
