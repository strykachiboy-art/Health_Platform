import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';

const _token = 'test-access-token';

Map<String, dynamic> patientJson() => {
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
  'address': 'Test address',
  'allergies': 'Peanuts',
  'chronic_conditions': 'None',
  'emirates_id': null,
  'umrn': 'UMRN-7',
  'is_active': true,
  'created_at': '2026-01-01T10:00:00+00:00',
  'updated_at': '2026-01-02T10:00:00+00:00',
};

Map<String, dynamic> pageJson({
  List<Map<String, dynamic>>? items,
  int page = 1,
}) => {
  'success': true,
  'data': {
    'items': items ?? [patientJson()],
    'total': 1,
    'page': page,
    'per_page': 25,
  },
};

PatientRemoteDataSource dataSource(MockClient client) {
  return PatientRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
  );
}

void main() {
  group('PatientRemoteDataSource', () {
    test(
      'fetchPatients encodes search, pagination, active-only and bearer token',
      () async {
        final client = MockClient((request) async {
          expect(request.method, 'GET');
          expect(request.url.path, '/api/v1/patients');
          expect(request.url.queryParameters, {
            'search': 'Ada Lovelace',
            'page': '2',
            'per_page': '25',
            'active_only': 'true',
          });
          expect(request.headers['authorization'], 'Bearer $_token');
          return http.Response(jsonEncode(pageJson(page: 2)), 200);
        });
        addTearDown(client.close);

        final page = await dataSource(client).fetchPatients(
          bearerToken: _token,
          search: '  Ada Lovelace  ',
          page: 2,
        );

        expect(page.total, 1);
        expect(page.page, 2);
        expect(page.items.single.fullName, 'Ada Lovelace');
        expect(page.items.single.patientNumber, 'PT-0007');
        expect(page.items.single.phone, '+2348000000000');
      },
    );

    test('fetchPatient parses details from the existing endpoint', () async {
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/patients/7');
        expect(request.headers['authorization'], 'Bearer $_token');
        return http.Response(
          jsonEncode({'success': true, 'data': patientJson()}),
          200,
        );
      });
      addTearDown(client.close);

      final patient = await dataSource(client)
          .fetchPatient(bearerToken: _token, patientId: 7);

      expect(patient.id, 7);
      expect(patient.dateOfBirth, DateTime(1815, 12, 10));
      expect(patient.allergies, 'Peanuts');
    });

    for (final desiredStatus in <bool>[true, false]) {
      test(
        'setPatientStatus sends is_active=$desiredStatus and parses response',
        () async {
          final client = MockClient((request) async {
            expect(request.method, 'PATCH');
            expect(request.url.path, '/api/v1/patients/7/status');
            expect(request.headers['authorization'], 'Bearer $_token');
            expect(jsonDecode(request.body), {'is_active': desiredStatus});
            final responsePatient = patientJson()
              ..['is_active'] = desiredStatus;
            return http.Response(
              jsonEncode({'success': true, 'data': responsePatient}),
              200,
            );
          });
          addTearDown(client.close);

          final patient = await dataSource(client).setPatientStatus(
            bearerToken: _token,
            patientId: 7,
            isActive: desiredStatus,
          );

          expect(patient.id, 7);
          expect(patient.isActive, desiredStatus);
        },
      );
    }

    test(
      'rejects a malformed success envelope instead of accepting partial data',
      () async {
        final client = MockClient((request) async {
          return http.Response(jsonEncode({'success': true, 'data': []}), 200);
        });
        addTearDown(client.close);

        await expectLater(
          dataSource(client).fetchPatient(bearerToken: _token, patientId: 7),
          throwsA(isA<FormatException>()),
        );
      },
    );

    test(
      'preserves typed unauthorized response for session-aware UI',
      () async {
        final client = MockClient((request) async {
          return http.Response(jsonEncode({'success': false}), 401);
        });
        addTearDown(client.close);

        await expectLater(
          dataSource(client).fetchPatients(bearerToken: _token),
          throwsA(
            isA<ApiException>()
                .having(
                  (error) => error.kind,
                  'kind',
                  ApiFailureKind.httpStatus,
                )
                .having((error) => error.statusCode, 'statusCode', 401),
          ),
        );
      },
    );

    test('rejects blank bearer token before issuing a request', () async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return http.Response(jsonEncode(pageJson()), 200);
      });
      addTearDown(client.close);

      await expectLater(
        dataSource(client).fetchPatients(bearerToken: '  '),
        throwsArgumentError,
      );
      expect(requests, 0);
    });
  });
}
