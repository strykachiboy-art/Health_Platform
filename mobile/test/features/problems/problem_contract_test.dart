import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/problems/data/problem_source.dart';

const tenant = '123e4567-e89b-42d3-a456-426614174001';
const foreignTenant = '123e4567-e89b-42d3-a456-426614174002';
const stamp = '2026-11-01T10:00:00+00:00';
const root = '/api/v1/patients/7/problems';
AuthSessionState ready({
  String role = 'doctor',
  String? organization = tenant,
  int? clinic = 3,
  String token = 'bound-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'refresh-token',
    userId: 19,
    role: role,
    clinicContextId: clinic,
    tenantId: organization,
  ),
);
Map<String, dynamic> content({
  String name = 'Synthetic hypertension',
  String clinical = 'active',
  String verification = 'confirmed',
  String? code = 'I10',
  String? system = 'http://hl7.org/fhir/sid/icd-10',
  String? notes,
}) => {
  'name': name,
  'code': code,
  'coding_system': system,
  'category': 'chronic',
  'clinical_status': clinical,
  'verification_status': verification,
  'severity': null,
  'onset_date': '2026-01-01',
  'onset_description': null,
  'resolution_date': clinical == 'resolved' ? '2026-11-01' : null,
  'notes': notes,
};
Map<String, dynamic> record({
  int id = 9,
  int patient = 7,
  int clinic = 3,
  String organization = tenant,
  int version = 1,
  Map<String, dynamic>? value,
  List<int> links = const [],
}) {
  final c = value ?? content();
  final confirmed = c['verification_status'] == 'confirmed';
  return {
    'id': id,
    'patient_id': patient,
    'clinic_id': clinic,
    'organization_id': organization,
    'version': version,
    'content': c,
    'consultation_ids': links,
    'recorded_by_staff_id': 8,
    'recorded_at': stamp,
    'reviewed_by_staff_id': 8,
    'reviewed_at': stamp,
    'confirmed_by_staff_id': confirmed ? 8 : null,
    'confirmed_at': confirmed ? stamp : null,
  };
}

http.Response ok(Object data, {int status = 200}) => http.Response(
  jsonEncode({'success': true, 'data': data}),
  status,
  headers: {'content-type': 'application/json'},
);
http.Response page(List<Object> items, {int? next}) =>
    ok({'items': items, 'next_after': next});
http.Response denied(int code) => http.Response(
  '{"success":false,"error":"Synthetic denial"}',
  code,
  headers: {'content-type': 'application/json'},
);
Map<String, dynamic> projection({
  int patient = 7,
  int resource = 12,
  bool medication = false,
  String verification = 'confirmed',
  String clinical = 'active',
}) => {
  'patient_id': patient,
  (medication ? 'reconciliation_id' : 'admission_id'): resource,
  'evaluated_at': stamp,
  'confirmed_current_problems': [
    {
      'problem_id': 9,
      'version': 1,
      'name': 'Synthetic hypertension',
      'code': 'I10',
      'coding_system': problemCodingSystems.first,
      'clinical_status': clinical,
      'verification_status': verification,
    },
  ],
};

class Net implements DeviceConnectivitySource {
  DeviceNetworkStatus value = DeviceNetworkStatus.connected;
  @override
  Future<DeviceNetworkStatus> checkStatus() async => value;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

ApiClient apiFor(MockClient client) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  addTearDown(api.close);
  return api;
}

class Harness {
  Harness(MockClient client, {AuthSessionState? auth})
    : state = auth ?? ready() {
    source = ProblemSource(
      apiClient: apiFor(client),
      guard: guard,
      loadSession: () async => state,
      connectivity: net,
    );
    addTearDown(guard.dispose);
    addTearDown(client.close);
  }
  AuthSessionState state;
  final guard = SessionNavigationGuard()..markSessionAvailable();
  final net = Net();
  late final ProblemSource source;
}

void main() {
  test(
    'list uses bearer, exact filter/cursor and checks returned ordering',
    () async {
      final h = Harness(
        MockClient((r) async {
          expect(r.method, 'GET');
          expect(r.url.path, root);
          expect(r.headers['authorization'], 'Bearer bound-token');
          expect(r.url.queryParameters, {
            'status': 'current',
            'after': '5',
            'limit': '2',
          });
          return page([record(id: 6), record(id: 9)], next: 9);
        }),
      );
      final s = await h.source.open();
      final p = await s.list(7, after: 5, limit: 2);
      expect(p.items.map((x) => x.id), [6, 9]);
      expect(p.nextAfter, 9);
    },
  );
  for (final bad in [
    record(patient: 8),
    record(clinic: 4),
    record(organization: foreignTenant),
    record(value: content(clinical: 'resolved')),
    record(value: content(verification: 'refuted')),
  ]) {
    test(
      'reject foreign or incompatible record ${bad['patient_id']}/${bad['clinic_id']}/${bad['organization_id']}/${bad['content']}',
      () async {
        final h = Harness(MockClient((_) async => page([bad])));
        final s = await h.source.open();
        await expectLater(s.list(7), throwsFormatException);
      },
    );
  }
  test('malformed and duplicate cursor records fail closed', () async {
    final h = Harness(
      MockClient((_) async => page([record(), record()], next: 9)),
    );
    final s = await h.source.open();
    await expectLater(s.list(7, limit: 2), throwsFormatException);
  });
  test('current/historical/resolved keep provisional and erroneous records distinct', () async {
    final h = Harness(
      MockClient((r) async {
        switch (r.url.queryParameters['status']) {
          case 'current':
            return page([record(value: content(verification: 'provisional'))]);
          case 'historical':
            return page([
              record(value: content(verification: 'entered_in_error')),
            ]);
          case 'resolved':
            return page([record(value: content(clinical: 'resolved'))]);
          default:
            throw StateError('Unexpected filter');
        }
      }),
    );
    final s = await h.source.open();
    expect(
      (await s.list(7)).items.single.content.verificationStatus,
      'provisional',
    );
    expect(
      (await s.list(7, filter: 'historical')).items.single.content.terminal,
      true,
    );
    expect(
      (await s.list(7, filter: 'resolved')).items.single.content.resolutionDate,
      '2026-11-01',
    );
  });
  for (final role in [
    'super_admin',
    'admin',
    'patient',
    'pharmacist',
    'receptionist',
    'lab_technician',
    'paramedic',
    'unknown',
  ]) {
    test('$role cannot open general problem session', () async {
      var requests = 0;
      final h = Harness(
        MockClient((_) async {
          requests++;
          return page([]);
        }),
        auth: ready(role: role),
      );
      await expectLater(h.source.open(), throwsA(isA<ProblemUnavailable>()));
      expect(requests, 0);
    });
  }
  test('unbound tenant or clinic denied without transport', () async {
    final h = Harness(
      MockClient((_) async => throw StateError('Unexpected request')),
      auth: ready(organization: null),
    );
    await expectLater(h.source.open(), throwsA(isA<ProblemUnavailable>()));
    h.state = ready(clinic: null);
    await expectLater(h.source.open(), throwsA(isA<ProblemUnavailable>()));
  });
  test('nurse can read but cannot create or change', () async {
    final h = Harness(
      MockClient((r) async {
        expect(r.method, 'GET');
        return ok(record());
      }),
      auth: ready(role: 'nurse'),
    );
    final s = await h.source.open();
    final p = await s.detail(7, 9);
    expect(s.canWrite, false);
    await expectLater(
      s.create(7, ProblemContent.fromJson(content())),
      throwsA(isA<ProblemUnavailable>()),
    );
    await expectLater(
      s.change(p, 'review', 'Clinical review'),
      throwsA(isA<ProblemUnavailable>()),
    );
  });
  test(
    'pharmacist only gets reconciliation-bound current confirmed projection',
    () async {
      var calls = 0;
      final h = Harness(
        MockClient((r) async {
          calls++;
          expect(r.method, 'GET');
          expect(
            r.url.path,
            '/api/v1/medication-reconciliations/12/problem-context',
          );
          return ok(projection(medication: true));
        }),
        auth: ready(role: 'pharmacist'),
      );
      final s = await h.source.open(medicationReview: true);
      expect(
        (await s.workflow(7, 12, medication: true)).conditions.single.name,
        'Synthetic hypertension',
      );
      await expectLater(s.list(7), throwsA(isA<ProblemUnavailable>()));
      await expectLater(
        s.workflow(7, 12, medication: false),
        throwsA(isA<ProblemUnavailable>()),
      );
      expect(calls, 1);
    },
  );
  test('workflow enforces resource and patient identity and confirmed-current status', () async {
    var i = 0;
    final bad = [
      projection(patient: 8),
      projection(resource: 13),
      projection(verification: 'provisional'),
      projection(clinical: 'resolved'),
    ];
    final h = Harness(
      MockClient((r) async {
        expect(r.url.path, '/api/v1/wards/admissions/12/problem-context');
        return ok(bad[i++]);
      }),
    );
    final s = await h.source.open();
    for (var j = 0; j < bad.length; j++) {
      await expectLater(
        s.workflow(7, 12, medication: false),
        throwsFormatException,
      );
    }
  });
  test('doctor writes full explicit lifecycle with versions and no offline idempotency replay', () async {
    var version = 1;
    var c = content();
    var links = <int>[11];
    var sends = 0;
    final h = Harness(
      MockClient((r) async {
        expect(r.headers['authorization'], 'Bearer bound-token');
        expect(r.headers.containsKey('idempotency-key'), false);
        if (r.method == 'POST') {
          sends++;
          expect(r.url.path, root);
          final b = jsonDecode(r.body) as Map<String, dynamic>;
          expect(b, {...c, 'consultation_id': 11});
          return ok(record(value: c, links: links), status: 201);
        }
        if (r.method == 'GET') {
          expect(r.url.path, '$root/9');
          return ok(record(version: version, value: c, links: links));
        }
        sends++;
        expect(r.method, 'PATCH');
        expect(r.url.path, '$root/9');
        final b = jsonDecode(r.body) as Map<String, dynamic>;
        expect(b['expected_version'], version);
        expect(b['reason'], 'Explicit review reason');
        if (b['action'] == 'associate') {
          expect(b.containsKey('content'), false);
          links = [...links, b['consultation_id'] as int];
        } else if (b['action'] == 'review') {
          expect(b.containsKey('content'), false);
        } else {
          c = b['content'] as Map<String, dynamic>;
        }
        version++;
        return ok(record(version: version, value: c, links: links));
      }),
    );
    final s = await h.source.open();
    final created = await s.create(
      7,
      ProblemContent.fromJson(c),
      consultationId: 11,
    );
    expect(created.version, 1);
    var prior = await s.detail(7, 9);
    await s.change(prior, 'review', 'Explicit review reason');
    prior = await s.detail(7, 9);
    await s.change(
      prior,
      'update',
      'Explicit review reason',
      content: ProblemContent.fromJson({...c, 'notes': 'Reviewed note'}),
    );
    prior = await s.detail(7, 9);
    await s.change(
      prior,
      'transition',
      'Explicit review reason',
      content: ProblemContent.fromJson({
        ...c,
        'clinical_status': 'resolved',
        'resolution_date': '2026-11-01',
      }),
    );
    prior = await s.detail(7, 9);
    await s.change(
      prior,
      'transition',
      'Explicit review reason',
      content: ProblemContent.fromJson({
        ...c,
        'clinical_status': 'recurrence',
        'resolution_date': null,
      }),
    );
    prior = await s.detail(7, 9);
    await s.change(
      prior,
      'associate',
      'Explicit review reason',
      consultationId: 12,
    );
    prior = await s.detail(7, 9);
    final terminal = await s.change(
      prior,
      'correct',
      'Explicit review reason',
      content: ProblemContent.fromJson({
        ...c,
        'verification_status': 'entered_in_error',
      }),
    );
    expect(terminal.version, 7);
    expect(terminal.consultationIds, [11, 12]);
    expect(terminal.content.terminal, true);
    await expectLater(
      s.change(terminal, 'review', 'Explicit review reason'),
      throwsFormatException,
    );
    expect(sends, 7);
  });
  test(
    'history retains immutable original and scoped full snapshots',
    () async {
      final updated = content(notes: 'Reviewed note');
      final h = Harness(
        MockClient((r) async {
          if (r.url.path.endsWith('/history')) {
            return ok({
              'original': record(),
              'amendments': [
                {
                  'version': 2,
                  'reason': 'Review reason',
                  'actor_staff_id': 8,
                  'amended_at': stamp,
                  'content': record(version: 2, value: updated),
                  'sha256': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                },
              ],
            });
          }
          return ok(record(version: 2, value: updated));
        }),
      );
      final s = await h.source.open();
      final d = await s.detail(7, 9);
      final hist = await s.history(d);
      expect(hist.original.content.notes, null);
      expect(hist.amendments.single.snapshot.content.notes, 'Reviewed note');
      expect(hist.amendments.single.reason, 'Review reason');
    },
  );
  test('invalid history version or ownership fails closed', () async {
    final h = Harness(
      MockClient(
        (r) async => r.url.path.endsWith('/history')
            ? ok({'original': record(patient: 8), 'amendments': []})
            : ok(record()),
      ),
    );
    final s = await h.source.open();
    final d = await s.detail(7, 9);
    await expectLater(s.history(d), throwsFormatException);
  });
  test(
    'stale version conflict does not replay the selected snapshot',
    () async {
      var patches = 0;
      final h = Harness(
        MockClient((r) async {
          if (r.method == 'GET') {
            return ok(record());
          }
          patches++;
          return denied(409);
        }),
      );
      final s = await h.source.open();
      final p = await s.detail(7, 9);
      await expectLater(
        s.change(p, 'review', 'Reason'),
        throwsA(isA<ApiException>().having((e) => e.statusCode, 'status', 409)),
      );
      await expectLater(
        s.change(p, 'review', 'Reason'),
        throwsA(isA<ProblemUnavailable>()),
      );
      expect(patches, 1);
      expect(h.source.outcomeUnconfirmed, false);
    },
  );
  test(
    'invalid action, terminal creation and disconnected write send nothing',
    () async {
      var requests = 0;
      final h = Harness(
        MockClient((_) async {
          requests++;
          return ok(record());
        }),
      );
      final s = await h.source.open();
      await expectLater(
        s.create(7, ProblemContent.fromJson(content(verification: 'refuted'))),
        throwsFormatException,
      );
      h.net.value = DeviceNetworkStatus.disconnected;
      await expectLater(
        s.create(7, ProblemContent.fromJson(content())),
        throwsA(isA<ProblemUnavailable>()),
      );
      expect(requests, 0);
      expect(h.source.writesHeld, false);
    },
  );
  test(
    'unconfirmed write locks shared adapter across route reopening',
    () async {
      var posts = 0;
      final h = Harness(
        MockClient((r) async {
          if (r.method == 'GET') {
            return page([]);
          }
          posts++;
          return denied(503);
        }),
      );
      final s = await h.source.open();
      await expectLater(
        s.create(7, ProblemContent.fromJson(content())),
        throwsA(isA<ProblemOutcomeUnconfirmed>()),
      );
      final reopened = await h.source.open();
      await expectLater(
        reopened.create(7, ProblemContent.fromJson(content())),
        throwsA(isA<ProblemUnavailable>()),
      );
      expect((await reopened.list(7)).items, isEmpty);
      expect(posts, 1);
      expect(h.source.outcomeUnconfirmed, true);
    },
  );
  test('wrong mutation response identity also locks further writes', () async {
    final h = Harness(
      MockClient((_) async => ok(record(patient: 8), status: 201)),
    );
    final s = await h.source.open();
    await expectLater(
      s.create(7, ProblemContent.fromJson(content())),
      throwsA(isA<ProblemOutcomeUnconfirmed>()),
    );
    expect(h.source.writesHeld, true);
  });
  test(
    'late response discarded after guard invalidation or tenant/token change',
    () async {
      final entered = Completer<void>();
      final response = Completer<http.Response>();
      final h = Harness(
        MockClient((_) async {
          entered.complete();
          return response.future;
        }),
      );
      final s = await h.source.open();
      final pending = s.list(7);
      await entered.future;
      final rejected = expectLater(pending, throwsA(isA<ProblemUnavailable>()));
      h.guard.invalidate();
      response.complete(page([record()]));
      await rejected;
    },
  );
  test(
    'tenant and access token rechecked even without guard revision change',
    () async {
      final entered = Completer<void>();
      final response = Completer<http.Response>();
      final h = Harness(
        MockClient((_) async {
          entered.complete();
          return response.future;
        }),
      );
      final s = await h.source.open();
      final pending = s.detail(7, 9);
      await entered.future;
      final rejected = expectLater(pending, throwsA(isA<ProblemUnavailable>()));
      h.state = ready(organization: foreignTenant, token: 'new-token');
      response.complete(ok(record()));
      await rejected;
    },
  );
}
