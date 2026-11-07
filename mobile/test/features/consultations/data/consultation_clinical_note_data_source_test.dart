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
  String role = 'doctor',
  int clinic = 3,
  String token = 'clinical-test',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'refresh',
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
  String? diagnosis,
  String? notes,
  String? updated = '2026-11-07T08:00:00+00:00',
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'staff_id': 8,
  'appointment_id': null,
  'consultation_type': 'general',
  'status': status,
  'record_status': recordStatus,
  'record_version': version,
  'finalized_by_staff_id': recordStatus == 'draft' ? null : 8,
  'finalized_at': recordStatus == 'draft' ? null : '2026-11-07T08:30:00+00:00',
  'latest_amendment_id': recordStatus == 'amended' ? 200 : null,
  'icd10_code': null,
  'chief_complaint': 'Fever',
  'symptoms': null,
  'diagnosis': diagnosis,
  'treatment_plan': null,
  'notes': notes,
  'voice_note_url': null,
  'transcribed_text': null,
  'template_id': null,
  'started_at': '2026-11-07T08:00:00',
  'ended_at': status == 'in_progress' ? null : '2026-11-07T08:20:00+00:00',
  'created_at': '2026-11-07T08:00:00+00:00',
  'updated_at': updated,
};

PatientConsultation _record([Map<String, dynamic>? row]) =>
    PatientConsultation.fromJson(
      row ?? _row(),
      expectedPatientId: 7,
      expectedClinicId: 3,
    );

http.Response _response(Map<String, dynamic> row) => http.Response(
  jsonEncode(<String, dynamic>{'success': true, 'data': row}),
  200,
);

ConsultationClinicalNoteDataSource _source(
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

Map<String, String?> _document({String? notes = 'Corrected note'}) =>
    <String, String?>{
      'icd10_code': null,
      'chief_complaint': 'Fever',
      'symptoms': null,
      'diagnosis': 'Confirmed',
      'treatment_plan': null,
      'notes': notes,
      'voice_note_url': null,
      'transcribed_text': null,
    };

Map<String, dynamic> _amendment() => <String, dynamic>{
  'id': 200,
  'clinic_id': 3,
  'patient_id': 7,
  'source_record_type': 'consultation_note',
  'source_record_id': 11,
  'previous_version': 1,
  'new_version': 2,
  'previous_amendment_id': null,
  'amended_by_staff_id': 8,
  'reason': 'Correction',
  'new_content': _document(),
  'new_content_sha256':
      '0000000000000000000000000000000000000000000000000000000000000000',
  'amended_at': '2026-11-07T09:00:00+00:00',
  'created_at': '2026-11-07T09:00:00+00:00',
};

void main() {
  test(
    'draft PATCH sends changed text only, no retry or idempotency header',
    () async {
      var getCount = 0;
      var writeCount = 0;
      final client = MockClient((req) async {
        if (req.method == 'GET') {
          getCount++;
          return _response(_row());
        }
        writeCount++;
        expect(req.method, 'PATCH');
        expect(req.url.path, '/api/v1/consultations/11');
        expect(req.headers.containsKey('idempotency-key'), isFalse);
        expect(req.headers['authorization'], 'Bearer clinical-test');
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        expect(body, {'notes': 'Assessment updated'});
        return _response(_row(notes: 'Assessment updated'));
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final record = await _source(
        client,
        guard,
      ).saveDraft(_record(), {'notes': 'Assessment updated'});
      expect(record.notes, 'Assessment updated');
      expect(getCount, 1);
      expect(writeCount, 1);
    },
  );

  test('complete submits diagnosis once with exact POST contract', () async {
    var posts = 0;
    final client = MockClient((req) async {
      if (req.method == 'GET') {
        return _response(_row());
      }
      posts++;
      expect(req.url.path, '/api/v1/consultations/11/complete');
      expect(req.method, 'POST');
      expect(jsonDecode(req.body), {'diagnosis': 'Confirmed'});
      expect(req.headers.containsKey('idempotency-key'), isFalse);
      return _response(_row(status: 'completed', diagnosis: 'Confirmed'));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await _source(
      client,
      guard,
    ).complete(_record(), diagnosis: 'Confirmed');
    expect(result.status, PatientConsultationStatus.completed);
    expect(posts, 1);
  });

  test('cancel transmits optional reason once', () async {
    final client = MockClient((req) async {
      if (req.method == 'GET') {
        return _response(_row());
      }
      expect(req.url.path, '/api/v1/consultations/11/cancel');
      expect(jsonDecode(req.body), {'reason': 'Entered in error'});
      return _response(_row(status: 'cancelled'));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final result = await _source(
      client,
      guard,
    ).cancel(_record(), reason: 'Entered in error');
    expect(result.status, PatientConsultationStatus.cancelled);
  });

  test(
    'doctor finalizes only a completed draft with expected version',
    () async {
      final original = _row(status: 'completed', diagnosis: 'Confirmed');
      final client = MockClient((req) async {
        if (req.method == 'GET') {
          return _response(original);
        }
        expect(req.url.path, '/api/v1/consultations/11/finalize');
        expect(jsonDecode(req.body), {'expected_version': 1});
        return _response(
          _row(
            status: 'completed',
            recordStatus: 'final',
            diagnosis: 'Confirmed',
          ),
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final signed = await _source(client, guard).finalize(_record(original));
      expect(signed.recordStatus, PatientClinicalRecordStatus.finalized);
    },
  );

  test(
    'doctor amendment submits full document and validates returned chain',
    () async {
      final prior = _row(
        status: 'completed',
        recordStatus: 'final',
        diagnosis: 'Confirmed',
      );
      final client = MockClient((req) async {
        if (req.method == 'GET') {
          return _response(prior);
        }
        expect(req.url.path, '/api/v1/consultations/11/amend');
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        expect(body['expected_version'], 1);
        expect(body['reason'], 'Correction');
        expect((body['document'] as Map).length, 8);
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'consultation': _row(
                status: 'completed',
                recordStatus: 'amended',
                version: 2,
                diagnosis: 'Confirmed',
                notes: 'Corrected note',
              ),
              'amendment': _amendment(),
            },
          }),
          200,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final amended = await _source(
        client,
        guard,
      ).amend(_record(prior), reason: 'Correction', document: _document());
      expect(amended.recordVersion, 2);
      expect(amended.latestAmendmentId, 200);
    },
  );

  test('amendments GET enforces the active patient and clinic', () async {
    final client = MockClient((req) async {
      expect(req.method, 'GET');
      expect(req.url.path, '/api/v1/consultations/11/amendments');
      return http.Response(
        jsonEncode({
          'success': true,
          'data': [_amendment()],
        }),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final list = await _source(client, guard).loadAmendments(_record());
    expect(list.length, 1);
    expect(list.single.newVersion, 2);
    expect(list.single.reason, 'Correction');
  });

  test('clinic mismatch in amendment history fails closed', () async {
    final client = MockClient(
      (req) async => http.Response(
        jsonEncode({
          'success': true,
          'data': [_amendment()..['clinic_id'] = 99],
        }),
        200,
      ),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).loadAmendments(_record()),
      throwsA(isA<FormatException>()),
    );
  });

  test('nurse cannot sign or amend; no HTTP is dispatched', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      return _response(_row());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(
      client,
      guard,
      session: () async => _ready(role: 'nurse'),
    );
    final signed = _record(_row(status: 'completed', diagnosis: 'Confirmed'));
    await expectLater(
      source.finalize(signed),
      throwsA(isA<ConsultationNoteSessionException>()),
    );
    final finalRecord = _record(
      _row(status: 'completed', recordStatus: 'final', diagnosis: 'Confirmed'),
    );
    await expectLater(
      source.amend(finalRecord, reason: 'Correction', document: _document()),
      throwsA(isA<ConsultationNoteSessionException>()),
    );
    expect(calls, 0);
  });

  test('receptionist cannot write notes or view amendment history', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      return _response(_row());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(
      client,
      guard,
      session: () async => _ready(role: 'receptionist'),
    );
    await expectLater(
      source.complete(_record(), diagnosis: 'Confirmed'),
      throwsA(isA<ConsultationNoteSessionException>()),
    );
    await expectLater(
      source.loadAmendments(_record()),
      throwsA(isA<ConsultationNoteSessionException>()),
    );
    expect(calls, 0);
  });

  test('missing clinic blocks before GET or mutation', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      return _response(_row());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(
        client,
        guard,
        session: () async => _ready(clinic: 0),
      ).cancel(_record()),
      throwsA(isA<ConsultationNoteSessionException>()),
    );
    expect(calls, 0);
  });

  test('stale record version blocks mutation after read-back', () async {
    var writes = 0;
    final old = _record();
    final client = MockClient((req) async {
      if (req.method == 'GET') {
        return _response(
          _row(
            status: 'completed',
            recordStatus: 'final',
            diagnosis: 'Confirmed',
          ),
        );
      }
      writes++;
      return _response(_row());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).saveDraft(old, {'notes': 'Change'}),
      throwsA(isA<ConsultationNoteStaleException>()),
    );
    expect(writes, 0);
  });

  test('changed clinic mid-read cancels write before POST', () async {
    var calls = 0;
    var reads = 0;
    final client = MockClient((req) async {
      calls++;
      if (req.method == 'GET') {
        reads++;
        return _response(_row());
      }
      return _response(_row(status: 'completed', diagnosis: 'Confirmed'));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var sessions = 0;
    final source = _source(
      client,
      guard,
      session: () async {
        sessions++;
        return _ready(clinic: sessions <= 2 ? 3 : 4);
      },
    );
    await expectLater(
      source.complete(_record(), diagnosis: 'Confirmed'),
      throwsA(
        anyOf(
          isA<ConsultationNoteSessionException>(),
          isA<ConsultationSessionMismatchException>(),
        ),
      ),
    );
    expect(reads, 1);
    expect(calls, 1);
  });

  test('signed out before write dispatch issues no HTTP', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      return _response(_row());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).cancel(_record()),
      throwsA(isA<ConsultationNoteSessionException>()),
    );
    expect(calls, 0);
  });

  test('logout while POST pending rejects success after transport', () async {
    final pending = Completer<http.Response>();
    final sent = Completer<void>();
    var writes = 0;
    final client = MockClient((req) {
      if (req.method == 'GET') {
        return Future<http.Response>.value(_response(_row()));
      }
      writes++;
      sent.complete();
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final task = _source(
      client,
      guard,
    ).complete(_record(), diagnosis: 'Confirmed');
    await sent.future.timeout(const Duration(seconds: 5));
    guard.markSignedOut();
    pending.complete(
      _response(_row(status: 'completed', diagnosis: 'Confirmed')),
    );
    await expectLater(task, throwsA(isA<ConsultationNoteSessionException>()));
    expect(writes, 1);
  });

  test('backend wrong patient or consultation identity fails closed', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final row in <Map<String, dynamic>>[_row(patient: 9), _row(id: 19)]) {
      final client = MockClient((req) async {
        if (req.method == 'GET') {
          return _response(_row());
        }
        return _response(row);
      });
      await expectLater(
        _source(client, guard).complete(_record(), diagnosis: 'Confirmed'),
        throwsA(isA<FormatException>()),
      );
      client.close();
    }
  });

  test(
    'invalid note fields and partial amendment document dispatch nothing',
    () {
      var calls = 0;
      final client = MockClient((req) async {
        calls++;
        return _response(_row());
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      expect(
        () => source.complete(_record(), diagnosis: '  '),
        throwsArgumentError,
      );
      expect(
        () => source.saveDraft(_record(), {'invalid': 'x'}),
        throwsArgumentError,
      );
      expect(
        () => source.saveDraft(_record(), <String, String>{}),
        throwsArgumentError,
      );
      expect(
        () => source.amend(
          _record(),
          reason: 'Correction',
          document: {'notes': 'Correction'},
        ),
        throwsArgumentError,
      );
      expect(calls, 0);
    },
  );
}
