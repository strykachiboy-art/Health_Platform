import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/consultations/presentation/consultation_clinical_note_screen.dart';
import 'package:clinic_system_pro/features/consultations/presentation/consultation_detail_screen.dart';

AuthSessionState _ready() => AuthSessionState.ready(
  const AuthTokenResponse(
    accessToken: 'route-token',
    refreshToken: 'refresh',
    userId: 42,
    role: 'doctor',
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _row({int patient = 7}) => <String, dynamic>{
  'id': 11,
  'clinic_id': 3,
  'patient_id': patient,
  'staff_id': 8,
  'appointment_id': 22,
  'icd10_code': null,
  'consultation_type': 'general',
  'status': 'completed',
  'record_status': 'draft',
  'record_version': 1,
  'finalized_by_staff_id': null,
  'finalized_at': null,
  'latest_amendment_id': null,
  'chief_complaint': 'Fever',
  'symptoms': null,
  'diagnosis': 'Confirmed',
  'treatment_plan': null,
  'notes': null,
  'voice_note_url': null,
  'transcribed_text': null,
  'template_id': null,
  'started_at': '2026-11-07T08:00:00',
  'ended_at': '2026-11-07T08:30:00+00:00',
  'created_at': '2026-11-07T08:00:00+00:00',
  'updated_at': '2026-11-07T08:00:00+00:00',
};

class _Harness {
  _Harness({
    required this.guard,
    required this.client,
    this.sourceGuard,
    this.injectSource = true,
  });

  final SessionNavigationGuard guard;
  final MockClient client;
  final SessionNavigationGuard? sourceGuard;
  final bool injectSource;

  GoRouter router() {
    final api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    );
    final source = ConsultationRemoteDataSource(
      apiClient: api,
      loadSession: () async => _ready(),
      sessionNavigationGuard: sourceGuard ?? guard,
    );
    return createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(apiClient: api),
      loginScreen: const Scaffold(body: Center(child: Text('Login sentinel'))),
      loadHomeSession: () async => _ready(),
      consultationDataSource: injectSource ? source : null,
      sessionNavigationGuard: guard,
    );
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
    'detail route opens guarded clinical note with same patient scope',
    (tester) async {
      tester.view.devicePixelRatio = 1.0;
      tester.view.physicalSize = const Size(1200, 3000);
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.view.resetPhysicalSize);
      var reads = 0;
      final client = MockClient((request) async {
        reads++;
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/consultations/11');
        return http.Response(
          jsonEncode({'success': true, 'data': _row()}),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final router = _Harness(guard: guard, client: client).router();
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go('/consultations/patient/7/11');
      await tester.pumpAndSettle();
      expect(find.byType(ConsultationDetailScreen), findsOneWidget);
      expect(
        find.byKey(const ValueKey('p23c-open-clinical-note')),
        findsOneWidget,
      );
      await tester.tap(find.byKey(const ValueKey('p23c-open-clinical-note')));
      await tester.pumpAndSettle();
      expect(find.byType(ConsultationClinicalNoteScreen), findsOneWidget);
      expect(find.byKey(const ValueKey('p23c-note-loaded')), findsOneWidget);
      expect(reads, 2);
    },
  );

  testWidgets('missing consultation injection denies note deep link', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => throw StateError('No HTTP expected'),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final router = _Harness(
      guard: guard,
      client: client,
      injectSource: false,
    ).router();
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/consultations/patient/7/11/note');
    await tester.pumpAndSettle();
    expect(find.text('Clinical note unavailable.'), findsOneWidget);
    expect(find.byType(ConsultationClinicalNoteScreen), findsNothing);
  });

  testWidgets('consultation source with other guard denies note deep link', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => throw StateError('No HTTP expected'),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final other = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(other.dispose);
    final router = _Harness(
      guard: guard,
      client: client,
      sourceGuard: other,
    ).router();
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/consultations/patient/7/11/note');
    await tester.pumpAndSettle();
    expect(find.text('Clinical note unavailable.'), findsOneWidget);
    expect(find.byType(ConsultationClinicalNoteScreen), findsNothing);
  });

  for (final path in <String>[
    '/consultations/patient/0/11/note',
    '/consultations/patient/7/0/note',
    '/consultations/patient/oops/11/note',
  ]) {
    testWidgets('malformed or zero IDs deny clinical note: $path', (
      tester,
    ) async {
      final client = MockClient(
        (_) async => throw StateError('No HTTP expected'),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final router = _Harness(guard: guard, client: client).router();
      addTearDown(router.dispose);
      await tester.pumpWidget(MaterialApp.router(routerConfig: router));
      router.go(path);
      await tester.pumpAndSettle();
      expect(find.text('Clinical note unavailable.'), findsOneWidget);
      expect(find.byType(ConsultationClinicalNoteScreen), findsNothing);
    });
  }

  testWidgets('backend patient mismatch never exposes note document', (
    tester,
  ) async {
    final client = MockClient(
      (request) async => http.Response(
        jsonEncode({'success': true, 'data': _row(patient: 99)}),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final router = _Harness(guard: guard, client: client).router();
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/consultations/patient/7/11/note');
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23c-note-loaded')), findsNothing);
    expect(find.byKey(const ValueKey('p23c-note-unavailable')), findsOneWidget);
    expect(find.byKey(const ValueKey('p23c-note-Notes')), findsNothing);
  });
}
