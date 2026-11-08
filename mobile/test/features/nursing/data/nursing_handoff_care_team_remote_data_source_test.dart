import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/nursing/data/models/nursing_handoff_care_team.dart';
import 'package:clinic_system_pro/features/nursing/data/nursing_handoff_care_team_remote_data_source.dart';

AuthSessionState _ready({
  String role = 'nurse',
  int clinic = 3,
  int user = 42,
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'handoff-token',
    refreshToken: 'refresh',
    userId: user,
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

Map<String, dynamic> _assignment({
  int id = 1,
  String kind = 'primary_nurse',
  int staff = 9,
  bool ended = false,
}) => {
  'assignment_id': id,
  'assignment_kind': kind,
  'staff_id': staff,
  'staff_user_id': 48,
  'staff_display_name': 'Assigned clinician',
  'staff_active': true,
  'assigned_by_user_id': 42,
  'assignment_reason': 'Shift coverage',
  'assigned_at': '2026-11-08T08:00:00Z',
  'ended_at': ended ? '2026-11-08T10:00:00Z' : null,
  'ended_by_user_id': ended ? 42 : null,
  'end_reason': ended ? 'Shift ended' : null,
};

Map<String, dynamic> _team({int? nurse = 9, int clinic = 3}) => {
  'clinic_id': clinic,
  'patient_id': 7,
  'admission_id': 11,
  'admission_status': 'admitted',
  'admission_active': true,
  'authoritative_assignment_available': true,
  'responsibility': {
    'primary_doctor_staff_id': null,
    'primary_nurse_staff_id': nurse,
    'primary_doctor': null,
    'primary_nurse': nurse == null
        ? null
        : _assignment(id: nurse == 9 ? 1 : nurse, staff: nurse),
    'primary_doctor_confirmed': false,
    'primary_nurse_confirmed': nurse != null,
    'confirmed': false,
  },
  'involvement_evidence': <String, dynamic>{},
  'handoff_evidence': <String, dynamic>{},
};

Map<String, dynamic> _handoff({
  String state = 'draft',
  int version = 1,
  int clinic = 3,
  String situation = 'Situation',
}) => {
  'id': 20,
  'clinic_id': clinic,
  'patient_id': 7,
  'admission_id': 11,
  'outgoing_staff_id': 9,
  'incoming_staff_id': 10,
  'status': state,
  'version': version,
  'shift_start_at': '2026-11-08T08:00:00Z',
  'shift_end_at': '2026-11-08T16:00:00Z',
  'situation': situation,
  'background': 'Background',
  'assessment': 'Assessment',
  'recommendation': 'Recommendation',
  'safety_concerns': 'Watch oxygen',
  'pending_actions': 'Follow up labs',
};

http.Response _json(Object body, [int code = 200]) =>
    http.Response(jsonEncode(body), code);

http.Response _get(
  http.Request req, {
  int? nurse = 9,
  bool noAdmission = false,
  String handoffStatus = 'draft',
  int handoffVersion = 1,
  bool includeHandoff = true,
  int clinic = 3,
  String situation = 'Situation',
}) {
  final path = req.url.path;
  if (path.endsWith('/current-admission')) {
    return noAdmission
        ? _json({'message': 'No active admission found'}, 404)
        : _json(_admission());
  }
  if (path.endsWith('/care-team/assignment-history')) {
    final history = nurse == null
        ? <Object>[]
        : <Object>[_assignment(id: nurse == 9 ? 1 : nurse, staff: nurse)];
    return _json({'items': history, 'total': history.length});
  }
  if (path.endsWith('/care-team')) {
    return _json(_team(nurse: nurse, clinic: clinic));
  }
  if (path.endsWith('/admissions/11/handoffs')) {
    final handoffs = includeHandoff
        ? <Object>[
            _handoff(
              state: handoffStatus,
              version: handoffVersion,
              situation: situation,
            ),
          ]
        : <Object>[];
    return _json({'items': handoffs, 'total': handoffs.length});
  }
  if (path.endsWith('/handoffs/20/summary')) {
    return _json({
      'generated_at': '2026-11-08T12:00:00Z',
      'handoff': _handoff(
        state: handoffStatus,
        version: handoffVersion,
        situation: situation,
      ),
      'patient': <String, dynamic>{},
      'admission': <String, dynamic>{},
      'critical_alerts': [
        <String, dynamic>{'id': 1},
      ],
      'pending_lab_orders': <Object>[],
      'due_medication_administrations': <Object>[],
      'tasks': <Object>[],
    });
  }
  if (path.endsWith('/handoffs/20')) {
    return _json(
      _handoff(
        state: handoffStatus,
        version: handoffVersion,
        situation: situation,
      ),
    );
  }
  throw StateError('Unexpected GET: $path');
}

NursingHandoffCareTeamRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'nurse',
  int clinic = 3,
  int user = 42,
  Future<AuthSessionState> Function()? loadSession,
}) => NursingHandoffCareTeamRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession:
      loadSession ?? () async => _ready(role: role, clinic: clinic, user: user),
  sessionNavigationGuard: guard,
);

void main() {
  test(
    'nurse review uses only four GETs and validates active admission',
    () async {
      final paths = <String>[];
      final client = MockClient((req) async {
        expect(req.method, 'GET');
        expect(req.headers['authorization'], 'Bearer handoff-token');
        paths.add(req.url.path);
        return _get(req);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final review = (await source.inspect(patientId: 7))!;
      expect(paths, hasLength(4));
      expect(review.careTeam.primaryNurse?.staffId, 9);
      expect(review.handoffs.single.id, 20);
      expect(review.assignmentHistory.single.id, 1);
      expect(review.mayHandoffWrite, isTrue);
      expect(review.mayTeamWrite, isFalse);
      expect(review.sessionUserId, 42);
      expect(source.writeAttempted, isFalse);
    },
  );

  test('doctor reads only; admin reads but cannot mutate handoffs', () async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method != 'GET') {
        writes++;
      }
      return _get(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in ['doctor', 'admin']) {
      final source = _source(client, guard, role: role);
      final review = (await source.inspect(patientId: 7))!;
      expect(review.mayHandoffWrite, isFalse);
      await expectLater(
        source.transitionHandoff(
          reviewed: review,
          selected: review.handoffs.single,
          action: HandoffLife.ready,
        ),
        throwsA(isA<HandoffCarePreflightChanged>()),
      );
    }
    expect(writes, 0);
  });

  test(
    'nurse cannot mutate primary assignment and admin can inspect',
    () async {
      var writes = 0;
      final client = MockClient((req) async {
        if (req.method != 'GET') {
          writes++;
        }
        return _get(req);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final nurse = _source(client, guard);
      final reviewed = (await nurse.inspect(patientId: 7))!;
      await expectLater(
        nurse.endPrimary(
          reviewed: reviewed,
          kind: PrimaryClinicianKind.primaryNurse,
          reason: 'Coverage ended',
        ),
        throwsA(isA<HandoffCarePreflightChanged>()),
      );
      final admin = (await _source(
        client,
        guard,
        role: 'admin',
      ).inspect(patientId: 7))!;
      expect(admin.mayTeamWrite, isTrue);
      expect(admin.mayHandoffWrite, isFalse);
      expect(writes, 0);
    },
  );

  test('wrong clinic care-team response is rejected', () async {
    final client = MockClient((req) async => _get(req, clinic: 99));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await expectLater(source.inspect(patientId: 7), throwsFormatException);
    expect(source.writeAttempted, isFalse);
  });

  test('exact absent admission does not call nursing endpoints', () async {
    var reads = 0;
    final client = MockClient((req) async {
      reads++;
      expect(req.url.path.endsWith('/current-admission'), isTrue);
      return _get(req, noAdmission: true);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    expect(await source.inspect(patientId: 7), isNull);
    expect(reads, 1);
  });

  test(
    'admin replace primary nurse includes compare-and-swap id and confirms GET',
    () async {
      var writes = 0;
      var nurse = 9;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          expect(
            req.url.path,
            '/api/v1/nursing/admissions/11/care-team/assignments',
          );
          expect(req.headers.containsKey('idempotency-key'), isFalse);
          final body = jsonDecode(req.body) as Map<String, dynamic>;
          expect(body, {
            'assignment_kind': 'primary_nurse',
            'staff_id': 12,
            'assignment_reason': 'Coverage change',
            'expected_current_assignment_id': 1,
          });
          nurse = 12;
          return _json({'assignment': _assignment(id: 12, staff: 12)});
        }
        return _get(req, nurse: nurse);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard, role: 'admin');
      final review = (await source.inspect(patientId: 7))!;
      final assigned = await source.assignPrimary(
        reviewed: review,
        kind: PrimaryClinicianKind.primaryNurse,
        staffId: 12,
        reason: 'Coverage change',
      );
      expect(assigned.staffId, 12);
      expect(writes, 1);
      expect(source.writeAttempted, isTrue);
      await expectLater(
        source.assignPrimary(
          reviewed: review,
          kind: PrimaryClinicianKind.primaryNurse,
          staffId: 13,
          reason: 'Try again',
        ),
        throwsA(isA<HandoffCareOutcomeUnconfirmed>()),
      );
      expect(writes, 1);
    },
  );

  test(
    'admin end assignment is compare-and-swap and readback finds none',
    () async {
      var writes = 0;
      int? nurse = 9;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          expect(
            req.url.path,
            '/api/v1/nursing/admissions/11/care-team/end-assignment',
          );
          expect(jsonDecode(req.body), {
            'assignment_kind': 'primary_nurse',
            'expected_current_assignment_id': 1,
            'end_reason': 'End coverage',
          });
          nurse = null;
          return _json({'assignment': _assignment(ended: true)});
        }
        return _get(req, nurse: nurse);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard, role: 'admin');
      final reviewed = (await source.inspect(patientId: 7))!;
      final ended = await source.endPrimary(
        reviewed: reviewed,
        kind: PrimaryClinicianKind.primaryNurse,
        reason: 'End coverage',
      );
      expect(ended.current, isFalse);
      expect(writes, 1);
    },
  );

  test(
    'handoff draft creation uses one POST and confirms server detail',
    () async {
      var writes = 0;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          expect(req.url.path, '/api/v1/nursing/admissions/11/handoffs');
          expect(req.headers.containsKey('idempotency-key'), isFalse);
          final body = jsonDecode(req.body) as Map<String, dynamic>;
          expect(body['incoming_staff_id'], 10);
          expect(body['situation'], 'Situation');
          return _json({'handoff': _handoff()}, 201);
        }
        return _get(req, includeHandoff: false);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final reviewed = (await source.inspect(patientId: 7))!;
      final created = await source.createHandoff(
        reviewed: reviewed,
        shiftStart: DateTime.utc(2026, 11, 8, 8),
        shiftEnd: DateTime.utc(2026, 11, 8, 16),
        incomingStaffId: 10,
        situation: 'Situation',
        assessment: 'Assessment',
        recommendation: 'Recommendation',
        background: 'Background',
        safetyConcerns: 'Watch oxygen',
        pendingActions: 'Follow up labs',
      );
      expect(created.id, 20);
      expect(writes, 1);
    },
  );

  test(
    'handoff draft PATCH is nurse-only, one attempt and server-confirmed',
    () async {
      var writes = 0;
      var version = 1;
      final client = MockClient((req) async {
        if (req.method == 'PATCH') {
          writes++;
          expect(req.url.path, '/api/v1/nursing/handoffs/20');
          expect(req.headers.containsKey('idempotency-key'), isFalse);
          final body = jsonDecode(req.body) as Map<String, dynamic>;
          expect(body['situation'], 'New situation');
          expect(body['incoming_staff_id'], 10);
          expect(body['background'], 'Background');
          version = 2;
          return _json({
            'handoff': _handoff(version: version, situation: 'New situation'),
          });
        }
        return _get(
          req,
          handoffVersion: version,
          situation: version == 2 ? 'New situation' : 'Situation',
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final reviewed = (await source.inspect(patientId: 7))!;
      final changed = await source.editHandoff(
        reviewed: reviewed,
        selected: reviewed.handoffs.single,
        situation: 'New situation',
        assessment: 'Assessment',
        recommendation: 'Recommendation',
        incomingStaffId: 10,
        background: 'Background',
        safetyConcerns: 'Watch oxygen',
        pendingActions: 'Follow up labs',
      );
      expect(changed.status, HandoffLife.draft);
      expect(changed.version, 2);
      expect(writes, 1);
    },
  );

  test('handoff cancel POST confirms terminal state without replay', () async {
    var writes = 0;
    var state = 'ready';
    var version = 1;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        expect(req.url.path, '/api/v1/nursing/handoffs/20/cancel');
        state = 'cancelled';
        version = 2;
        return _json({'handoff': _handoff(state: state, version: version)});
      }
      return _get(req, handoffStatus: state, handoffVersion: version);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final reviewed = (await source.inspect(patientId: 7))!;
    final result = await source.transitionHandoff(
      reviewed: reviewed,
      selected: reviewed.handoffs.single,
      action: HandoffLife.cancelled,
    );
    expect(result.status, HandoffLife.cancelled);
    expect(writes, 1);
  });

  test('live summary uses GET only and validates selected handoff', () async {
    var gets = 0;
    final client = MockClient((req) async {
      expect(req.method, 'GET');
      gets++;
      return _get(req, handoffStatus: 'ready');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final review = (await source.inspect(patientId: 7))!;
    final summary = await source.summary(
      reviewed: review,
      selected: review.handoffs.single,
    );
    expect(summary.criticalAlerts, 1);
    expect(summary.handoffId, 20);
    expect(gets, 5);
    expect(source.writeAttempted, isFalse);
  });

  test(
    'handoff ready transition sends one POST and verifies version',
    () async {
      var writes = 0;
      var state = 'draft';
      var version = 1;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          expect(req.url.path, '/api/v1/nursing/handoffs/20/ready');
          expect(jsonDecode(req.body), <String, dynamic>{});
          state = 'ready';
          version = 2;
          return _json({'handoff': _handoff(state: state, version: version)});
        }
        return _get(req, handoffStatus: state, handoffVersion: version);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final reviewed = (await source.inspect(patientId: 7))!;
      final ready = await source.transitionHandoff(
        reviewed: reviewed,
        selected: reviewed.handoffs.single,
        action: HandoffLife.ready,
      );
      expect(ready.status, HandoffLife.ready);
      expect(ready.version, 2);
      expect(writes, 1);
    },
  );

  test('incoming nurse acknowledgement uses idempotency and reads transferred owner', () async {
    var posts = 0;
    var state = 'ready';
    var version = 1;
    var nurse = 9;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        posts++;
        expect(req.url.path, '/api/v1/nursing/handoffs/20/acknowledge');
        expect(req.headers['idempotency-key'], startsWith('p23ef-'));
        expect(jsonDecode(req.body), <String, dynamic>{});
        state = 'acknowledged';
        version = 2;
        nurse = 10;
        return _json({'handoff': _handoff(state: state, version: version)});
      }
      return _get(
        req,
        nurse: nurse,
        handoffStatus: state,
        handoffVersion: version,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final reviewed = (await source.inspect(patientId: 7))!;
    final acknowledged = await source.transitionHandoff(
      reviewed: reviewed,
      selected: reviewed.handoffs.single,
      action: HandoffLife.acknowledged,
    );
    expect(acknowledged.status, HandoffLife.acknowledged);
    expect(posts, 1);
    expect(source.writeAttempted, isTrue);
  });

  test(
    'acknowledgement readback refuses missing primary-nurse transfer',
    () async {
      var posts = 0;
      var state = 'ready';
      var version = 1;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          posts++;
          state = 'acknowledged';
          version = 2;
          return _json({'handoff': _handoff(state: state, version: version)});
        }
        return _get(
          req,
          handoffStatus: state,
          handoffVersion: version,
          nurse: 9,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final review = (await source.inspect(patientId: 7))!;
      await expectLater(
        source.transitionHandoff(
          reviewed: review,
          selected: review.handoffs.single,
          action: HandoffLife.acknowledged,
        ),
        throwsA(isA<HandoffCareOutcomeUnconfirmed>()),
      );
      expect(posts, 1);
      expect(source.writeAttempted, isTrue);
    },
  );

  test(
    'lost POST response never automatically replays or clears write lock',
    () async {
      var posts = 0;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          posts++;
          throw http.ClientException('response lost after write');
        }
        return _get(req);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final reviewed = (await source.inspect(patientId: 7))!;
      await expectLater(
        source.createHandoff(
          reviewed: reviewed,
          shiftStart: DateTime.utc(2026, 11, 8, 8),
          shiftEnd: DateTime.utc(2026, 11, 8, 16),
          incomingStaffId: 10,
          situation: 'Situation',
          assessment: 'Assessment',
          recommendation: 'Recommendation',
        ),
        throwsA(isA<HandoffCareOutcomeUnconfirmed>()),
      );
      await expectLater(
        source.createHandoff(
          reviewed: reviewed,
          shiftStart: DateTime.utc(2026, 11, 8, 8),
          shiftEnd: DateTime.utc(2026, 11, 8, 16),
          incomingStaffId: 10,
          situation: 'Situation',
          assessment: 'Assessment',
          recommendation: 'Recommendation',
        ),
        throwsA(isA<HandoffCareOutcomeUnconfirmed>()),
      );
      expect(posts, 1);
      expect(source.writeAttempted, isTrue);
    },
  );

  test('stale handoff version prevents POST in preflight', () async {
    var listReads = 0;
    var posts = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        posts++;
        return _json({}, 500);
      }
      if (req.url.path.endsWith('/admissions/11/handoffs')) {
        listReads++;
        final v = listReads == 1 ? 1 : 2;
        return _json({
          'items': [_handoff(version: v)],
          'total': 1,
        });
      }
      return _get(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final reviewed = (await source.inspect(patientId: 7))!;
    await expectLater(
      source.transitionHandoff(
        reviewed: reviewed,
        selected: reviewed.handoffs.single,
        action: HandoffLife.ready,
      ),
      throwsA(isA<HandoffCarePreflightChanged>()),
    );
    expect(posts, 0);
    expect(source.writeAttempted, isFalse);
  });

  test('changed session user rejects write prior to POST', () async {
    var user = 42;
    var posts = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        posts++;
      }
      return _get(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(
      client,
      guard,
      loadSession: () async => _ready(user: user),
    );
    final reviewed = (await source.inspect(patientId: 7))!;
    user = 84;
    await expectLater(
      source.transitionHandoff(
        reviewed: reviewed,
        selected: reviewed.handoffs.single,
        action: HandoffLife.ready,
      ),
      throwsA(isA<HandoffCareSessionUnavailable>()),
    );
    expect(posts, 0);
  });

  test('signout prevents further GET and clears access', () async {
    var calls = 0;
    final client = MockClient((req) async {
      calls++;
      return _get(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    guard.markSignedOut();
    await expectLater(
      source.inspect(patientId: 7),
      throwsA(isA<HandoffCareSessionUnavailable>()),
    );
    expect(calls, 0);
  });
}
