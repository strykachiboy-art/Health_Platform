import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_allergy_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_detail_screen.dart';

AuthSessionState _session({
  int clinicId = 3,
  String role = 'doctor',
  String token = 's02c-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 's02c-refresh',
    userId: 42,
    role: role,
    clinicContextId: clinicId,
  ),
);

Map<String, dynamic> _patient() => {
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

Map<String, dynamic> _allergy({
  int id = 11,
  String name = 'Penicillin',
  String severity = 'life_threatening',
  String clinical = 'active',
  String verification = 'confirmed',
}) => {
  'id': id,
  'clinic_id': 3,
  'patient_id': 7,
  'allergen_name': name,
  'allergen_code': null,
  'allergen_type': 'drug',
  'reaction': 'Anaphylaxis',
  'severity': severity,
  'clinical_status': clinical,
  'verification_status': verification,
  'onset_at': null,
  'recorded_at': '2026-11-06T09:00:00+00:00',
  'recorded_by_id': 12,
  'notes': null,
  'created_at': '2026-11-06T09:00:00+00:00',
  'updated_at': '2026-11-06T09:00:00+00:00',
};

Map<String, dynamic> _allergies({List<Map<String, dynamic>>? items}) {
  final rows = items ?? [_allergy()];
  return {
    'success': true,
    'data': {'items': rows, 'total': rows.length, 'page': 1, 'per_page': 20},
  };
}

ApiClient _api(MockClient client) => ApiClient(
  config: const BackendConfig(
    baseUrl: 'http://127.0.0.1:5000',
    allowInsecureHttp: true,
  ),
  client: client,
);

PatientRemoteDataSource _patientSource(ApiClient api) =>
    PatientRemoteDataSource(
      apiClient: api,
      loadAccessToken: () async => 's02c-token',
    );

PatientAllergyRemoteDataSource _allergySource(
  ApiClient api,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => PatientAllergyRemoteDataSource(
  apiClient: api,
  loadSession: loader ?? () async => _session(),
  sessionNavigationGuard: guard,
);

Widget _screen(
  ApiClient api,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
  double textScale = 1,
}) => MaterialApp(
  builder: (context, child) => MediaQuery(
    data: MediaQuery.of(context)
        .copyWith(textScaler: TextScaler.linear(textScale)),
    child: child!,
  ),
  home: PatientDetailScreen(
    dataSource: _patientSource(api),
    patientId: 7,
    allergyDataSource: _allergySource(api, guard, loader: loader),
  ),
);

Future<void> _showAllergy(WidgetTester tester) async {
  await tester.scrollUntilVisible(
    find.byKey(const ValueKey('patient-structured-allergies-card')),
    200,
  );
  await tester.pumpAndSettle();
}

void main() {
  for (final width in <double>[390, 1100]) {
    testWidgets(
      '${width.toInt()}px: large-text patient identity and allergy labels',
      (tester) async {
        tester.view.physicalSize = Size(width, 1000);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        // testWidgets manages its semantics handle.

        final client = MockClient((request) async {
          if (request.url.path == '/api/v1/patients/7') {
            return http.Response(
              jsonEncode({'success': true, 'data': _patient()}),
              200,
            );
          }
          if (request.url.path == '/api/v1/patients/7/allergies') {
            return http.Response(jsonEncode(_allergies()), 200);
          }
          throw StateError('Unexpected request');
        });
        addTearDown(client.close);

        final guard = SessionNavigationGuard()..markSessionAvailable();
        addTearDown(guard.dispose);

        await tester.pumpWidget(_screen(_api(client), guard, textScale: 1.25));
        await tester.pumpAndSettle();

        expect(find.text('Ada Lovelace'), findsOneWidget);
        expect(
          find.byKey(const ValueKey('patient-detail-name')),
          findsOneWidget,
        );

        await _showAllergy(tester);

        expect(
          find.byKey(const ValueKey('patient-structured-allergies-card')),
          findsOneWidget,
        );
        expect(find.text('Penicillin'), findsOneWidget);
        expect(find.textContaining('Life-threatening'), findsOneWidget);
        expect(find.textContaining('Verification: Confirmed'), findsOneWidget);
        expect(find.byTooltip('Refresh structured allergies'), findsOneWidget);
        expect(tester.takeException(), isNull);
      },
    );
  }

  testWidgets('resolved and refuted records retain distinct labels', (
    tester,
  ) async {
    final client = MockClient((request) async {
      if (request.url.path == '/api/v1/patients/7') {
        return http.Response(
          jsonEncode({'success': true, 'data': _patient()}),
          200,
        );
      }
      return http.Response(
        jsonEncode(
          _allergies(
            items: [
              _allergy(id: 11, clinical: 'resolved', verification: 'confirmed'),
              _allergy(
                id: 12,
                name: 'Latex',
                clinical: 'inactive',
                verification: 'refuted',
              ),
            ],
          ),
        ),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_screen(_api(client), guard));
    await tester.pumpAndSettle();
    await _showAllergy(tester);

    expect(find.text('Penicillin'), findsOneWidget);
    expect(find.text('Latex'), findsOneWidget);
    expect(find.textContaining('Clinical: Resolved'), findsOneWidget);
    expect(find.textContaining('Verification: Refuted'), findsOneWidget);
  });

  testWidgets('patient refresh 401 removes identity and allergy PHI', (
    tester,
  ) async {
    var patientGets = 0;

    final client = MockClient((request) async {
      if (request.url.path == '/api/v1/patients/7') {
        patientGets++;
        if (patientGets == 2) {
          return http.Response('{"error":"Unauthorized"}', 401);
        }
        return http.Response(
          jsonEncode({'success': true, 'data': _patient()}),
          200,
        );
      }

      return http.Response(jsonEncode(_allergies()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_screen(_api(client), guard));
    await tester.pumpAndSettle();
    await _showAllergy(tester);

    expect(find.text('Penicillin'), findsOneWidget);

    await tester.tap(
      find.byKey(const ValueKey('patient-detail-refresh-button')),
    );
    await tester.pumpAndSettle();

    expect(patientGets, 2);
    expect(
      find.byKey(const ValueKey('patient-detail-name'), skipOffstage: false),
      findsNothing,
    );
    expect(
      find.byKey(
        const ValueKey('patient-structured-allergies-card'),
        skipOffstage: false,
      ),
      findsNothing,
    );
    expect(find.text('Penicillin', skipOffstage: false), findsNothing);
  });

  testWidgets('clinic switch discards pending allergy response', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    final allergyStarted = Completer<void>();
    var session = _session();

    final client = MockClient((request) {
      if (request.url.path == '/api/v1/patients/7') {
        return Future.value(
          http.Response(jsonEncode({'success': true, 'data': _patient()}), 200),
        );
      }

      if (request.url.path == '/api/v1/patients/7/allergies') {
        allergyStarted.complete();
        return pending.future;
      }

      throw StateError('Unexpected request');
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(
      _screen(_api(client), guard, loader: () async => session),
    );
    await tester.pump();
    // Wait a bounded number of frames for patient identity to render.
    for (
      var attempt = 0;
      attempt < 40 &&
          find.byKey(const ValueKey('patient-detail-name')).evaluate().isEmpty;
      attempt++
    ) {
      await tester.pump(const Duration(milliseconds: 10));
    }

    expect(
      find.byKey(const ValueKey('patient-detail-name')),
      findsOneWidget,
      reason: 'The patient identity must load before allergy access.',
    );

    // The structured allergy card sits below the initial ListView viewport.
    // Its HTTP GET cannot start until the card is actually built.
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('patient-structured-allergies-card')),
      200,
      maxScrolls: 30,
    );

    for (
      var attempt = 0;
      attempt < 40 && !allergyStarted.isCompleted;
      attempt++
    ) {
      await tester.pump(const Duration(milliseconds: 10));
    }

    expect(
      allergyStarted.isCompleted,
      isTrue,
      reason: 'The visible allergy card must start its authenticated GET.',
    );

    session = _session(clinicId: 9, token: 'other-clinic-token');

    pending.complete(http.Response(jsonEncode(_allergies()), 200));

    await tester.pumpAndSettle();
    await _showAllergy(tester);

    expect(find.text('Penicillin'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-allergies-error')),
      findsOneWidget,
    );
  });

  testWidgets('patient role cannot fetch structured allergies', (tester) async {
    var allergyRequests = 0;

    final client = MockClient((request) async {
      if (request.url.path == '/api/v1/patients/7') {
        return http.Response(
          jsonEncode({'success': true, 'data': _patient()}),
          200,
        );
      }

      allergyRequests++;
      return http.Response(jsonEncode(_allergies()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(
      _screen(
        _api(client),
        guard,
        loader: () async => _session(role: 'patient'),
      ),
    );

    await tester.pumpAndSettle();
    await _showAllergy(tester);

    expect(allergyRequests, 0);
    expect(find.text('Penicillin'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-allergies-error')),
      findsOneWidget,
    );
  });

  testWidgets('router logout removes protected patient route', (tester) async {
    final client = MockClient((request) async {
      if (request.url.path == '/api/v1/patients/7') {
        return http.Response(
          jsonEncode({'success': true, 'data': _patient()}),
          200,
        );
      }
      if (request.url.path == '/api/v1/patients/7/allergies') {
        return http.Response(jsonEncode(_allergies()), 200);
      }
      throw StateError('Unexpected request');
    });
    addTearDown(client.close);

    final api = _api(client);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Center(child: Text('Login'))),
      patientDataSource: _patientSource(api),
      patientAllergyDataSource: _allergySource(api, guard),
      sessionNavigationGuard: guard,
    );
    addTearDown(router.dispose);

    await tester.pumpWidget(MaterialApp.router(routerConfig: router));

    router.go('/patients/7');
    await tester.pumpAndSettle();
    await _showAllergy(tester);

    expect(find.text('Penicillin'), findsOneWidget);

    guard.markSignedOut();
    await tester.pumpAndSettle();

    expect(find.text('Login'), findsOneWidget);
    expect(
      find.byKey(
        const ValueKey('patient-structured-allergies-card'),
        skipOffstage: false,
      ),
      findsNothing,
    );
    expect(find.text('Penicillin', skipOffstage: false), findsNothing);
    expect(find.text('Ada Lovelace', skipOffstage: false), findsNothing);
  });
}
