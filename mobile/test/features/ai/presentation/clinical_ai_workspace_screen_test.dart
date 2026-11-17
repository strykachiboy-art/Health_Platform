import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/ai/data/clinical_ai_remote_data_source.dart';
import 'package:clinic_system_pro/features/ai/presentation/clinical_ai_workspace_screen.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient.dart';

class _Connected implements DeviceConnectivitySource {
  @override
  Future<DeviceNetworkStatus> checkStatus() async =>
      DeviceNetworkStatus.connected;
  @override
  Stream<DeviceNetworkStatus> get statusChanges => const Stream.empty();
}

Patient _patient({int clinic = 7}) => Patient(
  id: 3,
  clinicId: clinic,
  patientNumber: 'P-003',
  firstName: 'Test',
  lastName: 'Patient',
  dateOfBirth: null,
  gender: null,
  bloodType: null,
  phone: null,
  email: null,
  address: null,
  allergies: null,
  chronicConditions: null,
  emiratesId: null,
  umrn: null,
  isActive: true,
  createdAt: DateTime.utc(2026),
  updatedAt: DateTime.utc(2026),
);

void main() {
  testWidgets('patient verified before UI, no AI POST on screen opening', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    var posts = 0;
    final client = MockClient((request) async {
      posts++;
      return http.Response(
        jsonEncode({
          'success': true,
          'data': {'interactions': [], 'recommendations': []},
        }),
        200,
      );
    });
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    final source = ClinicalAiRemoteDataSource(
      apiClient: api,
      guard: guard,
      connectivity: _Connected(),
      loadSession: () async => const AuthSessionState.ready(
        AuthTokenResponse(
          accessToken: 'token',
          refreshToken: 'refresh',
          userId: 5,
          role: 'doctor',
          clinicContextId: 7,
        ),
      ),
    );
    addTearDown(() {
      guard.dispose();
      api.close();
      client.close();
    });
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalAiWorkspaceScreen(
          patientId: 3,
          loadPatient: (_) async => _patient(),
          source: source,
          guard: guard,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('gap-ai-patient-context')),
      findsOneWidget,
    );
    expect(posts, 0);
    final button = tester.widget<FilledButton>(
      find.byKey(const ValueKey('gap-ai-submit')),
    );
    expect(button.onPressed, isNull); // No implicit credit-consuming request.
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('gap-ai-patient-context')), findsNothing);
    expect(posts, 0);
  });

  testWidgets('wrong-clinic patient never exposes advisory input', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    final client = MockClient(
      (_) async => throw StateError('No request expected'),
    );
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    final source = ClinicalAiRemoteDataSource(
      apiClient: api,
      guard: guard,
      connectivity: _Connected(),
      loadSession: () async => const AuthSessionState.ready(
        AuthTokenResponse(
          accessToken: 'token',
          refreshToken: 'refresh',
          userId: 5,
          role: 'doctor',
          clinicContextId: 7,
        ),
      ),
    );
    addTearDown(() {
      guard.dispose();
      api.close();
      client.close();
    });
    await tester.pumpWidget(
      MaterialApp(
        home: ClinicalAiWorkspaceScreen(
          patientId: 3,
          loadPatient: (_) async => _patient(clinic: 9),
          source: source,
          guard: guard,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('gap-ai-submit')), findsNothing);
    expect(find.textContaining('could not be verified'), findsOneWidget);
  });
}
