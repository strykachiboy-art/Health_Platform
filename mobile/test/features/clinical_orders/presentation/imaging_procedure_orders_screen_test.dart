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
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/presentation/imaging_procedure_orders_screen.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

AuthSessionState _ready(String role) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'widget-token',
    refreshToken: 'widget-refresh',
    userId: 42,
    role: role,
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _row({String kind = 'imaging'}) => <String, dynamic>{
  'id': 17,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 9,
  'ordered_by_staff_id': 8,
  'status': 'active',
  'priority': 'routine',
  'version': 1,
  'ordered_at': '2026-11-07T08:00:00+00:00',
  'indication': 'Clinical indication',
  if (kind == 'imaging') ...<String, dynamic>{
    'study_name': 'Chest X-ray',
    'modality': 'xray',
    'body_site': 'Chest',
  } else ...<String, dynamic>{
    'procedure_name': 'Wound repair',
    'procedure_type': 'bedside',
    'consent_required': true,
    'consent_verified_at': null,
  },
};

Widget _app(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
  String kind = 'imaging',
}) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  Future<AuthSessionState> load() async => _ready(role);
  return MaterialApp(
    home: ImagingProcedureOrdersScreen(
      unifiedSource: ClinicalOrdersRemoteDataSource(
        apiClient: api,
        loadSession: load,
        sessionNavigationGuard: guard,
      ),
      admissionDataSource: PatientAdmissionRemoteDataSource(
        apiClient: api,
        loadSession: load,
        sessionNavigationGuard: guard,
      ),
      patientId: 7,
      kind: kind,
    ),
  );
}

void _wide(WidgetTester tester) {
  tester.view.physicalSize = const Size(1200, 3000);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

void main() {
  testWidgets('doctor sees imaging create and admission-verified context', (
    tester,
  ) async {
    _wide(tester);
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) {
        return http.Response(
          jsonEncode({
            'id': 9,
            'patient_id': 7,
            'bed_id': 2,
            'admitted_by_id': 8,
            'status': 'admitted',
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'items': [_row()],
          'total': 1,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23d-b-ready')), findsOneWidget);
    expect(find.textContaining('Verified admission #9'), findsOneWidget);
    expect(find.byKey(const ValueKey('p23d-b-create')), findsOneWidget);
    expect(find.text('Chest X-ray'), findsOneWidget);
  });

  testWidgets('nurse reads imaging but cannot write', (tester) async {
    _wide(tester);
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) {
        return http.Response(
          jsonEncode({
            'id': 9,
            'patient_id': 7,
            'bed_id': 2,
            'admitted_by_id': 8,
            'status': 'admitted',
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'items': [_row()],
          'total': 1,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard, role: 'nurse'));
    await tester.pumpAndSettle();
    expect(find.text('Chest X-ray'), findsOneWidget);
    expect(find.byKey(const ValueKey('p23d-b-create')), findsNothing);
    expect(find.textContaining('Read-only'), findsOneWidget);
  });

  testWidgets('doctor submits one imaging order and locks all further writes', (
    tester,
  ) async {
    _wide(tester);
    var posts = 0;
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) {
        return http.Response(
          jsonEncode({
            'id': 9,
            'patient_id': 7,
            'bed_id': 2,
            'admitted_by_id': 8,
            'status': 'admitted',
          }),
          200,
        );
      }
      if (req.method == 'POST') {
        posts++;
        return http.Response(
          jsonEncode({'order': _row(), 'message': 'created'}),
          201,
        );
      }
      return http.Response(jsonEncode({'items': [], 'total': 0}), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('p23d-b-name')),
      'Chest X-ray',
    );
    await tester.enterText(
      find.byKey(const ValueKey('p23d-b-body-site')),
      'Chest',
    );
    await tester.enterText(
      find.byKey(const ValueKey('p23d-b-indication')),
      'Clinical indication',
    );
    await tester.tap(find.byKey(const ValueKey('p23d-b-create')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('Server confirmed order #17'), findsOneWidget);
    final button = tester.widget<FilledButton>(
      find.byKey(const ValueKey('p23d-b-create')),
    );
    expect(button.onPressed, isNull);
  });

  testWidgets('uncertain imaging POST remains locked with no retry', (
    tester,
  ) async {
    _wide(tester);
    var posts = 0;
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) {
        return http.Response(
          jsonEncode({
            'id': 9,
            'patient_id': 7,
            'bed_id': 2,
            'admitted_by_id': 8,
            'status': 'admitted',
          }),
          200,
        );
      }
      if (req.method == 'POST') {
        posts++;
        throw http.ClientException('unknown outcome');
      }
      return http.Response(jsonEncode({'items': [], 'total': 0}), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('p23d-b-name')),
      'Chest X-ray',
    );
    await tester.enterText(
      find.byKey(const ValueKey('p23d-b-body-site')),
      'Chest',
    );
    await tester.enterText(
      find.byKey(const ValueKey('p23d-b-indication')),
      'Clinical indication',
    );
    await tester.tap(find.byKey(const ValueKey('p23d-b-create')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('Outcome not confirmed'), findsOneWidget);
    expect(
      tester
          .widget<FilledButton>(find.byKey(const ValueKey('p23d-b-create')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('procedure requires verified consent before completion', (
    tester,
  ) async {
    _wide(tester);
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) {
        return http.Response(
          jsonEncode({
            'id': 9,
            'patient_id': 7,
            'bed_id': 2,
            'admitted_by_id': 8,
            'status': 'admitted',
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'items': [_row(kind: 'procedures')],
          'total': 1,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard, kind: 'procedures'));
    await tester.pumpAndSettle();
    expect(find.text('Consent pending verification'), findsOneWidget);
    expect(find.byKey(const ValueKey('p23d-b-verify-17')), findsOneWidget);
    expect(
      tester
          .widget<OutlinedButton>(
            find.byKey(const ValueKey('p23d-b-verify-17')),
          )
          .onPressed,
      isNull,
    );
    expect(
      tester
          .widget<OutlinedButton>(
            find.byKey(const ValueKey('p23d-b-complete-17')),
          )
          .onPressed,
      isNull,
    );
  });

  testWidgets('sign-out immediately removes order data and write actions', (
    tester,
  ) async {
    _wide(tester);
    final client = MockClient((req) async {
      if (req.url.path.endsWith('current-admission')) {
        return http.Response(
          jsonEncode({
            'id': 9,
            'patient_id': 7,
            'bed_id': 2,
            'admitted_by_id': 8,
            'status': 'admitted',
          }),
          200,
        );
      }
      return http.Response(
        jsonEncode({
          'items': [_row()],
          'total': 1,
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(_app(client, guard));
    await tester.pumpAndSettle();
    expect(find.text('Chest X-ray'), findsOneWidget);
    guard.markSignedOut();
    await tester.pumpAndSettle();
    expect(find.text('Chest X-ray'), findsNothing);
    expect(find.byKey(const ValueKey('p23d-b-create')), findsNothing);
    expect(find.byKey(const ValueKey('p23d-b-unavailable')), findsOneWidget);
  });
}
