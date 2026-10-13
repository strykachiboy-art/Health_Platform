import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_detail_screen.dart';

Map<String, dynamic> _patientJson() => {
  'id': 7,
  'clinic_id': 3,
  'patient_number': 'PT-0007',
  'first_name': 'Ada',
  'last_name': 'Lovelace',
  'date_of_birth': '1815-12-10',
  'gender': 'FEMALE',
  'blood_type': 'O_POSITIVE',
  'phone': '+2348000000000',
  'email': 'ada@example.test',
  'address': 'Private test address',
  'allergies': 'Peanuts',
  'chronic_conditions': 'Asthma',
  'emirates_id': null,
  'umrn': 'UMRN-7',
  'is_active': true,
  'created_at': '2026-01-01T10:00:00+00:00',
  'updated_at': '2026-01-02T10:00:00+00:00',
};

PatientRemoteDataSource _dataSource(
  MockClient client, {
  Duration requestTimeout = const Duration(seconds: 10),
}) => PatientRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
    requestTimeout: requestTimeout,
  ),
);

void main() {
  testWidgets(
    'detail screen opens editor and reflects the server-updated patient',
    (tester) async {
      var firstName = 'Ada';
      Map<String, dynamic>? submitted;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return http.Response(
            jsonEncode({
              'success': true,
              'data': _patientJson()..['first_name'] = firstName,
            }),
            200,
          );
        }
        expect(request.method, 'PATCH');
        expect(request.url.path, '/api/v1/patients/7');
        submitted = jsonDecode(request.body) as Map<String, dynamic>;
        firstName = submitted!['first_name'] as String;
        return http.Response(
          jsonEncode({
            'success': true,
            'data': _patientJson()..['first_name'] = firstName,
          }),
          200,
        );
      });
      addTearDown(client.close);

      await tester.pumpWidget(
        MaterialApp(
          home: PatientDetailScreen(
            dataSource: _dataSource(client),
            bearerToken: 'test-token',
            patientId: 7,
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(
        find.byKey(const ValueKey('patient-detail-edit-button')),
      );
      await tester.pumpAndSettle();
      expect(find.text('Edit patient'), findsOneWidget);

      await tester.enterText(
        find.byKey(const ValueKey('patient-profile-edit-first-name')),
        'Grace',
      );
      await tester.ensureVisible(
        find.byKey(const ValueKey('patient-profile-edit-save')),
      );
      await tester.tap(find.byKey(const ValueKey('patient-profile-edit-save')));
      await tester.pumpAndSettle();

      expect(submitted, {'first_name': 'Grace'});
      expect(find.text('Patient details'), findsOneWidget);
      expect(find.text('Grace Lovelace'), findsOneWidget);
      expect(find.text('Patient no. PT-0007'), findsOneWidget);
    },
  );

  testWidgets('detail screen renders authorized patient details', (
    tester,
  ) async {
    final client = MockClient((request) async {
      expect(request.url.path, '/api/v1/patients/7');
      expect(request.headers['authorization'], 'Bearer test-token');
      return http.Response(
        jsonEncode({'success': true, 'data': _patientJson()}),
        200,
      );
    });
    addTearDown(client.close);

    await tester.pumpWidget(
      MaterialApp(
        home: PatientDetailScreen(
          dataSource: _dataSource(client),
          bearerToken: 'test-token',
          patientId: 7,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('patient-detail-name')), findsOneWidget);
    expect(find.text('Ada Lovelace'), findsOneWidget);
    expect(find.text('+2348000000000'), findsOneWidget);

    await tester.scrollUntilVisible(find.text('Peanuts'), 250);
    expect(find.text('Peanuts'), findsOneWidget);
    expect(find.text('Asthma'), findsOneWidget);

    await tester.scrollUntilVisible(find.text('UMRN-7'), 250);
    expect(find.text('UMRN-7'), findsOneWidget);
  });

  testWidgets('detail screen exposes unauthorized state without patient data', (
    tester,
  ) async {
    final client = MockClient((request) async {
      return http.Response(jsonEncode({'success': false}), 401);
    });
    addTearDown(client.close);

    await tester.pumpWidget(
      MaterialApp(
        home: PatientDetailScreen(
          dataSource: _dataSource(client),
          bearerToken: 'expired-token',
          patientId: 7,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('HTTP 401'), findsOneWidget);
    expect(find.text('Return to sign in'), findsOneWidget);
    expect(find.byKey(const ValueKey('patient-detail-name')), findsNothing);
  });

  testWidgets(
    'failed refresh marks retained patient data stale until revalidated',
    (tester) async {
      var patientGets = 0;
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        patientGets++;

        if (patientGets == 2) {
          return http.Response('{"success":false}', 503);
        }

        return http.Response(
          jsonEncode({'success': true, 'data': _patientJson()}),
          200,
        );
      });
      addTearDown(client.close);

      await tester.pumpWidget(
        MaterialApp(
          home: PatientDetailScreen(
            dataSource: _dataSource(client),
            bearerToken: 'test-token',
            patientId: 7,
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Ada Lovelace'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('patient-detail-refresh-button')),
        findsOneWidget,
      );

      await tester.tap(
        find.byKey(const ValueKey('patient-detail-refresh-button')),
      );
      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('patient-detail-stale-data-notice')),
        findsOneWidget,
      );
      expect(
        find.text('Last confirmed patient details may be out of date.'),
        findsOneWidget,
      );
      expect(find.text('Ada Lovelace'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('patient-detail-edit-button')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('patient-status-action-button')),
        findsNothing,
      );
      expect(
        find.byKey(
          const ValueKey('patient-status-stale-disabled-message'),
        ),
        findsOneWidget,
      );

      await tester.tap(
        find.byKey(const ValueKey('patient-detail-refresh-button')),
      );
      await tester.pumpAndSettle();

      expect(patientGets, 3);
      expect(
        find.byKey(const ValueKey('patient-detail-stale-data-notice')),
        findsNothing,
      );
      expect(find.text('Ada Lovelace'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('patient-detail-edit-button')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('patient-status-action-button')),
        findsOneWidget,
      );
    },
  );

  for (final statusCode in <int>[401, 403, 404]) {
    testWidgets(
      'clears previously loaded patient data after HTTP $statusCode refresh',
      (tester) async {
        var patientGets = 0;
        final client = MockClient((request) async {
          expect(request.method, 'GET');
          patientGets++;

          if (patientGets == 1) {
            return http.Response(
              jsonEncode({'success': true, 'data': _patientJson()}),
              200,
            );
          }

          return http.Response(
            jsonEncode({'success': false, 'error': 'Unavailable'}),
            statusCode,
          );
        });
        addTearDown(client.close);

        await tester.pumpWidget(
          MaterialApp(
            home: PatientDetailScreen(
              dataSource: _dataSource(client),
              bearerToken: 'test-token',
              patientId: 7,
            ),
          ),
        );
        await tester.pumpAndSettle();

        expect(find.text('Ada Lovelace'), findsOneWidget);

        await tester.tap(
          find.byKey(const ValueKey('patient-detail-refresh-button')),
        );
        await tester.pumpAndSettle();

        expect(patientGets, 2);
        expect(find.text('Ada Lovelace'), findsNothing);
        expect(
          find.byKey(const ValueKey('patient-detail-name')),
          findsNothing,
        );
        expect(
          find.byKey(const ValueKey('patient-detail-stale-data-notice')),
          findsNothing,
        );
        expect(
          find.byKey(const ValueKey('patient-detail-refresh-button')),
          findsNothing,
        );
      },
    );
  }

  testWidgets(
    'status change requires explicit confirmation and cancel sends no PATCH',
    (tester) async {
      final requests = <String>[];
      final client = MockClient((request) async {
        requests.add('${request.method} ${request.url.path}');
        if (request.method == 'GET' &&
            request.url.path == '/api/v1/patients/7') {
          return http.Response(
            jsonEncode({'success': true, 'data': _patientJson()}),
            200,
          );
        }
        return http.Response('{}', 500);
      });
      addTearDown(client.close);

      await tester.pumpWidget(
        MaterialApp(
          home: PatientDetailScreen(
            dataSource: _dataSource(client),
            bearerToken: 'test-token',
            patientId: 7,
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(
        find.byKey(const ValueKey('patient-status-action-button')),
      );
      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('patient-status-confirmation-dialog')),
        findsOneWidget,
      );
      expect(find.text('Deactivate this patient?'), findsOneWidget);
      expect(find.textContaining('active-only patient lists'), findsOneWidget);

      await tester.tap(
        find.byKey(const ValueKey('patient-status-cancel-button')),
      );
      await tester.pumpAndSettle();

      expect(requests, ['GET /api/v1/patients/7']);
      expect(
        find.byKey(const ValueKey('patient-status-action-button')),
        findsOneWidget,
      );
      expect(find.text('Active'), findsOneWidget);
    },
  );

  testWidgets('confirmed deactivation applies server-returned patient state', (
    tester,
  ) async {
    final requests = <String>[];
    final client = MockClient((request) async {
      requests.add('${request.method} ${request.url.path}');
      if (request.method == 'GET' && request.url.path == '/api/v1/patients/7') {
        return http.Response(
          jsonEncode({'success': true, 'data': _patientJson()}),
          200,
        );
      }
      if (request.method == 'PATCH' &&
          request.url.path == '/api/v1/patients/7/status') {
        expect(request.headers['authorization'], 'Bearer test-token');
        expect(jsonDecode(request.body), {'is_active': false});
        final updated = _patientJson()..['is_active'] = false;
        return http.Response(
          jsonEncode({'success': true, 'data': updated}),
          200,
        );
      }
      return http.Response('{}', 500);
    });
    addTearDown(client.close);

    await tester.pumpWidget(
      MaterialApp(
        home: PatientDetailScreen(
          dataSource: _dataSource(client),
          bearerToken: 'test-token',
          patientId: 7,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(
      find.byKey(const ValueKey('patient-status-action-button')),
    );
    await tester.pumpAndSettle();
    await tester.tap(
      find.byKey(const ValueKey('patient-status-confirm-button')),
    );
    await tester.pumpAndSettle();

    expect(requests, [
      'GET /api/v1/patients/7',
      'PATCH /api/v1/patients/7/status',
    ]);
    expect(find.text('Inactive'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('patient-status-action-button')),
      findsOneWidget,
    );
    expect(find.text('Activate patient'), findsOneWidget);
    expect(find.text('Patient deactivated successfully.'), findsOneWidget);
  });

  testWidgets('confirmed activation applies server-returned patient state', (
    tester,
  ) async {
    final requests = <String>[];
    final client = MockClient((request) async {
      requests.add('${request.method} ${request.url.path}');
      if (request.method == 'GET' && request.url.path == '/api/v1/patients/7') {
        final inactive = _patientJson()..['is_active'] = false;
        return http.Response(
          jsonEncode({'success': true, 'data': inactive}),
          200,
        );
      }
      if (request.method == 'PATCH' &&
          request.url.path == '/api/v1/patients/7/status') {
        expect(request.headers['authorization'], 'Bearer test-token');
        expect(jsonDecode(request.body), {'is_active': true});
        final updated = _patientJson()..['is_active'] = true;
        return http.Response(
          jsonEncode({'success': true, 'data': updated}),
          200,
        );
      }
      return http.Response('{}', 500);
    });
    addTearDown(client.close);

    await tester.pumpWidget(
      MaterialApp(
        home: PatientDetailScreen(
          dataSource: _dataSource(client),
          bearerToken: 'test-token',
          patientId: 7,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Activate patient'), findsOneWidget);
    await tester.tap(
      find.byKey(const ValueKey('patient-status-action-button')),
    );
    await tester.pumpAndSettle();
    expect(find.text('Activate this patient?'), findsOneWidget);
    await tester.tap(
      find.byKey(const ValueKey('patient-status-confirm-button')),
    );
    await tester.pumpAndSettle();

    expect(requests, [
      'GET /api/v1/patients/7',
      'PATCH /api/v1/patients/7/status',
    ]);
    expect(find.text('Active'), findsOneWidget);
    expect(find.text('Deactivate patient'), findsOneWidget);
    expect(find.text('Patient activated successfully.'), findsOneWidget);
  });

  testWidgets(
    '403 status response shows permission message and does not flip state',
    (tester) async {
      final requests = <String>[];
      final client = MockClient((request) async {
        requests.add('${request.method} ${request.url.path}');
        if (request.method == 'GET' &&
            request.url.path == '/api/v1/patients/7') {
          return http.Response(
            jsonEncode({'success': true, 'data': _patientJson()}),
            200,
          );
        }
        if (request.method == 'PATCH') {
          return http.Response(
            jsonEncode({'success': false, 'error': 'Forbidden'}),
            403,
          );
        }
        return http.Response('{}', 500);
      });
      addTearDown(client.close);

      await tester.pumpWidget(
        MaterialApp(
          home: PatientDetailScreen(
            dataSource: _dataSource(client),
            bearerToken: 'test-token',
            patientId: 7,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(
        find.byKey(const ValueKey('patient-status-action-button')),
      );
      await tester.pumpAndSettle();
      await tester.tap(
        find.byKey(const ValueKey('patient-status-confirm-button')),
      );
      await tester.pumpAndSettle();

      expect(
        find.text('Your account is not permitted to change patient status.'),
        findsOneWidget,
      );
      expect(find.text('Active'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('patient-status-action-button')),
        findsNothing,
      );
      expect(requests, [
        'GET /api/v1/patients/7',
        'PATCH /api/v1/patients/7/status',
      ]);
    },
  );

  testWidgets(
    'uncertain 5xx status outcome requires refresh before another change',
    (tester) async {
      var patientGets = 0;
      final requests = <String>[];
      final client = MockClient((request) async {
        requests.add('${request.method} ${request.url.path}');
        if (request.method == 'GET' &&
            request.url.path == '/api/v1/patients/7') {
          patientGets++;
          final current = _patientJson()..['is_active'] = patientGets == 1;
          return http.Response(
            jsonEncode({'success': true, 'data': current}),
            200,
          );
        }
        if (request.method == 'PATCH') {
          expect(jsonDecode(request.body), {'is_active': false});
          // The test deliberately cannot know if the server applied the change.
          return http.Response(jsonEncode({'success': false}), 503);
        }
        return http.Response('{}', 500);
      });
      addTearDown(client.close);

      await tester.pumpWidget(
        MaterialApp(
          home: PatientDetailScreen(
            dataSource: _dataSource(client),
            bearerToken: 'test-token',
            patientId: 7,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(
        find.byKey(const ValueKey('patient-status-action-button')),
      );
      await tester.pumpAndSettle();
      await tester.tap(
        find.byKey(const ValueKey('patient-status-confirm-button')),
      );
      await tester.pumpAndSettle();

      expect(
        find.textContaining(
          'We could not confirm whether the server applied this change.',
        ),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('patient-status-reconcile-button')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('patient-status-action-button')),
        findsNothing,
      );
      expect(requests, [
        'GET /api/v1/patients/7',
        'PATCH /api/v1/patients/7/status',
      ]);

      await tester.tap(
        find.byKey(const ValueKey('patient-status-reconcile-button')),
      );
      await tester.pumpAndSettle();

      expect(find.text('Inactive'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('patient-status-action-button')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('patient-status-reconcile-button')),
        findsNothing,
      );
      expect(requests, [
        'GET /api/v1/patients/7',
        'PATCH /api/v1/patients/7/status',
        'GET /api/v1/patients/7',
      ]);
    },
  );
}
