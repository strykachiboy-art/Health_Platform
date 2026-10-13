import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_directory_screen.dart';

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
  'allergies': 'Private test allergy',
  'chronic_conditions': 'Private test condition',
  'emirates_id': null,
  'umrn': 'UMRN-7',
  'is_active': true,
  'created_at': '2026-01-01T10:00:00+00:00',
  'updated_at': '2026-01-02T10:00:00+00:00',
};

Map<String, dynamic> _pageEnvelope({List<Map<String, dynamic>>? items}) => {
  'success': true,
  'data': {
    'items': items ?? [_patientJson()],
    'total': items?.length ?? 1,
    'page': 1,
    'per_page': 25,
  },
};

PatientRemoteDataSource _dataSource(MockClient client) =>
    PatientRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
    );

void main() {
  testWidgets(
    'directory shows patient name and number without exposing clinical data',
    (tester) async {
      final client = MockClient((request) async {
        expect(request.headers['authorization'], 'Bearer test-token');
        return http.Response(jsonEncode(_pageEnvelope()), 200);
      });
      addTearDown(client.close);

      await tester.pumpWidget(
        MaterialApp(
          home: PatientDirectoryScreen(
            dataSource: _dataSource(client),
            bearerToken: 'test-token',
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Ada Lovelace'), findsOneWidget);
      expect(find.text('Patient no. PT-0007'), findsOneWidget);
      expect(find.text('+2348000000000'), findsNothing);
      expect(find.text('Private test address'), findsNothing);
      expect(find.text('Private test allergy'), findsNothing);
      expect(find.text('Private test condition'), findsNothing);
    },
  );

  testWidgets(
    'refresh retains previously loaded results as stale after a transient failure',
    (tester) async {
      var requestCount = 0;
      final client = MockClient((request) async {
        requestCount++;
        if (requestCount == 1) {
          return http.Response(jsonEncode(_pageEnvelope()), 200);
        }
        return http.Response(
          jsonEncode({'success': false, 'message': 'temporary unavailable'}),
          503,
        );
      });
      addTearDown(client.close);

      await tester.pumpWidget(
        MaterialApp(
          home: PatientDirectoryScreen(
            dataSource: _dataSource(client),
            bearerToken: 'test-token',
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(requestCount, 1);
      expect(find.text('Ada Lovelace'), findsOneWidget);

      await tester.drag(
        find.byKey(const ValueKey('patient-directory-list')),
        const Offset(0, 400),
      );
      await tester.pumpAndSettle();

      expect(requestCount, 2);
      expect(find.text('Ada Lovelace'), findsOneWidget);
      expect(
        find.text(
          'Showing previously loaded patients. These results may be out of date.',
        ),
        findsOneWidget,
      );
      expect(find.textContaining('HTTP 503'), findsOneWidget);
      expect(find.text('Retry'), findsOneWidget);
      expect(find.text('+2348000000000'), findsNothing);
    },
  );

  testWidgets('successful retry clears the stale directory warning', (
    tester,
  ) async {
    var requestCount = 0;
    final client = MockClient((request) async {
      requestCount++;
      if (requestCount == 2) {
        return http.Response(
          jsonEncode({'success': false, 'message': 'temporary unavailable'}),
          503,
        );
      }
      return http.Response(jsonEncode(_pageEnvelope()), 200);
    });
    addTearDown(client.close);

    await tester.pumpWidget(
      MaterialApp(
        home: PatientDirectoryScreen(
          dataSource: _dataSource(client),
          bearerToken: 'test-token',
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(requestCount, 1);
    expect(find.text('Ada Lovelace'), findsOneWidget);

    await tester.drag(
      find.byKey(const ValueKey('patient-directory-list')),
      const Offset(0, 400),
    );
    await tester.pumpAndSettle();

    expect(requestCount, 2);
    expect(
      find.text(
        'Showing previously loaded patients. These results may be out of date.',
      ),
      findsOneWidget,
    );
    expect(find.textContaining('HTTP 503'), findsOneWidget);

    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();

    expect(requestCount, 3);
    expect(find.text('Ada Lovelace'), findsOneWidget);
    expect(
      find.text(
        'Showing previously loaded patients. These results may be out of date.',
      ),
      findsNothing,
    );
    expect(find.textContaining('HTTP 503'), findsNothing);
  });
  for (final statusCode in <int>[401, 403]) {
    testWidgets('directory clears retained results after HTTP $statusCode', (
      tester,
    ) async {
      var requestCount = 0;
      final client = MockClient((request) async {
        requestCount++;
        if (requestCount == 1) {
          return http.Response(jsonEncode(_pageEnvelope()), 200);
        }
        return http.Response(
          jsonEncode({'success': false, 'message': 'access rejected'}),
          statusCode,
        );
      });
      addTearDown(client.close);

      await tester.pumpWidget(
        MaterialApp(
          home: PatientDirectoryScreen(
            dataSource: _dataSource(client),
            bearerToken: 'test-token',
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Ada Lovelace'), findsOneWidget);

      await tester.drag(
        find.byKey(const ValueKey('patient-directory-list')),
        const Offset(0, 400),
      );
      await tester.pumpAndSettle();

      expect(requestCount, 2);
      expect(find.text('Ada Lovelace'), findsNothing);
      expect(
        find.text(
          'Showing previously loaded patients. These results may be out of date.',
        ),
        findsNothing,
      );

      if (statusCode == 401) {
        expect(find.textContaining('HTTP 401'), findsOneWidget);
      } else {
        expect(
          find.text(
            'Your account is not permitted to view the patient directory.',
          ),
          findsOneWidget,
        );
      }
    });
  }

  testWidgets('directory shows an explicit empty state', (tester) async {
    final client = MockClient((request) async {
      return http.Response(jsonEncode(_pageEnvelope(items: [])), 200);
    });
    addTearDown(client.close);

    await tester.pumpWidget(
      MaterialApp(
        home: PatientDirectoryScreen(
          dataSource: _dataSource(client),
          bearerToken: 'test-token',
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('No active patients found'), findsOneWidget);
    expect(find.byKey(const ValueKey('patient-directory-list')), findsNothing);
  });

  testWidgets('search is debounced and sent to the existing patient endpoint', (
    tester,
  ) async {
    final receivedSearches = <String?>[];
    final client = MockClient((request) async {
      receivedSearches.add(request.url.queryParameters['search']);
      final noMatches = request.url.queryParameters['search'] == 'Nobody';
      return http.Response(
        jsonEncode(_pageEnvelope(items: noMatches ? [] : [_patientJson()])),
        200,
      );
    });
    addTearDown(client.close);

    await tester.pumpWidget(
      MaterialApp(
        home: PatientDirectoryScreen(
          dataSource: _dataSource(client),
          bearerToken: 'test-token',
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(receivedSearches, [null]);

    await tester.enterText(
      find.byKey(const ValueKey('patient-search-field')),
      'Nobody',
    );
    await tester.pump();
    expect(find.text('Ada Lovelace'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-directory-loading')),
      findsOneWidget,
    );

    await tester.pump(const Duration(milliseconds: 200));
    expect(receivedSearches, [null]);
    await tester.pump(const Duration(milliseconds: 200));
    await tester.pumpAndSettle();

    expect(receivedSearches, [null, 'Nobody']);
    expect(find.text('No matching patients'), findsOneWidget);
    expect(find.text('Ada Lovelace'), findsNothing);
  });
}
