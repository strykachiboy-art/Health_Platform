import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_clinical_note_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_consultation.dart';

AuthSessionState _ready({
  int clinic = 3,
  String role = 'doctor',
  String token = 'a',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'r',
    userId: 42,
    role: role,
    clinicContextId: clinic,
  ),
);

Map<String, dynamic> _row({
  int clinic = 3,
  int patient = 7,
  int id = 11,
  String status = 'in_progress',
  String recordStatus = 'draft',
  int version = 1,
  String updated = '2026-11-07T08:00:00+00:00',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'staff_id': 8,
  'appointment_id': 22,
  'consultation_type': 'general',
  'status': status,
  'record_status': recordStatus,
  'record_version': version,
  'finalized_by_staff_id': recordStatus == 'draft' ? null : 8,
  'finalized_at': recordStatus == 'draft' ? null : '2026-11-07T09:00:00+00:00',
  'latest_amendment_id': recordStatus == 'amended' ? 200 : null,
  'icd10_code': null,
  'chief_complaint': 'Fever',
  'symptoms': null,
  'diagnosis': status == 'completed' ? 'Confirmed' : null,
  'treatment_plan': null,
  'notes': null,
  'voice_note_url': null,
  'transcribed_text': null,
  'template_id': null,
  'started_at': '2026-11-07T08:00:00',
  'ended_at': status == 'in_progress' ? null : '2026-11-07T08:30:00+00:00',
  'created_at': '2026-11-07T08:00:00+00:00',
  'updated_at': updated,
};

PatientConsultation _record(Map<String, dynamic> json) =>
    PatientConsultation.fromJson(
      json,
      expectedPatientId: 7,
      expectedClinicId: 3,
    );

ConsultationClinicalNoteDataSource _notes(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? session,
}) => ConsultationClinicalNoteDataSource(
  consultations: ConsultationRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: session ?? () async => _ready(),
    sessionNavigationGuard: guard,
  ),
);

http.Response _reply(Map<String, dynamic> row) =>
    http.Response(jsonEncode({'success': true, 'data': row}), 200);

Map<String, dynamic> _amendment({
  int id = 200,
  int clinic = 3,
  int patient = 7,
  int source = 11,
  int previous = 1,
  int next = 2,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'source_record_id': source,
  'previous_version': previous,
  'new_version': next,
  'amended_by_staff_id': 8,
  'reason': 'Correction',
  'new_content': <String, dynamic>{},
};

void main() {
  for (final role in <String>['nurse', 'admin', 'receptionist', 'patient']) {
    test('$role cannot doctor-finalize, zero HTTP', () async {
      var httpCalls = 0;
      final client = MockClient((_) async {
        httpCalls++;
        return _reply(_row());
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final notes = _notes(
        client,
        guard,
        session: () async => _ready(role: role),
      );
      await expectLater(
        notes.finalize(_record(_row(status: 'completed'))),
        throwsA(isA<ConsultationNoteSessionException>()),
      );
      expect(httpCalls, 0);
    });
  }

  for (final row in <Map<String, dynamic>>[
    _row(version: 1, updated: '2026-11-07T10:00:00+00:00'),
    _row(status: 'completed'),
    _row(status: 'cancelled'),
    _row(clinic: 3, patient: 7, id: 22),
  ]) {
    test(
      'stale server record blocks PATCH, zero writes: ${row['id']} ${row['status']} ${row['updated_at']}',
      () async {
        var writes = 0;
        final client = MockClient((request) async {
          if (request.method == 'GET') return _reply(row);
          writes++;
          return _reply(_row());
        });
        addTearDown(client.close);
        final guard = SessionNavigationGuard()..markSessionAvailable();
        addTearDown(guard.dispose);
        final notes = _notes(client, guard);
        await expectLater(
          notes.saveDraft(_record(_row()), {'notes': 'Changed'}),
          throwsA(
            anyOf(
              isA<ConsultationNoteStaleException>(),
              isA<FormatException>(),
            ),
          ),
        );
        expect(writes, 0);
      },
    );
  }

  test('clinic switch during read blocks subsequent clinical write', () async {
    var clinic = 3;
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        clinic = 9;
        return _reply(_row());
      }
      writes++;
      return _reply(_row());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final notes = _notes(
      client,
      guard,
      session: () async => _ready(clinic: clinic),
    );
    await expectLater(
      notes.saveDraft(_record(_row()), {'notes': 'Changed'}),
      throwsA(
        anyOf(
          isA<ConsultationNoteSessionException>(),
          isA<ConsultationSessionMismatchException>(),
        ),
      ),
    );
    expect(writes, 0);
  });

  test('token rotation without navigation revision blocks POST', () async {
    var token = 'a';
    var writes = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        token = 'b';
        return _reply(_row());
      }
      writes++;
      return _reply(_row());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final notes = _notes(
      client,
      guard,
      session: () async => _ready(token: token),
    );
    await expectLater(
      notes.complete(_record(_row()), diagnosis: 'Confirmed'),
      throwsA(
        anyOf(
          isA<ConsultationNoteSessionException>(),
          isA<ConsultationSessionMismatchException>(),
        ),
      ),
    );
    expect(writes, 0);
  });

  test('signed out guard blocks clinical write before HTTP', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return _reply(_row());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    await expectLater(
      _notes(client, guard).complete(_record(_row()), diagnosis: 'Confirmed'),
      throwsA(isA<ConsultationNoteSessionException>()),
    );
    expect(calls, 0);
  });

  test('cross-clinic record cannot dispatch clinical write', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return _reply(_row());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _notes(
        client,
        guard,
        session: () async => _ready(clinic: 9),
      ).complete(_record(_row()), diagnosis: 'Confirmed'),
      throwsA(isA<ConsultationNoteSessionException>()),
    );
    expect(calls, 0);
  });

  for (final row in <Map<String, dynamic>>[
    _amendment(clinic: 9),
    _amendment(patient: 22),
    _amendment(source: 99),
    _amendment(previous: 2, next: 5),
  ]) {
    test(
      'amendment history rejects wrong scope/version ${row['clinic_id']} ${row['patient_id']} ${row['source_record_id']} ${row['previous_version']} ${row['new_version']}',
      () async {
        final client = MockClient(
          (_) async => http.Response(
            jsonEncode({
              'success': true,
              'data': [row],
            }),
            200,
          ),
        );
        addTearDown(client.close);
        final guard = SessionNavigationGuard()..markSessionAvailable();
        addTearDown(guard.dispose);
        await expectLater(
          _notes(client, guard).loadAmendments(_record(_row())),
          throwsA(isA<FormatException>()),
        );
      },
    );
  }

  test(
    'duplicate amendment ids fail closed without presenting ambiguous timeline',
    () async {
      final client = MockClient(
        (_) async => http.Response(
          jsonEncode({
            'success': true,
            'data': [_amendment(), _amendment()],
          }),
          200,
        ),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _notes(client, guard).loadAmendments(_record(_row())),
        throwsA(isA<FormatException>()),
      );
    },
  );

  test('signed out during pending amendment read discards response', () async {
    final pending = Completer<http.Response>();
    final client = MockClient((_) => pending.future);
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final future = _notes(client, guard).loadAmendments(_record(_row()));
    await Future<void>.delayed(Duration.zero);
    guard.markSignedOut();
    pending.complete(
      http.Response(
        jsonEncode({
          'success': true,
          'data': [_amendment()],
        }),
        200,
      ),
    );
    await expectLater(future, throwsA(isA<ConsultationNoteSessionException>()));
  });
}
