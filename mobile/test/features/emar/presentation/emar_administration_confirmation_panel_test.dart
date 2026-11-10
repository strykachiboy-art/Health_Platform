import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/emar_medication_administration_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/emar_barcode_verification.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';
import 'package:clinic_system_pro/features/emar/presentation/emar_administration_confirmation_panel.dart';

AuthSessionState ready() => AuthSessionState.ready(
  const AuthTokenResponse(
    accessToken: 'panel-token',
    refreshToken: 'panel-refresh',
    userId: 42,
    role: 'nurse',
    clinicContextId: 3,
  ),
);

Map<String, dynamic> row({String status = 'due'}) => {
  'id': 11,
  'clinic_id': 3,
  'patient_id': 5,
  'admission_id': 7,
  'prescription_id': 17,
  'prescription_item_id': 19,
  'status': status,
  'record_status': status == 'administered' ? 'final' : 'draft',
  'record_version': 1,
  'source_order_version': 2,
  'dose_value': '0.125',
  'dose_unit': 'mg',
  'route_code': 'oral',
  'scheduled_at': '2026-11-09T11:00:00Z',
  'administered_at': status == 'administered' ? '2026-11-09T11:01:00Z' : null,
  'verification_result': 'barcode_verified',
  'barcode_verification_event_id': status == 'administered' ? 41 : null,
};

EmarBarcodeVerification verification() => const EmarBarcodeVerification(
  id: 41,
  clinicId: 3,
  administrationId: 11,
  patientId: 5,
  admissionId: 7,
  result: EmarBarcodeResult.barcodeVerified,
  failureCode: null,
  patientMatch: true,
  medicationMatch: true,
  doseMatch: true,
  routeMatch: true,
  timeWindowMatch: true,
  sourceOrderVersion: 2,
  policyVersion: 1,
);

Map<String, dynamic> history() => {
  'history': {
    'total': 1,
    'items': [
      {
        'id': 41,
        'clinic_id': 3,
        'medication_administration_id': 11,
        'actor_staff_id': 17,
        'verification_result': 'barcode_verified',
        'failure_code': null,
        'failure_reason': null,
        'patient_match': true,
        'medication_match': true,
        'dose_match': true,
        'route_match': true,
        'time_window_match': true,
        'scan_timestamp': '2026-11-09T11:00:00Z',
        'verification_policy_version': 1,
      },
    ],
  },
};

Widget screen(MockClient client, SessionNavigationGuard guard) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  final read = EmarReadRemoteDataSource(
    apiClient: api,
    loadSession: () async => ready(),
    sessionNavigationGuard: guard,
  );
  final adapter = EmarMedicationAdministrationRemoteDataSource(
    apiClient: api,
    readSource: read,
    loadSession: () async => ready(),
    sessionNavigationGuard: guard,
  );
  return MaterialApp(
    home: Scaffold(
      body: SingleChildScrollView(
        child: EmarAdministrationConfirmationPanel(
          adapter: adapter,
          reviewed: EmarAdministration.fromJson(
            row(),
            expectedClinicId: 3,
            expectedPatientId: 5,
            expectedAdmissionId: 7,
            expectedId: 11,
          ),
          verification: verification(),
        ),
      ),
    ),
  );
}

void wide(WidgetTester tester) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(1300, 2400);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

void main() {
  testWidgets(
    'verification does NOT automatically administer; checkbox required',
    (tester) async {
      wide(tester);
      var requests = 0;
      final client = MockClient((_) async {
        requests++;
        return http.Response('{}', 500);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(screen(client, guard));
      await tester.pump();
      final button = tester.widget<FilledButton>(
        find.byKey(const ValueKey('p23f-s04-submit')),
      );
      expect(button.onPressed, isNull);
      expect(requests, 0);
      expect(find.textContaining('A scan is NOT proof'), findsOneWidget);
    },
  );

  testWidgets('explicit attestation sends exactly one administration', (
    tester,
  ) async {
    wide(tester);
    var posts = 0;
    var details = 0;
    final client = MockClient((request) async {
      if (request.url.path.endsWith('barcode-events')) {
        return http.Response(jsonEncode(history()), 200);
      }
      if (request.method == 'GET') {
        details++;
        return http.Response(
          jsonEncode({
            'administration': row(
              status: details == 1 ? 'due' : 'administered',
            ),
          }),
          200,
        );
      }
      posts++;
      expect(request.url.path, '/api/v1/emar/11/administer');
      expect(request.headers['idempotency-key'], isNotEmpty);
      expect(jsonDecode(request.body), {'barcode_verification_event_id': 41});
      return http.Response(
        jsonEncode({'administration': row(status: 'administered')}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(screen(client, guard));
    await tester.tap(find.byKey(const ValueKey('p23f-s04-attestation')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('p23f-s04-submit')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(
      find.textContaining('Server confirmed administration #11'),
      findsOneWidget,
    );
    final button = tester.widget<FilledButton>(
      find.byKey(const ValueKey('p23f-s04-submit')),
    );
    expect(button.onPressed, isNull);
  });

  testWidgets('unknown outcome locks button and never repeats POST', (
    tester,
  ) async {
    wide(tester);
    var posts = 0;
    final client = MockClient((request) async {
      if (request.url.path.endsWith('barcode-events')) {
        return http.Response(jsonEncode(history()), 200);
      }
      if (request.method == 'GET') {
        return http.Response(jsonEncode({'administration': row()}), 200);
      }
      posts++;
      throw http.ClientException('response lost');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(screen(client, guard));
    await tester.tap(find.byKey(const ValueKey('p23f-s04-attestation')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('p23f-s04-submit')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('Do NOT administer again'), findsOneWidget);
    final button = tester.widget<FilledButton>(
      find.byKey(const ValueKey('p23f-s04-submit')),
    );
    expect(button.onPressed, isNull);
  });

  testWidgets('session invalidation removes attestation/submit', (
    tester,
  ) async {
    wide(tester);
    var posts = 0;
    final client = MockClient((_) async {
      posts++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(screen(client, guard));
    await tester.tap(find.byKey(const ValueKey('p23f-s04-attestation')));
    guard.invalidate();
    await tester.pump();
    final button = tester.widget<FilledButton>(
      find.byKey(const ValueKey('p23f-s04-submit')),
    );
    expect(button.onPressed, isNull);
    expect(posts, 0);
  });
}
