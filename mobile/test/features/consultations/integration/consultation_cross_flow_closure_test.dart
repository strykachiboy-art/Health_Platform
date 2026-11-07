import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_clinical_note_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_consultation.dart';

AuthSessionState _ready({int clinic = 3, String role = 'doctor'}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'cross-flow-token',
        refreshToken: 'refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> _row({
  String status = 'in_progress',
  String recordStatus = 'draft',
  int version = 1,
  int clinic = 3,
  int patient = 7,
  int appointment = 22,
  int staff = 8,
  int id = 11,
  String? diagnosis,
  String? notes,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'staff_id': staff,
  'appointment_id': appointment,
  'icd10_code': null,
  'consultation_type': 'general',
  'status': status,
  'record_status': recordStatus,
  'record_version': version,
  'finalized_by_staff_id': recordStatus == 'draft' ? null : 8,
  'finalized_at': recordStatus == 'draft' ? null : '2026-11-07T09:00:00+00:00',
  'latest_amendment_id': recordStatus == 'amended' ? 200 : null,
  'chief_complaint': 'Fever',
  'symptoms': null,
  'diagnosis': diagnosis,
  'treatment_plan': null,
  'notes': notes,
  'voice_note_url': null,
  'transcribed_text': null,
  'template_id': null,
  'started_at': '2026-11-07T08:00:00',
  'ended_at': status == 'in_progress' ? null : '2026-11-07T08:30:00+00:00',
  'created_at': '2026-11-07T08:00:00+00:00',
  'updated_at': '2026-11-07T08:00:00+00:00',
};

http.Response _reply(Map<String, dynamic> row, {int status = 200}) =>
    http.Response(jsonEncode({'success': true, 'data': row}), status);

ConsultationRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? session,
}) => ConsultationRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: session ?? () async => _ready(),
  sessionNavigationGuard: guard,
);

PatientConsultation _record(Map<String, dynamic> row) =>
    PatientConsultation.fromJson(
      row,
      expectedPatientId: 7,
      expectedClinicId: 3,
    );

Map<String, String?> _document() => <String, String?>{
  'icd10_code': null,
  'chief_complaint': 'Fever',
  'symptoms': null,
  'diagnosis': 'Confirmed diagnosis',
  'treatment_plan': null,
  'notes': 'Corrected note',
  'voice_note_url': null,
  'transcribed_text': null,
};

Map<String, dynamic> _amendment() => <String, dynamic>{
  'id': 200,
  'clinic_id': 3,
  'patient_id': 7,
  'source_record_id': 11,
  'previous_version': 1,
  'new_version': 2,
  'amended_by_staff_id': 8,
  'reason': 'Correction',
  'new_content': _document(),
};

void main() {
  test('appointment-linked start to signed amended history is one ordered online chain', () async {
    final events = <String>[];
    var live = _row();
    final client = MockClient((request) async {
      final path = request.url.path;
      expect(request.headers['authorization'], 'Bearer cross-flow-token');
      if (request.method == 'GET' && path == '/api/v1/consultations/11') {
        events.add('GET');
        return _reply(live);
      }
      if (request.method == 'GET' && path.endsWith('/amendments')) {
        events.add('HISTORY');
        return http.Response(
          jsonEncode({
            'success': true,
            'data': [_amendment()],
          }),
          200,
        );
      }
      if (request.method == 'POST' && path == '/api/v1/consultations/') {
        events.add('START');
        expect(request.headers['idempotency-key'], 'p23c-cross-flow-22');
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['patient_id'], 7);
        expect(body['staff_id'], 8);
        expect(body['appointment_id'], 22);
        expect(body.containsKey('clinic_id'), false);
        return _reply(live, status: 201);
      }
      expect(request.headers.containsKey('idempotency-key'), false);
      if (request.method == 'PATCH' && path == '/api/v1/consultations/11') {
        events.add('PATCH');
        expect(jsonDecode(request.body), {'notes': 'Assessment'});
        live = _row(notes: 'Assessment');
        return _reply(live);
      }
      if (request.method == 'POST' && path.endsWith('/complete')) {
        events.add('COMPLETE');
        expect(jsonDecode(request.body), {'diagnosis': 'Confirmed diagnosis'});
        live = _row(
          status: 'completed',
          notes: 'Assessment',
          diagnosis: 'Confirmed diagnosis',
        );
        return _reply(live);
      }
      if (request.method == 'POST' && path.endsWith('/finalize')) {
        events.add('FINALIZE');
        expect(jsonDecode(request.body), {'expected_version': 1});
        live = _row(
          status: 'completed',
          recordStatus: 'final',
          notes: 'Assessment',
          diagnosis: 'Confirmed diagnosis',
        );
        return _reply(live);
      }
      if (request.method == 'POST' && path.endsWith('/amend')) {
        events.add('AMEND');
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['expected_version'], 1);
        expect(body['reason'], 'Correction');
        expect(body['document'], _document());
        live = _row(
          status: 'completed',
          recordStatus: 'amended',
          version: 2,
          notes: 'Corrected note',
          diagnosis: 'Confirmed diagnosis',
        );
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {'consultation': live, 'amendment': _amendment()},
          }),
          200,
        );
      }
      throw StateError(
        'Unexpected consultation request: ${request.method} $path',
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final consultations = _source(client, guard);
    final notes = ConsultationClinicalNoteDataSource(
      consultations: consultations,
    );
    final created = await consultations.start(
      const ConsultationStartCommand(
        patientId: 7,
        staffId: 8,
        appointmentId: 22,
        idempotencyKey: 'p23c-cross-flow-22',
      ),
    );
    expect(created.appointmentId, 22);
    final detail = await consultations.getForPatient(
      patientId: 7,
      consultationId: created.id,
    );
    expect(detail.id, created.id);
    final draft = await notes.saveDraft(detail, {'notes': 'Assessment'});
    final completed = await notes.complete(
      draft,
      diagnosis: 'Confirmed diagnosis',
    );
    final signed = await notes.finalize(completed);
    expect(signed.recordStatus, PatientClinicalRecordStatus.finalized);
    final amended = await notes.amend(
      signed,
      reason: 'Correction',
      document: _document(),
    );
    expect(amended.recordVersion, 2);
    final history = await notes.loadAmendments(amended);
    expect(history.single.newVersion, 2);
    expect(events, [
      'START',
      'GET',
      'GET',
      'PATCH',
      'GET',
      'COMPLETE',
      'GET',
      'FINALIZE',
      'GET',
      'AMEND',
      'HISTORY',
    ]);
  });

  test(
    'wrong appointment ID after start fails closed before next clinical step',
    () async {
      var posts = 0;
      final client = MockClient((request) async {
        posts++;
        return _reply(_row(appointment: 99), status: 201);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(client, guard).start(
          const ConsultationStartCommand(
            patientId: 7,
            staffId: 8,
            appointmentId: 22,
            idempotencyKey: 'p23c-appointment-mismatch',
          ),
        ),
        throwsA(isA<FormatException>()),
      );
      expect(posts, 1);
    },
  );

  test(
    'wrong staff response on start fails closed before note writer',
    () async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return _reply(_row(staff: 999), status: 201);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(client, guard).start(
          const ConsultationStartCommand(
            patientId: 7,
            staffId: 8,
            idempotencyKey: 'p23c-staff-mismatch',
          ),
        ),
        throwsA(isA<FormatException>()),
      );
      expect(requests, 1);
    },
  );

  test(
    'offline-like write failure performs exactly one dispatch with no replay',
    () async {
      var writes = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') return _reply(_row());
        writes++;
        throw http.ClientException('connection closed after request');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final notes = ConsultationClinicalNoteDataSource(
        consultations: _source(client, guard),
      );
      await expectLater(
        notes.saveDraft(_record(_row()), {'notes': 'Assessment'}),
        throwsA(isA<ApiException>()),
      );
      expect(writes, 1);
    },
  );
}
