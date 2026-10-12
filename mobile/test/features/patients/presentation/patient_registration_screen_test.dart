import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_registration_screen.dart';

Map<String, dynamic> _patientJson() => {
  'id': 7,
  'clinic_id': 3,
  'patient_number': 'PT-0007',
  'first_name': 'Ada',
  'last_name': 'Lovelace',
  'date_of_birth': null,
  'gender': null,
  'blood_type': null,
  'phone': null,
  'email': null,
  'address': null,
  'allergies': null,
  'chronic_conditions': null,
  'emirates_id': null,
  'umrn': null,
  'is_active': true,
  'created_at': '2026-01-01T10:00:00+00:00',
  'updated_at': '2026-01-01T10:00:00+00:00',
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

Future<void> _enterRequiredNames(WidgetTester tester) async {
  await tester.enterText(
    find.byKey(const ValueKey('patient-registration-first-name')),
    'Ada',
  );
  await tester.enterText(
    find.byKey(const ValueKey('patient-registration-last-name')),
    'Lovelace',
  );
}

Future<void> _scrollToAndTapSubmit(WidgetTester tester) async {
  FocusManager.instance.primaryFocus?.unfocus();
  await tester.pumpAndSettle();

  final button = find.byKey(
    const ValueKey('patient-registration-submit-button'),
  );
  final scrollable = find.byType(Scrollable).first;
  await tester.scrollUntilVisible(button, 200, scrollable: scrollable);
  await Scrollable.ensureVisible(tester.element(button), alignment: 0.5);
  await tester.pumpAndSettle();
  await tester.tap(button);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('required names are validated locally without making a request', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      return http.Response(
        jsonEncode({'success': true, 'data': _patientJson()}),
        201,
      );
    });
    addTearDown(client.close);

    await tester.pumpWidget(
      MaterialApp(
        home: PatientRegistrationScreen(
          dataSource: _dataSource(client),
          bearerToken: 'test-token',
        ),
      ),
    );
    await tester.pumpAndSettle();

    await _scrollToAndTapSubmit(tester);

    final scrollable = find.byType(Scrollable).first;
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('patient-registration-first-name')),
      -200,
      scrollable: scrollable,
    );
    await tester.pumpAndSettle();

    expect(find.text('First name is required.'), findsOneWidget);
    expect(find.text('Last name is required.'), findsOneWidget);
    expect(requests, 0);
  });

  testWidgets(
    'successful creation displays the backend-generated patient number',
    (tester) async {
      final client = MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/patients');
        expect(request.headers['authorization'], 'Bearer test-token');
        expect(jsonDecode(request.body), {
          'first_name': 'Ada',
          'last_name': 'Lovelace',
        });

        return http.Response(
          jsonEncode({'success': true, 'data': _patientJson()}),
          201,
        );
      });
      addTearDown(client.close);

      await tester.pumpWidget(
        MaterialApp(
          home: PatientRegistrationScreen(
            dataSource: _dataSource(client),
            bearerToken: 'test-token',
          ),
        ),
      );
      await tester.pumpAndSettle();
      await _enterRequiredNames(tester);

      await _scrollToAndTapSubmit(tester);

      expect(
        find.byKey(const ValueKey('patient-registration-success')),
        findsOneWidget,
      );
      expect(find.text('Patient registered'), findsOneWidget);
      expect(find.text('Patient no. PT-0007'), findsOneWidget);
      expect(
        find.byKey(
          const ValueKey('patient-registration-view-created-patient-button'),
        ),
        findsOneWidget,
      );
    },
  );

  testWidgets(
    '422 response shows a field error without echoing submitted values',
    (tester) async {
      const invalidEmail = 'not-an-email-sensitive-input';

      final client = MockClient((request) async {
        return http.Response(
          jsonEncode({
            'success': false,
            'error': 'Validation failed',
            'details': [
              {
                'loc': ['body', 'email'],
                'msg': 'Enter a valid email address.',
                'type': 'value_error',
                'input': invalidEmail,
              },
            ],
          }),
          422,
        );
      });
      addTearDown(client.close);

      await tester.pumpWidget(
        MaterialApp(
          home: PatientRegistrationScreen(
            dataSource: _dataSource(client),
            bearerToken: 'test-token',
          ),
        ),
      );
      await tester.pumpAndSettle();
      await _enterRequiredNames(tester);

      final emailField = find.byKey(
        const ValueKey('patient-registration-email'),
      );
      final scrollable = find.byType(Scrollable).first;
      await tester.scrollUntilVisible(emailField, 200, scrollable: scrollable);
      await tester.enterText(emailField, invalidEmail);

      await _scrollToAndTapSubmit(tester);

      final serverErrorEmail = find.byKey(
        const ValueKey('patient-registration-server-error-email'),
      );
      await tester.scrollUntilVisible(
        serverErrorEmail,
        -200,
        scrollable: scrollable,
      );
      await tester.pumpAndSettle();

      expect(serverErrorEmail, findsOneWidget);
      expect(find.text('Enter a valid email address.'), findsOneWidget);
      expect(
        find.text('Please correct the highlighted fields and try again.'),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: serverErrorEmail,
          matching: find.textContaining(invalidEmail),
        ),
        findsNothing,
      );
      expect(find.text('Patient registered'), findsNothing);
    },
  );

  testWidgets(
    'connection failure blocks repeat submission when outcome is unknown',
    (tester) async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        throw http.ClientException('simulated connection interruption');
      });
      addTearDown(client.close);

      await tester.pumpWidget(
        MaterialApp(
          home: PatientRegistrationScreen(
            dataSource: _dataSource(client),
            bearerToken: 'test-token',
          ),
        ),
      );
      await tester.pumpAndSettle();
      await _enterRequiredNames(tester);

      await _scrollToAndTapSubmit(tester);

      expect(requests, 1);
      expect(
        find.textContaining('could not confirm whether registration completed'),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('patient-registration-return-button')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('patient-registration-submit-button')),
        findsNothing,
      );
    },
  );
}
