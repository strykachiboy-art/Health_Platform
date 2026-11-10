import 'dart:async';
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
import 'package:clinic_system_pro/features/emar/data/emar_barcode_verification_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';
import 'package:clinic_system_pro/features/emar/presentation/emar_barcode_verification_screen.dart';
import 'package:clinic_system_pro/features/emar/presentation/emar_camera_barcode_capture.dart';

AuthSessionState ready({String role = 'nurse'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's03b-test',
    refreshToken: 's03b-refresh',
    userId: 42,
    role: role,
    clinicContextId: 3,
  ),
);

Map<String, dynamic> administration({
  int patientId = 5,
  int version = 2,
  int? eventId,
  bool verifiedResult = false,
}) => {
  'id': 11,
  'clinic_id': 3,
  'patient_id': patientId,
  'admission_id': 7,
  'prescription_id': 17,
  'prescription_item_id': 19,
  'status': 'due',
  'record_status': 'draft',
  'record_version': 1,
  'source_order_version': version,
  'dose_value': '0.125',
  'dose_unit': 'mg',
  'route_code': 'oral',
  'scheduled_at': '2026-11-09T11:00:00Z',
  'barcode_verification_event_id': eventId,
  'verification_result': eventId != null || verifiedResult ? 'barcode_verified' : null,
};

EmarAdministration selected() => EmarAdministration.fromJson(
  administration(),
  expectedClinicId: 3,
  expectedPatientId: 5,
  expectedAdmissionId: 7,
);

const scanTime = '2026-11-09T11:00:00.000Z';
final capturedAt = DateTime.utc(2026, 11, 9, 11);

EmarCapturedBarcode captured(EmarScanKind kind) => EmarCapturedBarcode(
  value: kind == EmarScanKind.patient ? 'PAT-5' : 'MED-3',
  capturedAtUtc: capturedAt,
);

Map<String, dynamic> event(http.Request request, {bool success = true}) {
  final payload = jsonDecode(request.body) as Map<String, dynamic>;
  return {
    'id': 41,
    'clinic_id': 3,
    'patient_id': success ? 5 : null,
    'admission_id': success ? 7 : null,
    'medication_administration_id': success ? 11 : null,
    'actor_staff_id': 17,
    'patient_barcode': payload['patient_barcode'],
    'medication_barcode': payload['medication_barcode'],
    'scan_timestamp': payload['scan_timestamp'],
    'scan_device': payload['scan_device'],
    'operation_identity': request.headers['idempotency-key'],
    'verification_result': success
        ? 'barcode_verified'
        : 'not_barcode_verified',
    'failure_code': success ? null : 'unknown_patient_barcode',
    'failure_reason': success ? null : 'Barcode not found',
    'patient_match': success,
    'medication_match': success,
    'dose_match': success,
    'route_match': success,
    'time_window_match': success,
    'source_order_version': success ? 2 : null,
    'verification_policy_version': 1,
  };
}

Widget screen({
  required MockClient client,
  required SessionNavigationGuard guard,
  EmarBarcodeCapture? capture,
  Future<AuthSessionState> Function()? loader,
}) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  final load = loader ?? () async => ready();
  final read = EmarReadRemoteDataSource(
    apiClient: api,
    loadSession: load,
    sessionNavigationGuard: guard,
  );
  final adapter = EmarBarcodeVerificationRemoteDataSource(
    apiClient: api,
    readSource: read,
    loadSession: load,
    sessionNavigationGuard: guard,
  );
  return MaterialApp(
    home: EmarBarcodeVerificationScreen(
      adapter: adapter,
      reviewed: selected(),
      capture: capture ?? (context, kind, guard) async => captured(kind),
    ),
  );
}

Future<void> scanBoth(WidgetTester tester) async {
  await tester.tap(find.byKey(const ValueKey('p23f-s03b-scan-patient')));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(const ValueKey('p23f-s03b-scan-medication')));
  await tester.pumpAndSettle();
}

void wide(WidgetTester tester) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(1200, 2200);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

void main() {
  testWidgets(
    'two camera captures require human confirmation; one verification POST',
    (tester) async {
      wide(tester);
      var gets = 0;
      var posts = 0;
      var administered = 0;
      final client = MockClient((request) async {
        if (request.url.path == '/api/v1/emar/11/barcode-events') {
          gets++;
        return http.Response(jsonEncode({'history': {'total': 1, 'items': [{
          'id': 41, 'clinic_id': 3, 'medication_administration_id': 11,
          'actor_staff_id': 17, 'verification_result': 'barcode_verified',
          'failure_code': null, 'failure_reason': null,
          'patient_match': true, 'medication_match': true,
          'dose_match': true, 'route_match': true,
          'time_window_match': true,
          'scan_timestamp': '2026-11-09T11:00:00Z',
          'verification_policy_version': 1,
        }]}}), 200);
      }
        if (request.method == 'GET') {
          gets++;
          expect(request.url.path, '/api/v1/emar/11');
          return http.Response(
            jsonEncode({
              'administration': administration(verifiedResult: gets == 3),
            }),
            200,
          );
        }
        posts++;
        if (request.url.path.contains('/administer')) administered++;
        expect(request.url.path, '/api/v1/emar/barcode/verify');
        expect(request.headers['authorization'], 'Bearer s03b-test');
        expect(request.headers['idempotency-key'], isNotEmpty);
        expect(jsonDecode(request.body)['scan_timestamp'], scanTime);
        expect(
          jsonDecode(request.body)['scan_device'],
          'flutter_mobile_scanner_camera',
        );
        return http.Response(jsonEncode({'verification': event(request)}), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(screen(client: client, guard: guard));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p23f-s03b-verify-once')),
        findsOneWidget,
      );
      expect(
        tester
            .widget<FilledButton>(
              find.byKey(const ValueKey('p23f-s03b-verify-once')),
            )
            .onPressed,
        isNull,
      );
      await scanBoth(tester);
      expect(posts, 0);
      expect(
        tester
            .widget<FilledButton>(
              find.byKey(const ValueKey('p23f-s03b-verify-once')),
            )
            .onPressed,
        isNotNull,
      );
      expect(find.textContaining('PAT-5'), findsNothing);
      expect(find.textContaining('MED-3'), findsNothing);
      await tester.tap(find.byKey(const ValueKey('p23f-s03b-verify-once')));
      await tester.pumpAndSettle();
      expect(gets, 4);
      expect(posts, 1);
      expect(administered, 0);
      expect(
        find.textContaining('Medication NOT administered'),
        findsOneWidget,
      );
      expect(find.text('Verification event #41'), findsOneWidget);
      expect(
        tester
            .widget<FilledButton>(
              find.byKey(const ValueKey('p23f-s03b-verify-once')),
            )
            .onPressed,
        isNull,
      );
    },
  );

  testWidgets('durable failed HTTP 200 blocks workflow, no administration', (
    tester,
  ) async {
    wide(tester);
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return http.Response(
          jsonEncode({'administration': administration()}),
          200,
        );
      }
      posts++;
      return http.Response(
        jsonEncode({'verification': event(request, success: false)}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(screen(client: client, guard: guard));
    await tester.pumpAndSettle();
    await scanBoth(tester);
    await tester.tap(find.byKey(const ValueKey('p23f-s03b-verify-once')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('verification FAILED'), findsOneWidget);
    expect(find.textContaining('Do not administer'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(
            find.byKey(const ValueKey('p23f-s03b-verify-once')),
          )
          .onPressed,
      isNull,
    );
  });

  testWidgets('doctor role cannot capture or issue verification request', (
    tester,
  ) async {
    wide(tester);
    var calls = 0;
    var scans = 0;
    final client = MockClient((request) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      screen(
        client: client,
        guard: guard,
        loader: () async => ready(role: 'doctor'),
        capture: (context, kind, guard) async {
          scans++;
          return captured(kind);
        },
      ),
    );
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(scans, 0);
    expect(find.byKey(const ValueKey('p23f-s03b-scan-patient')), findsNothing);
    expect(find.textContaining('Nurse authorization'), findsOneWidget);
  });

  testWidgets('camera cancellation does not send server mutation', (
    tester,
  ) async {
    wide(tester);
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') posts++;
      return http.Response(
        jsonEncode({'administration': administration()}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      screen(
        client: client,
        guard: guard,
        capture: (context, kind, guard) async => null,
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23f-s03b-scan-patient')));
    await tester.pumpAndSettle();
    expect(find.text('Patient barcode not captured.'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(
            find.byKey(const ValueKey('p23f-s03b-verify-once')),
          )
          .onPressed,
      isNull,
    );
    expect(posts, 0);
  });

  testWidgets('guard invalidation during scan discards captured barcode', (
    tester,
  ) async {
    wide(tester);
    final pending = Completer<EmarCapturedBarcode?>();
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') posts++;
      return http.Response(
        jsonEncode({'administration': administration()}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      screen(
        client: client,
        guard: guard,
        capture: (context, kind, guard) => pending.future,
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('p23f-s03b-scan-patient')));
    await tester.pump();
    guard.invalidate();
    pending.complete(captured(EmarScanKind.patient));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23f-s03b-unavailable')), findsOneWidget);
    expect(find.textContaining('PAT-5'), findsNothing);
    expect(posts, 0);
  });

  testWidgets(
    'changed clinical order prevents camera capture and verification',
    (tester) async {
      wide(tester);
      var posts = 0;
      var scans = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') posts++;
        return http.Response(
          jsonEncode({'administration': administration(version: 3)}),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(
        screen(
          client: client,
          guard: guard,
          capture: (context, kind, guard) async {
            scans++;
            return captured(kind);
          },
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('p23f-s03b-scan-patient')),
        findsNothing,
      );
      expect(find.textContaining('schedule changed'), findsOneWidget);
      expect(scans, 0);
      expect(posts, 0);
    },
  );

  testWidgets('timeout locks verification action; no auto replay', (
    tester,
  ) async {
    wide(tester);
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return http.Response(
          jsonEncode({'administration': administration()}),
          200,
        );
      }
      posts++;
      throw http.ClientException('simulated lost response');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(screen(client: client, guard: guard));
    await tester.pumpAndSettle();
    await scanBoth(tester);
    await tester.tap(find.byKey(const ValueKey('p23f-s03b-verify-once')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('outcome unconfirmed'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(
            find.byKey(const ValueKey('p23f-s03b-verify-once')),
          )
          .onPressed,
      isNull,
    );
  });
}
