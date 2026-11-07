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
import 'package:clinic_system_pro/features/consultations/presentation/consultation_clinical_note_screen.dart';

AuthSessionState _ready({String role = 'doctor'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'widget-clinical-token',
    refreshToken: 'refresh-token',
    userId: 42,
    role: role,
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _row({
  String status = 'in_progress',
  String recordStatus = 'draft',
  int version = 1,
  String? notes,
}) => <String, dynamic>{
  'id': 11,
  'clinic_id': 3,
  'patient_id': 7,
  'staff_id': 8,
  'appointment_id': null,
  'icd10_code': null,
  'consultation_type': 'general',
  'status': status,
  'record_status': recordStatus,
  'record_version': version,
  'finalized_by_staff_id': recordStatus == 'draft' ? null : 8,
  'finalized_at': recordStatus == 'draft' ? null : '2026-11-07T08:30:00+00:00',
  'latest_amendment_id': recordStatus == 'amended' ? 200 : null,
  'chief_complaint': 'Fever',
  'symptoms': null,
  'diagnosis': status == 'completed' ? 'Confirmed' : null,
  'treatment_plan': null,
  'notes': notes,
  'voice_note_url': null,
  'transcribed_text': null,
  'template_id': null,
  'started_at': '2026-11-07T08:00:00',
  'ended_at': status == 'in_progress' ? null : '2026-11-07T08:20:00+00:00',
  'created_at': '2026-11-07T08:00:00+00:00',
  'updated_at': '2026-11-07T08:00:00+00:00',
};

http.Response _res(Map<String, dynamic> row) =>
    http.Response(jsonEncode({'success': true, 'data': row}), 200);

ConsultationRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => ConsultationRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _ready(role: role),
  sessionNavigationGuard: guard,
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
    'completed draft doctor finalizes once and sees verified status',
    (tester) async {
      var posts = 0;
      final client = MockClient((req) async {
        if (req.method == 'GET') {
          return _res(_row(status: 'completed'));
        }
        posts++;
        expect(req.url.path, '/api/v1/consultations/11/finalize');
        return _res(_row(status: 'completed', recordStatus: 'final'));
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      tester.view.devicePixelRatio = 1.0;
      tester.view.physicalSize = const Size(1200, 3000);
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.view.resetPhysicalSize);
      await tester.pumpWidget(
        MaterialApp(
          home: ConsultationClinicalNoteScreen(
            consultationDataSource: _source(client, guard),
            patientId: 7,
            consultationId: 11,
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('p23c-note-finalize')), findsOneWidget);
      await tester.ensureVisible(
        find.byKey(const ValueKey('p23c-note-finalize')),
      );
      await tester.tap(find.byKey(const ValueKey('p23c-note-finalize')));
      await tester.pumpAndSettle();
      expect(posts, 1);
      expect(find.textContaining('finalized v1'), findsOneWidget);
    },
  );

  testWidgets('nurse cannot finalize or amend', (tester) async {
    final client = MockClient((req) async => _res(_row(status: 'completed')));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(1200, 3000);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    await tester.pumpWidget(
      MaterialApp(
        home: ConsultationClinicalNoteScreen(
          consultationDataSource: _source(client, guard, role: 'nurse'),
          patientId: 7,
          consultationId: 11,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23c-note-finalize')), findsNothing);
    expect(find.byKey(const ValueKey('p23c-note-amend')), findsNothing);
    expect(find.byKey(const ValueKey('p23c-note-save')), findsOneWidget);
  });

  testWidgets(
    'uncertain completion disables further writes and no auto retry',
    (tester) async {
      var posts = 0;
      final client = MockClient((req) async {
        if (req.method == 'GET') {
          return _res(_row());
        }
        posts++;
        throw http.ClientException('network interruption');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      tester.view.devicePixelRatio = 1.0;
      tester.view.physicalSize = const Size(1200, 3000);
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.view.resetPhysicalSize);
      await tester.pumpWidget(
        MaterialApp(
          home: ConsultationClinicalNoteScreen(
            consultationDataSource: _source(client, guard),
            patientId: 7,
            consultationId: 11,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const ValueKey('p23c-note-Diagnosis')),
        'Confirmed',
      );
      await tester.ensureVisible(
        find.byKey(const ValueKey('p23c-note-complete')),
      );
      await tester.tap(find.byKey(const ValueKey('p23c-note-complete')));
      await tester.pumpAndSettle();
      expect(posts, 1);
      expect(find.textContaining('Outcome not confirmed'), findsOneWidget);
      final complete = tester.widget<ElevatedButton>(
        find.byKey(const ValueKey('p23c-note-complete')),
      );
      expect(complete.onPressed, isNull);
      final save = tester.widget<ElevatedButton>(
        find.byKey(const ValueKey('p23c-note-save')),
      );
      expect(save.onPressed, isNull);
    },
  );

  testWidgets(
    'amended signed note uses one POST with reason and full document',
    (tester) async {
      var posts = 0;
      final client = MockClient((req) async {
        if (req.method == 'GET') {
          return _res(_row(status: 'completed', recordStatus: 'final'));
        }
        posts++;
        expect(req.url.path, '/api/v1/consultations/11/amend');
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        expect(body['reason'], 'Correction');
        expect(body['expected_version'], 1);
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'consultation': _row(
                status: 'completed',
                recordStatus: 'amended',
                version: 2,
                notes: 'New correction',
              ),
              'amendment': {
                'id': 200,
                'clinic_id': 3,
                'patient_id': 7,
                'source_record_id': 11,
                'previous_version': 1,
                'new_version': 2,
                'reason': 'Correction',
                'amended_by_staff_id': 8,
                'new_content': body['document'],
              },
            },
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      tester.view.devicePixelRatio = 1.0;
      tester.view.physicalSize = const Size(1200, 3000);
      addTearDown(tester.view.resetDevicePixelRatio);
      addTearDown(tester.view.resetPhysicalSize);
      await tester.pumpWidget(
        MaterialApp(
          home: ConsultationClinicalNoteScreen(
            consultationDataSource: _source(client, guard),
            patientId: 7,
            consultationId: 11,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const ValueKey('p23c-note-Notes')),
        'New correction',
      );
      await tester.enterText(
        find.byKey(const ValueKey('p23c-note-reason')),
        'Correction',
      );
      await tester.ensureVisible(find.byKey(const ValueKey('p23c-note-amend')));
      await tester.tap(find.byKey(const ValueKey('p23c-note-amend')));
      await tester.pumpAndSettle();
      expect(posts, 1);
      expect(find.textContaining('amended v2'), findsOneWidget);
    },
  );

  testWidgets('amendment history is read-only and scoped', (tester) async {
    final client = MockClient((req) async {
      if (req.url.path.endsWith('/amendments')) {
        return http.Response(
          jsonEncode({
            'success': true,
            'data': [
              {
                'id': 200,
                'clinic_id': 3,
                'patient_id': 7,
                'source_record_id': 11,
                'previous_version': 1,
                'new_version': 2,
                'reason': 'Correction',
                'amended_by_staff_id': 8,
                'new_content': <String, dynamic>{},
              },
            ],
          }),
          200,
        );
      }
      return _res(_row(status: 'completed', recordStatus: 'final'));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(1200, 3000);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    await tester.pumpWidget(
      MaterialApp(
        home: ConsultationClinicalNoteScreen(
          consultationDataSource: _source(client, guard),
          patientId: 7,
          consultationId: 11,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const ValueKey('p23c-note-history')));
    await tester.tap(find.byKey(const ValueKey('p23c-note-history')));
    await tester.pumpAndSettle();
    expect(find.text('Correction'), findsOneWidget);
    expect(find.textContaining('Version 1'), findsOneWidget);
  });

  testWidgets('logout clears clinical text and removes write actions', (
    tester,
  ) async {
    final client = MockClient(
      (req) async => _res(_row(notes: 'Private clinical note')),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(1200, 3000);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    await tester.pumpWidget(
      MaterialApp(
        home: ConsultationClinicalNoteScreen(
          consultationDataSource: _source(client, guard),
          patientId: 7,
          consultationId: 11,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<TextField>(find.byKey(const ValueKey('p23c-note-Notes')))
          .controller!
          .text,
      'Private clinical note',
    );
    guard.markSignedOut();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23c-note-Notes')), findsNothing);
    expect(find.byKey(const ValueKey('p23c-note-unavailable')), findsOneWidget);
  });

  testWidgets('cancelled consultation is view-only', (tester) async {
    final client = MockClient((req) async => _res(_row(status: 'cancelled')));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    tester.view.devicePixelRatio = 1.0;
    tester.view.physicalSize = const Size(1200, 3000);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    await tester.pumpWidget(
      MaterialApp(
        home: ConsultationClinicalNoteScreen(
          consultationDataSource: _source(client, guard),
          patientId: 7,
          consultationId: 11,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('p23c-note-save')), findsOneWidget);
    expect(
      tester
          .widget<ElevatedButton>(find.byKey(const ValueKey('p23c-note-save')))
          .onPressed,
      isNull,
    );
    expect(find.byKey(const ValueKey('p23c-note-complete')), findsNothing);
    expect(find.byKey(const ValueKey('p23c-note-cancel')), findsNothing);
  });
}
