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
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/consultations/presentation/consultation_start_screen.dart';
import 'package:clinic_system_pro/features/consultations/presentation/consultation_detail_screen.dart';

AuthSessionState _session() => const AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'widget-token',
    refreshToken: 'refresh-token',
    userId: 42,
    role: 'doctor',
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _record() => {
  'id': 11,
  'clinic_id': 3,
  'patient_id': 7,
  'staff_id': 8,
  'appointment_id': null,
  'icd10_code': null,
  'consultation_type': 'general',
  'status': 'in_progress',
  'record_status': 'draft',
  'record_version': 1,
  'finalized_by_staff_id': null,
  'finalized_at': null,
  'latest_amendment_id': null,
  'chief_complaint': 'Fever',
  'symptoms': null,
  'diagnosis': null,
  'treatment_plan': null,
  'notes': null,
  'voice_note_url': null,
  'transcribed_text': null,
  'template_id': null,
  'started_at': '2026-11-07T08:00:00',
  'ended_at': null,
  'created_at': '2026-11-07T08:00:00+00:00',
  'updated_at': '2026-11-07T08:00:00+00:00',
};

ConsultationRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard,
) => ConsultationRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _session(),
  sessionNavigationGuard: guard,
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('one start action gives one POST and opens the returned record', (
    tester,
  ) async {
    var posts = 0;
    final client = MockClient((request) async {
      posts++;
      return http.Response(
        jsonEncode({'success': true, 'data': _record()}),
        201,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final opened = <int>[];
    await tester.pumpWidget(
      MaterialApp(
        home: ConsultationStartScreen(
          dataSource: _source(client, guard),
          onCreated: (p, c) => opened.addAll([p, c]),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('p23c-patient-id')), '7');
    await tester.enterText(find.byKey(const ValueKey('p23c-staff-id')), '8');
    await tester.ensureVisible(find.byKey(const ValueKey('p23c-submit')));
    await tester.tap(find.byKey(const ValueKey('p23c-submit')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(opened, [7, 11]);
    expect(
      tester
          .widget<ElevatedButton>(find.byKey(const ValueKey('p23c-submit')))
          .onPressed,
      isNull,
    );
  });

  testWidgets('uncertain start never enables automatic resend', (tester) async {
    var posts = 0;
    final client = MockClient((_) async {
      posts++;
      throw http.ClientException('transport interruption');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: ConsultationStartScreen(
          dataSource: _source(client, guard),
          onCreated: (_, _) {},
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('p23c-patient-id')), '7');
    await tester.enterText(find.byKey(const ValueKey('p23c-staff-id')), '8');
    await tester.ensureVisible(find.byKey(const ValueKey('p23c-submit')));
    await tester.tap(find.byKey(const ValueKey('p23c-submit')));
    await tester.pumpAndSettle();
    expect(posts, 1);
    expect(find.textContaining('Result not confirmed'), findsOneWidget);
    expect(
      tester
          .widget<ElevatedButton>(find.byKey(const ValueKey('p23c-submit')))
          .onPressed,
      isNull,
    );
  });

  testWidgets(
    'detail loads verified backend record and clears it after logout',
    (tester) async {
      final client = MockClient(
        (_) async => http.Response(
          jsonEncode({'success': true, 'data': _record()}),
          200,
        ),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: ConsultationDetailScreen(
            dataSource: _source(client, guard),
            patientId: 7,
            consultationId: 11,
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23c-detail-loaded')), findsOneWidget);
      guard.markSignedOut();
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23c-detail-loaded')), findsNothing);
    },
  );
}
