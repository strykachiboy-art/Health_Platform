import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_profile_edit_screen.dart';

Map<String, dynamic> _patientJson({String firstName = 'Ada'}) => {
  'id': 7,
  'clinic_id': 3,
  'patient_number': 'PT-0007',
  'first_name': firstName,
  'last_name': 'Lovelace',
  'date_of_birth': '1815-12-10',
  'gender': 'female',
  'blood_type': 'O+',
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

Patient _patient({String firstName = 'Ada'}) =>
    Patient.fromJson(_patientJson(firstName: firstName));

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

Future<void> _openEditor(
  WidgetTester tester,
  PatientRemoteDataSource source,
) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: Builder(
          builder: (context) => TextButton(
            onPressed: () async {
              await Navigator.of(context).push<Patient>(
                MaterialPageRoute(
                  builder: (_) => PatientProfileEditScreen(
                    dataSource: source,
                    bearerToken: 'test-token',
                    patient: _patient(),
                  ),
                ),
              );
            },
            child: const Text('Open editor'),
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('Open editor'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
    'editor sends only changed profile fields and returns server patient',
    (tester) async {
      Map<String, dynamic>? submitted;
      final client = MockClient((request) async {
        expect(request.method, 'PATCH');
        expect(request.url.path, '/api/v1/patients/7');
        expect(request.headers['authorization'], 'Bearer test-token');
        submitted = jsonDecode(request.body) as Map<String, dynamic>;
        return http.Response(
          jsonEncode({
            'success': true,
            'data': _patientJson(firstName: 'Grace'),
          }),
          200,
        );
      });
      addTearDown(client.close);
      final source = _dataSource(client);
      Patient? returned;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Builder(
              builder: (context) => TextButton(
                onPressed: () async {
                  returned = await Navigator.of(context).push<Patient>(
                    MaterialPageRoute(
                      builder: (_) => PatientProfileEditScreen(
                        dataSource: source,
                        bearerToken: 'test-token',
                        patient: _patient(),
                      ),
                    ),
                  );
                },
                child: const Text('Open editor'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Open editor'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const ValueKey('patient-profile-edit-first-name')),
        ' Grace ',
      );
      await tester.ensureVisible(
        find.byKey(const ValueKey('patient-profile-edit-save')),
      );
      await tester.tap(find.byKey(const ValueKey('patient-profile-edit-save')));
      await tester.pumpAndSettle();

      expect(submitted, {'first_name': 'Grace'});
      expect(returned?.fullName, 'Grace Lovelace');
    },
  );

  testWidgets('editor validates required names before making a request', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      return http.Response(
        jsonEncode({'success': true, 'data': _patientJson()}),
        200,
      );
    });
    addTearDown(client.close);
    await _openEditor(tester, _dataSource(client));

    await tester.enterText(
      find.byKey(const ValueKey('patient-profile-edit-first-name')),
      '   ',
    );
    await tester.ensureVisible(
      find.byKey(const ValueKey('patient-profile-edit-save')),
    );
    await tester.tap(find.byKey(const ValueKey('patient-profile-edit-save')));
    await tester.pumpAndSettle();

    expect(find.text('First name is required.'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets(
    'editor displays server validation errors on corresponding fields',
    (tester) async {
      final client = MockClient(
        (request) async => http.Response(
          jsonEncode({
            'message': 'Validation failed',
            'details': [
              {
                'loc': ['body', 'email'],
                'msg': 'Invalid email address',
              },
            ],
          }),
          422,
        ),
      );
      addTearDown(client.close);
      await _openEditor(tester, _dataSource(client));

      await tester.enterText(
        find.byKey(const ValueKey('patient-profile-edit-email')),
        'grace@example.test',
      );
      await tester.ensureVisible(
        find.byKey(const ValueKey('patient-profile-edit-save')),
      );
      await tester.tap(find.byKey(const ValueKey('patient-profile-edit-save')));
      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('patient-profile-edit-server-error-email')),
        findsOneWidget,
      );
      expect(find.text('Invalid email address'), findsOneWidget);
    },
  );

  testWidgets(
    'uncertain PATCH outcome requires checking server state instead of resubmitting',
    (tester) async {
      var patchRequests = 0;
      var getRequests = 0;
      final client = MockClient((request) async {
        if (request.method == 'PATCH') {
          patchRequests++;
          return http.Response('{"success":false}', 503);
        }
        getRequests++;
        return http.Response(
          jsonEncode({
            'success': true,
            'data': _patientJson(firstName: 'Grace'),
          }),
          200,
        );
      });
      addTearDown(client.close);
      Patient? returned;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Builder(
              builder: (context) => TextButton(
                onPressed: () async {
                  returned = await Navigator.of(context).push<Patient>(
                    MaterialPageRoute(
                      builder: (_) => PatientProfileEditScreen(
                        dataSource: _dataSource(client),
                        bearerToken: 'test-token',
                        patient: _patient(),
                      ),
                    ),
                  );
                },
                child: const Text('Open editor'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Open editor'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const ValueKey('patient-profile-edit-first-name')),
        'Grace',
      );
      await tester.ensureVisible(
        find.byKey(const ValueKey('patient-profile-edit-save')),
      );
      await tester.tap(find.byKey(const ValueKey('patient-profile-edit-save')));
      await tester.pumpAndSettle();

      expect(
        find.textContaining('could not confirm whether the update was saved'),
        findsOneWidget,
      );
      expect(
        find
            .byKey(const ValueKey('patient-profile-edit-save'))
            .evaluate()
            .single
            .widget,
        isA<FilledButton>(),
      );
      expect(
        (find
                    .byKey(const ValueKey('patient-profile-edit-save'))
                    .evaluate()
                    .single
                    .widget
                as FilledButton)
            .onPressed,
        isNull,
      );
      expect(patchRequests, 1);

      await tester.ensureVisible(
        find.byKey(const ValueKey('patient-profile-edit-check-server-state')),
      );
      await tester.tap(
        find.byKey(const ValueKey('patient-profile-edit-check-server-state')),
      );
      await tester.pumpAndSettle();
      expect(getRequests, 1);
      expect(returned?.fullName, 'Grace Lovelace');
    },
  );
}
