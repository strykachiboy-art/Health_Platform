import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/security/secure_key_value_storage.dart';
import 'package:clinic_system_pro/features/auth/data/auth_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_store.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/auth/presentation/google_login_screen.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/main.dart';

const _accessToken = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiJ9.signature';
const _refreshToken =
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiI0MiIsInR5cGUiOiJyZWZyZXNoIn0.signature';

class _MemorySecureStorage implements SecureKeyValueStorage {
  final Map<String, String> values = {};

  @override
  Future<String?> read({required String key}) async => values[key];

  @override
  Future<void> write({required String key, required String value}) async {
    values[key] = value;
  }

  @override
  Future<void> delete({required String key}) async {
    values.remove(key);
  }
}

AuthTokenResponse _session() => const AuthTokenResponse(
  accessToken: _accessToken,
  refreshToken: _refreshToken,
  userId: 42,
  role: 'DOCTOR',
  clinicContextId: null,
);

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
  'address': 'Test address',
  'allergies': 'Peanuts',
  'chronic_conditions': 'Asthma',
  'emirates_id': null,
  'umrn': 'UMRN-7',
  'is_active': true,
  'created_at': '2026-01-01T10:00:00+00:00',
  'updated_at': '2026-01-02T10:00:00+00:00',
};

void main() {
  testWidgets(
    'signed-in patient navigation preserves token for directory, registration and detail',
    (tester) async {
      final storage = _MemorySecureStorage();
      final store = AuthSessionStore(storage: storage);
      await store.save(_session());

      final requestedPaths = <String>[];

      final client = MockClient((request) async {
        requestedPaths.add(request.url.path);

        expect(
          request.headers['authorization'],
          'Bearer $_accessToken',
          reason: 'Every patient request must use the signed-in access token.',
        );

        if (request.url.path == '/api/v1/patients') {
          return http.Response(
            jsonEncode({
              'success': true,
              'data': {
                'items': [_patientJson()],
                'total': 1,
                'page': 1,
                'per_page': 25,
              },
            }),
            200,
          );
        }

        if (request.url.path == '/api/v1/patients/7') {
          return http.Response(
            jsonEncode({'success': true, 'data': _patientJson()}),
            200,
          );
        }

        throw StateError(
          'Unexpected HTTP request: ${request.method} ${request.url}',
        );
      });
      addTearDown(client.close);

      const config = BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      );
      final apiClient = ApiClient(config: config, client: client);
      final authRemote = AuthRemoteDataSource(apiClient: apiClient);
      final coordinator = AuthSessionCoordinator(
        sessionStore: store,
        remoteDataSource: authRemote,
      );

      await tester.pumpWidget(
        ClinicSystemProApp(
          healthDataSource: BackendHealthRemoteDataSource(apiClient: apiClient),
          patientDataSource: PatientRemoteDataSource(apiClient: apiClient),
          loginScreen: GoogleLoginScreen(
            acquireGoogleIdToken: () async => null,
            authRemoteDataSource: authRemote,
            sessionCoordinator: coordinator,
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('authenticated-session')),
        findsOneWidget,
      );

      await tester.tap(
        find.byKey(const ValueKey('open-patient-directory-button')),
      );
      await tester.pumpAndSettle();

      expect(find.text('Patient directory'), findsOneWidget);
      expect(find.text('Ada Lovelace'), findsOneWidget);
      expect(requestedPaths, ['/api/v1/patients']);

      await tester.tap(find.byKey(const ValueKey('patient-register-button')));
      await tester.pumpAndSettle();

      expect(find.text('Register a new patient'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('patient-registration-first-name')),
        findsOneWidget,
      );
      expect(requestedPaths, [
        '/api/v1/patients',
      ], reason: 'Opening registration must not issue a registration request.');

      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(find.text('Patient directory'), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('patient-row-7')));
      await tester.pumpAndSettle();

      expect(find.text('Patient details'), findsOneWidget);
      expect(find.byKey(const ValueKey('patient-detail-name')), findsOneWidget);
      expect(requestedPaths, ['/api/v1/patients', '/api/v1/patients/7']);
    },
  );
}
