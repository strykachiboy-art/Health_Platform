import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/nursing/data/models/nursing_note_workflow.dart';
import 'package:clinic_system_pro/features/nursing/data/nursing_note_workflow_remote_data_source.dart';

AuthSessionState _ready({String role = 'nurse', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'note-token',
        refreshToken: 'refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> _admission() => {
  'id': 11,
  'patient_id': 7,
  'bed_id': 9,
  'admitted_by_id': 8,
  'status': 'admitted',
};

Map<String, dynamic> _note({
  int id = 20,
  String status = 'draft',
  String recordStatus = 'draft',
  String kind = 'progress',
  int version = 1,
  int recordVersion = 1,
  String text = 'Old nursing note',
  int clinic = 3,
}) => {
  'id': id,
  'clinic_id': clinic,
  'patient_id': 7,
  'admission_id': 11,
  'author_staff_id': 8,
  'note_type': kind,
  'status': status,
  'record_status': recordStatus,
  'record_version': recordVersion,
  'version': version,
  'note_text': text,
  'observed_at': '2026-11-08T10:00:00+00:00',
  'signed_at': status == 'signed' ? '2026-11-08T10:30:00+00:00' : null,
  'latest_amendment_id': recordStatus == 'amended' ? 55 : null,
};

http.Response _json(Object json, [int status = 200]) =>
    http.Response(jsonEncode(json), status);

NursingNoteWorkflowRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'nurse',
  int clinic = 3,
}) => NursingNoteWorkflowRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _ready(role: role, clinic: clinic),
  sessionNavigationGuard: guard,
);

http.Response _read(
  http.Request req, {
  Map<String, dynamic>? listed,
  Map<String, dynamic>? detail,
  bool noAdmission = false,
}) {
  if (req.url.path.endsWith('/current-admission')) {
    return noAdmission
        ? _json({'message': 'No active admission found'}, 404)
        : _json(_admission());
  }
  if (req.url.path.endsWith('/admissions/11/notes')) {
    return _json({
      'items': listed == null ? <Object>[] : [listed],
      'total': listed == null ? 0 : 1,
    });
  }
  if (req.url.path.endsWith('/notes/20') ||
      req.url.path.endsWith('/notes/21')) {
    return _json(detail ?? listed ?? _note());
  }
  if (req.url.path.endsWith('/notes/20/amendments')) {
    return _json({'items': <Object>[], 'total': 0});
  }
  throw StateError('Unexpected nursing note GET: ${req.url.path}');
}

void main() {
  test('GET-only review is admission-scoped and makes no writes', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      expect(req.method, 'GET');
      expect(req.headers['authorization'], 'Bearer note-token');
      return _read(req, listed: _note());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = _source(client, guard);
    final review = (await s.inspect(patientId: 7))!;
    expect(review.admission.id, 11);
    expect(review.notes.single.id, 20);
    expect(review.mayWrite, isTrue);
    expect(calls, 2);
    expect(s.writeAttempted, isFalse);
  });

  test(
    'exact no-current-admission 404 returns null without notes GET',
    () async {
      var calls = 0;
      final client = MockClient((req) async {
        calls++;
        expect(req.url.path.endsWith('/current-admission'), isTrue);
        return _read(req, noAdmission: true);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      expect(await _source(client, guard).inspect(patientId: 7), isNull);
      expect(calls, 1);
    },
  );

  test('doctor/admin may read but cannot create draft', () async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method != 'GET') {
        writes++;
        return _json({}, 500);
      }
      return _read(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in ['doctor', 'admin']) {
      final s = _source(client, guard, role: role);
      final review = (await s.inspect(patientId: 7))!;
      expect(review.mayWrite, isFalse);
      await expectLater(
        s.createDraft(
          reviewed: review,
          kind: NursingNoteKind.progress,
          text: 'Example',
        ),
        throwsA(isA<NursingNotePreflightChanged>()),
      );
    }
    expect(writes, 0);
  });

  test(
    'create draft POST once, detail GET, permanently locks replay',
    () async {
      var writes = 0;
      final created = _note(id: 21, text: 'Clinical draft');
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          expect(req.url.path, '/api/v1/nursing/admissions/11/notes');
          expect(req.headers.containsKey('idempotency-key'), isFalse);
          expect(jsonDecode(req.body), {
            'note_text': 'Clinical draft',
            'note_type': 'progress',
          });
          return _json({'note': created}, 201);
        }
        return _read(req, detail: created);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final s = _source(client, guard);
      final review = (await s.inspect(patientId: 7))!;
      final result = await s.createDraft(
        reviewed: review,
        kind: NursingNoteKind.progress,
        text: 'Clinical draft',
      );
      expect(result.id, 21);
      expect(result.mayEditDraft, isTrue);
      expect(writes, 1);
      expect(s.writeAttempted, isTrue);
      await expectLater(
        s.createDraft(
          reviewed: review,
          kind: NursingNoteKind.progress,
          text: 'Again',
        ),
        throwsA(isA<NursingNoteOutcomeUnconfirmed>()),
      );
      expect(writes, 1);
    },
  );

  test('draft PATCH updates only once and confirms server version', () async {
    var writes = 0;
    final updated = _note(text: 'Revised text', version: 2);
    final client = MockClient((req) async {
      if (req.method == 'PATCH') {
        writes++;
        expect(req.url.path, '/api/v1/nursing/notes/20');
        expect(req.headers.containsKey('idempotency-key'), isFalse);
        expect(jsonDecode(req.body), {
          'note_text': 'Revised text',
          'note_type': 'progress',
        });
        return _json({'note': updated});
      }
      return _read(req, listed: _note(), detail: updated);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = _source(client, guard);
    final review = (await s.inspect(patientId: 7))!;
    final revised = await s.editDraft(
      reviewed: review,
      selected: review.notes.single,
      kind: NursingNoteKind.progress,
      text: 'Revised text',
    );
    expect(revised.version, 2);
    expect(writes, 1);
  });

  test(
    'sign POST requires unique idempotency key and confirms final note',
    () async {
      var writes = 0;
      final signed = _note(status: 'signed', recordStatus: 'final', version: 2);
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          expect(req.url.path, '/api/v1/nursing/notes/20/sign');
          expect(req.headers['idempotency-key'], startsWith('p23ee-'));
          expect(jsonDecode(req.body), <String, dynamic>{});
          return _json({'note': signed});
        }
        return _read(req, listed: _note(), detail: signed);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final s = _source(client, guard);
      final review = (await s.inspect(patientId: 7))!;
      final result = await s.signDraft(
        reviewed: review,
        selected: review.notes.single,
      );
      expect(result.status, NursingNoteLife.signed);
      expect(result.recordStatus, NursingRecordLife.finalRecord);
      expect(writes, 1);
    },
  );

  test(
    'amend POST supplies expected_version and entire replacement document',
    () async {
      var writes = 0;
      final prior = _note(status: 'signed', recordStatus: 'final');
      final changed = _note(
        status: 'signed',
        recordStatus: 'amended',
        recordVersion: 2,
        text: 'Corrected documentation',
      );
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          expect(req.url.path, '/api/v1/nursing/notes/20/amend');
          expect(req.headers.containsKey('idempotency-key'), isFalse);
          final body = jsonDecode(req.body) as Map<String, dynamic>;
          expect(body['expected_version'], 1);
          expect(body['reason'], 'Correct factual error');
          final doc = body['document'] as Map<String, dynamic>;
          expect(doc['note_type'], 'progress');
          expect(doc['note_text'], 'Corrected documentation');
          expect(doc['observed_at'], isA<String>());
          return _json({'note': changed, 'amendment': {}});
        }
        return _read(req, listed: prior, detail: changed);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final s = _source(client, guard);
      final review = (await s.inspect(patientId: 7))!;
      final amended = await s.amendSigned(
        reviewed: review,
        selected: review.notes.single,
        reason: 'Correct factual error',
        replacementText: 'Corrected documentation',
      );
      expect(amended.recordVersion, 2);
      expect(amended.recordStatus, NursingRecordLife.amended);
      expect(writes, 1);
    },
  );

  test(
    'lost POST response produces unknown outcome, no automatic replay',
    () async {
      var writes = 0;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          throw http.ClientException('response lost');
        }
        return _read(req);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final s = _source(client, guard);
      final review = (await s.inspect(patientId: 7))!;
      await expectLater(
        s.createDraft(
          reviewed: review,
          kind: NursingNoteKind.progress,
          text: 'A',
        ),
        throwsA(isA<NursingNoteOutcomeUnconfirmed>()),
      );
      await expectLater(
        s.createDraft(
          reviewed: review,
          kind: NursingNoteKind.progress,
          text: 'B',
        ),
        throwsA(isA<NursingNoteOutcomeUnconfirmed>()),
      );
      expect(writes, 1);
    },
  );

  test('stale note list version fails preflight before any mutation', () async {
    var reads = 0;
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        return _json({}, 500);
      }
      if (req.url.path.endsWith('/admissions/11/notes')) {
        reads++;
        return _json({
          'items': [_note(version: reads == 1 ? 1 : 2)],
          'total': 1,
        });
      }
      return _read(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = _source(client, guard);
    final review = (await s.inspect(patientId: 7))!;
    await expectLater(
      s.signDraft(reviewed: review, selected: review.notes.single),
      throwsA(isA<NursingNotePreflightChanged>()),
    );
    expect(writes, 0);
    expect(s.writeAttempted, isFalse);
  });

  test('wrong-clinic note is rejected and no write is attempted', () async {
    final client = MockClient((req) async {
      return _read(req, listed: _note(clinic: 4));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = _source(client, guard);
    await expectLater(s.inspect(patientId: 7), throwsFormatException);
    expect(s.writeAttempted, isFalse);
  });

  test('guard invalidation prevents subsequent nursing note reads', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      return _read(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = _source(client, guard);
    guard.markSignedOut();
    await expectLater(
      s.inspect(patientId: 7),
      throwsA(isA<NursingNoteSessionUnavailable>()),
    );
    expect(calls, 0);
  });
}
