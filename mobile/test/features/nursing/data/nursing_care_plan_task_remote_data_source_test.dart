import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/nursing/data/models/nursing_care_plan_task.dart';
import 'package:clinic_system_pro/features/nursing/data/nursing_care_plan_task_remote_data_source.dart';

AuthSessionState ready({String role = 'nurse', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'nursing-token',
        refreshToken: 'refresh',
        userId: 42,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> admission() => {
  'id': 11,
  'patient_id': 7,
  'bed_id': 9,
  'admitted_by_id': 8,
  'status': 'admitted',
};

Map<String, dynamic> plan({
  String status = 'active',
  int version = 1,
  int id = 20,
}) => {
  'id': id,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 11,
  'created_by_staff_id': 8,
  'title': 'Pain management',
  'problem': 'Acute discomfort',
  'goal': 'Pain score reduced',
  'status': status,
  'started_at': '2026-11-08T09:00:00+00:00',
  'version': version,
};

Map<String, dynamic> task({
  String status = 'pending',
  int version = 1,
  int id = 30,
  String priority = 'high',
}) => {
  'id': id,
  'care_plan_id': 20,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 11,
  'created_by_staff_id': 8,
  'description': 'Assess pain',
  'priority': priority,
  'status': status,
  'version': version,
};

NursingCarePlanTaskRemoteDataSource source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'nurse',
  int clinic = 3,
}) => NursingCarePlanTaskRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => ready(role: role, clinic: clinic),
  sessionNavigationGuard: guard,
);

http.Response response(Object json, [int status = 200]) =>
    http.Response(jsonEncode(json), status);

http.Response read(
  http.Request request, {
  String planStatus = 'active',
  String taskStatus = 'pending',
  int planVersion = 1,
  int taskVersion = 1,
  bool noAdmission = false,
}) {
  final path = request.url.path;
  if (path.endsWith('/current-admission')) {
    return noAdmission
        ? response({'message': 'No active admission found'}, 404)
        : response(admission());
  }
  if (path.endsWith('/admissions/11/care-plans')) {
    return response({
      'items': [plan(status: planStatus, version: planVersion)],
      'total': 1,
    });
  }
  if (path.endsWith('/admissions/11/tasks')) {
    return response({
      'items': [task(status: taskStatus, version: taskVersion)],
      'total': 1,
    });
  }
  if (path.endsWith('/care-plans/21')) {
    return response(plan(id: 21));
  }
  if (path.endsWith('/care-plans/20')) {
    return response(plan(status: planStatus, version: planVersion));
  }
  if (path.endsWith('/tasks/31')) {
    return response(task(id: 31, priority: 'urgent'));
  }
  if (path.endsWith('/tasks/30')) {
    return response(task(status: taskStatus, version: taskVersion));
  }
  throw StateError('Unexpected nursing GET route: $path');
}

void main() {
  test('GET-only nurse review reuses admission and raw care lists', () async {
    var reads = 0;
    final client = MockClient((req) async {
      reads++;
      expect(req.method, 'GET');
      expect(req.headers['authorization'], 'Bearer nursing-token');
      expect(req.headers.containsKey('idempotency-key'), isFalse);
      return read(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = source(client, guard);
    final reviewed = await s.inspect(patientId: 7);
    expect(reviewed!.admission.id, 11);
    expect(reviewed.plans.single.id, 20);
    expect(reviewed.tasks.single.carePlanId, 20);
    expect(reviewed.mayWrite, isTrue);
    expect(reads, 3);
    expect(s.writeAttempted, isFalse);
  });

  test(
    'exact no-active-admission 404 returns null without nursing calls',
    () async {
      var reads = 0;
      final client = MockClient((req) async {
        reads++;
        expect(req.url.path.endsWith('/current-admission'), isTrue);
        return read(req, noAdmission: true);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      expect(await source(client, guard).inspect(patientId: 7), isNull);
      expect(reads, 1);
    },
  );

  test('doctor and admin can read but cannot create plans', () async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        return response({}, 500);
      }
      return read(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in ['doctor', 'admin']) {
      final s = source(client, guard, role: role);
      final reviewed = (await s.inspect(patientId: 7))!;
      expect(reviewed.mayWrite, isFalse);
      await expectLater(
        s.createPlan(
          reviewed: reviewed,
          title: 'Plan',
          problem: 'P',
          goal: 'G',
        ),
        throwsA(isA<NursingCarePreflightChanged>()),
      );
    }
    expect(writes, 0);
  });

  test(
    'create plan makes exactly one POST and confirms GET readback',
    () async {
      var writes = 0;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          expect(req.url.path, '/api/v1/nursing/admissions/11/care-plans');
          expect(req.headers.containsKey('idempotency-key'), isFalse);
          expect(jsonDecode(req.body), {
            'title': 'Care plan',
            'problem': 'Issue',
            'goal': 'Improve',
          });
          return response({'care_plan': plan(id: 21)}, 201);
        }
        return read(req);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final s = source(client, guard);
      final snapshot = (await s.inspect(patientId: 7))!;
      final result = await s.createPlan(
        reviewed: snapshot,
        title: 'Care plan',
        problem: 'Issue',
        goal: 'Improve',
      );
      expect(result.id, 21);
      expect(writes, 1);
      expect(s.writeAttempted, isTrue);
      await expectLater(
        s.createPlan(
          reviewed: snapshot,
          title: 'Care plan',
          problem: 'Issue',
          goal: 'Improve',
        ),
        throwsA(isA<NursingCareOutcomeUnconfirmed>()),
      );
      expect(writes, 1);
    },
  );

  test(
    'plan completion supplies idempotency key and verifies status',
    () async {
      var writes = 0;
      final client = MockClient((req) async {
        if (req.method == 'POST') {
          writes++;
          expect(req.url.path, '/api/v1/nursing/care-plans/20/complete');
          expect(req.headers['idempotency-key'], startsWith('p23ed-'));
          expect(jsonDecode(req.body), <String, dynamic>{});
          return response({'care_plan': plan(status: 'completed', version: 2)});
        }
        return read(
          req,
          planStatus: writes == 0 ? 'active' : 'completed',
          planVersion: writes == 0 ? 1 : 2,
        );
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final s = source(client, guard);
      final snapshot = (await s.inspect(patientId: 7))!;
      final result = await s.changePlan(
        reviewed: snapshot,
        selected: snapshot.plans.single,
        action: NursingPlanAction.complete,
      );
      expect(result.status, NursingPlanStatus.completed);
      expect(writes, 1);
    },
  );

  test('task completion supplies idempotency key and verifies GET', () async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        expect(req.url.path, '/api/v1/nursing/tasks/30/complete');
        expect(req.headers['idempotency-key'], startsWith('p23ed-'));
        return response({'task': task(status: 'completed', version: 2)});
      }
      return read(
        req,
        taskStatus: writes == 0 ? 'in_progress' : 'completed',
        taskVersion: writes == 0 ? 1 : 2,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = source(client, guard);
    final snapshot = (await s.inspect(patientId: 7))!;
    final result = await s.changeTask(
      reviewed: snapshot,
      selected: snapshot.tasks.single,
      action: NursingTaskAction.complete,
    );
    expect(result.status, NursingTaskStatus.completed);
    expect(writes, 1);
  });

  test('task create obeys plan scope and confirms task detail GET', () async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        expect(req.url.path, '/api/v1/nursing/care-plans/20/tasks');
        expect(req.headers.containsKey('idempotency-key'), isFalse);
        expect(jsonDecode(req.body), {
          'description': 'Assess pain',
          'priority': 'urgent',
        });
        return response({'task': task(id: 31, priority: 'urgent')}, 201);
      }
      return read(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = source(client, guard);
    final snapshot = (await s.inspect(patientId: 7))!;
    final created = await s.createTask(
      reviewed: snapshot,
      selectedPlan: snapshot.plans.single,
      description: 'Assess pain',
      priority: NursingTaskPriority.urgent,
    );
    expect(created.id, 31);
    expect(created.carePlanId, 20);
    expect(writes, 1);
  });

  test('unknown POST transport outcome locks all further writes', () async {
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        throw http.ClientException('response lost');
      }
      return read(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = source(client, guard);
    final snapshot = (await s.inspect(patientId: 7))!;
    for (var i = 0; i < 2; i++) {
      await expectLater(
        s.createPlan(
          reviewed: snapshot,
          title: 'Care plan',
          problem: 'Issue',
          goal: 'Improve',
        ),
        throwsA(isA<NursingCareOutcomeUnconfirmed>()),
      );
    }
    expect(s.writeAttempted, isTrue);
    expect(writes, 1);
  });

  test('preflight refuses stale plan version without POST', () async {
    var plansRead = 0;
    var writes = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        writes++;
        return response({}, 500);
      }
      if (req.url.path.endsWith('/admissions/11/care-plans')) {
        plansRead++;
        return response({
          'items': [plan(version: plansRead == 1 ? 1 : 2)],
          'total': 1,
        });
      }
      return read(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = source(client, guard);
    final snapshot = (await s.inspect(patientId: 7))!;
    await expectLater(
      s.changePlan(
        reviewed: snapshot,
        selected: snapshot.plans.single,
        action: NursingPlanAction.hold,
      ),
      throwsA(isA<NursingCarePreflightChanged>()),
    );
    expect(writes, 0);
    expect(s.writeAttempted, isFalse);
  });

  test('sign-out during nursing read cannot disclose stale data', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((req) async {
      if (req.url.path.endsWith('/admissions/11/care-plans')) {
        guard.markSignedOut();
      }
      return read(req);
    });
    addTearDown(client.close);
    final s = source(client, guard);
    await expectLater(
      s.inspect(patientId: 7),
      throwsA(isA<NursingCareSessionUnavailable>()),
    );
    expect(s.writeAttempted, isFalse);
  });

  test('clinic mismatch in nursing list cannot be displayed', () async {
    final client = MockClient((req) async {
      if (req.url.path.endsWith('/admissions/11/care-plans')) {
        return response({
          'items': [
            {...plan(), 'clinic_id': 999},
          ],
          'total': 1,
        });
      }
      return read(req);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      source(client, guard).inspect(patientId: 7),
      throwsFormatException,
    );
  });
}
