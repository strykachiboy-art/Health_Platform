import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/unified_search/data/search_source.dart';

import '../problems/problem_contract_test.dart' as f;

Map<String, dynamic> hit({
  String type = 'patient',
  int id = 7,
  int? patient,
  String name = 'Synthetic Person',
  String? status,
  int? admission,
}) => {
  'resource_type': type,
  'record_id': id,
  'patient_id': patient ?? (type == 'patient' ? id : 7),
  'patient_name': name,
  'patient_number': 'SYN-007',
  'status':
      status ??
      (type == 'appointment'
          ? 'scheduled'
          : type == 'lab_order'
          ? 'ordered'
          : type == 'prescription' || type == 'patient'
          ? 'active'
          : type == 'medication_administration'
          ? 'scheduled'
          : 'pending'),
  'admission_id':
      admission ??
      (const {
            'imaging_order',
            'procedure_order',
            'blood_product_order',
            'diet_order',
            'nursing_order',
            'nursing_task',
            'medication_administration',
          }.contains(type)
          ? 12
          : null),
  'kind': type == 'patient' ? 'patient' : 'operational',
};
Map<String, dynamic> page(
  List<Object> items, {
  String role = 'doctor',
  String? next,
  int clinic = 3,
  String org = f.tenant,
}) => {
  'organization_id': org,
  'clinic_id': clinic,
  'items': items,
  'next_cursor': next,
  'available_resources': {
    for (final e in searchResources.entries)
      if (e.value.$1.contains(role)) e.key: e.value.$2.toList(),
  },
};

class Harness {
  Harness(MockClient client, {AuthSessionState? auth})
    : state = auth ?? f.ready() {
    source = SearchSource(
      apiClient: f.apiFor(client),
      loadSession: () async => state,
      guard: guard,
    );
    addTearDown(guard.dispose);
    addTearDown(client.close);
  }
  AuthSessionState state;
  final guard = SessionNavigationGuard()..markSessionAvailable();
  late final SearchSource source;
}

void main() {
  test(
    'POST body only, bearer, no emergency header or idempotency/replay',
    () async {
      final h = Harness(
        MockClient((r) async {
          expect(r.method, 'POST');
          expect(r.url.path, '/api/v1/search');
          expect(r.url.query, isEmpty);
          expect(r.headers['authorization'], 'Bearer bound-token');
          expect(r.headers.containsKey('idempotency-key'), false);
          expect(r.headers.containsKey('x-emergency-access-id'), false);
          expect(jsonDecode(r.body), {
            'q': 'Synthetic',
            'resource_types': ['patient'],
            'status': 'active',
            'limit': 25,
            'cursor': null,
          });
          return f.ok(page([hit()]));
        }),
      );
      final s = await h.source.open();
      final p = await s.read(
        SearchFilters(' Synthetic ', resourceType: 'patient', status: 'active'),
      );
      expect(p.items.single.routeFor('doctor'), '/patients/7');
      expect(p.items.single.exactRoute, true);
    },
  );
  for (final role in searchRoles) {
    test('$role allowed resources match role scope', () async {
      final h = Harness(
        MockClient((_) async => f.ok(page([], role: role))),
        auth: f.ready(role: role),
      );
      final s = await h.source.open();
      expect((await s.read(SearchFilters('Syn'))).items, isEmpty);
      expect(
        s.available.keys,
        searchResources.entries
            .where((e) => e.value.$1.contains(role))
            .map((e) => e.key),
      );
    });
  }
  for (final role in [
    'super_admin',
    'patient',
    'accountant',
    'driver',
    'ambulance_coordinator',
    'other',
    'unknown',
  ]) {
    test('$role denied before transport', () async {
      final h = Harness(
        MockClient((_) async => throw StateError('No request expected')),
        auth: f.ready(role: role),
      );
      await expectLater(h.source.open(), throwsA(isA<SearchUnavailable>()));
    });
  }
  test('unbound clinic or organization denied', () async {
    final h = Harness(
      MockClient((_) async => throw StateError('No request expected')),
      auth: f.ready(organization: null),
    );
    await expectLater(h.source.open(), throwsA(isA<SearchUnavailable>()));
    h.state = f.ready(clinic: null);
    await expectLater(h.source.open(), throwsA(isA<SearchUnavailable>()));
  });
  test('unauthorized filter not sent', () async {
    final h = Harness(
      MockClient((_) async => throw StateError('No request expected')),
      auth: f.ready(role: 'nurse'),
    );
    final s = await h.source.open();
    await expectLater(
      s.read(SearchFilters('Syn', resourceType: 'prescription')),
      throwsA(isA<SearchUnavailable>()),
    );
  });
  test(
    'too short, too long, invalid exact ID, status or page size rejected',
    () {
      for (final text in [
        '',
        'a',
        '#0',
        '#01',
        '#abc',
        '#2147483648',
        'abc\nxyz',
        List.filled(121, 'a').join(),
      ]) {
        expect(() => SearchFilters(text), throwsFormatException);
      }
      expect(() => SearchFilters('ab', limit: 51), throwsFormatException);
      expect(
        () => SearchFilters('ab', resourceType: 'patient', status: 'scheduled'),
        throwsFormatException,
      );
    },
  );
  test('exact ID validates all hits, and patient/operational rank', () async {
    final h = Harness(
      MockClient(
        (_) async => f.ok(page([hit(id: 9), hit(type: 'appointment', id: 9)])),
      ),
    );
    final s = await h.source.open();
    final p = await s.read(SearchFilters('#9'));
    expect(p.items.map((x) => x.kind), ['patient', 'operational']);
    expect(p.items.last.routeFor('doctor'), '/appointments/patient/7');
    expect(p.items.last.exactRoute, false);
  });
  test(
    'opaque cursor stays bound to session, query, filters and ordering',
    () async {
      var calls = 0;
      final h = Harness(
        MockClient((r) async {
          calls++;
          final b = jsonDecode(r.body) as Map<String, dynamic>;
          if (calls == 1) {
            expect(b['cursor'], isNull);
            return f.ok(page([hit(id: 9)], next: 'opaque'));
          }
          expect(b['cursor'], 'opaque');
          return f.ok(page([hit(id: 8)]));
        }),
      );
      final s = await h.source.open();
      final f1 = SearchFilters('Syn', limit: 1);
      final first = await s.read(f1);
      await expectLater(
        s.read(SearchFilters('New', limit: 1), previous: first),
        throwsFormatException,
      );
      final other = await h.source.open();
      await expectLater(other.read(f1, previous: first), throwsFormatException);
      expect((await s.read(f1, previous: first)).items.single.recordId, 8);
      expect(calls, 2);
    },
  );
  for (final e in <String, Map<String, dynamic>>{
    'foreign clinic': page([], clinic: 4),
    'foreign organization': page([], org: f.foreignTenant),
    'foreign type': page([hit()..['resource_type'] = 'audit']),
    'invalid identity': page([hit(patient: 8)]),
    'invalid kind': page([hit()..['kind'] = 'operational']),
    'invalid status': page([hit(status: 'unknown')]),
    'duplicates': page([hit(), hit()]),
    'wrong order': page([hit(id: 6), hit(id: 9)]),
    'wrong rank': page([hit(type: 'appointment'), hit()]),
    'missing admission': page([
      hit(type: 'nursing_task')..['admission_id'] = null,
    ]),
    'unexpected cursor': page([], next: 'opaque'),
    'bad allowed resources': page([])..['available_resources'] = {},
  }.entries) {
    test('reject ${e.key}', () async {
      final h = Harness(MockClient((_) async => f.ok(e.value)));
      final s = await h.source.open();
      await expectLater(s.read(SearchFilters('Syn')), throwsFormatException);
    });
  }
  test('out-of-filter and out-of-exact-ID data denied', () async {
    final h = Harness(MockClient((_) async => f.ok(page([hit()]))));
    final s = await h.source.open();
    await expectLater(s.read(SearchFilters('#8')), throwsFormatException);
    await expectLater(
      s.read(SearchFilters('Syn', resourceType: 'appointment')),
      throwsFormatException,
    );
    await expectLater(
      s.read(SearchFilters('Syn', status: 'inactive')),
      throwsFormatException,
    );
  });
  test('late token change discards response', () async {
    final sent = Completer<void>();
    final response = Completer<Map<String, dynamic>>();
    final h = Harness(
      MockClient((_) async {
        sent.complete();
        return f.ok(await response.future);
      }),
    );
    final s = await h.source.open();
    final future = s.read(SearchFilters('Syn'));
    final check = expectLater(future, throwsA(isA<SearchUnavailable>()));
    await sent.future;
    h.state = f.ready(token: 'replacement');
    response.complete(page([hit()]));
    await check;
  });
  test('invalidated or superseded read sends no request', () async {
    final h = Harness(
      MockClient((_) async => throw StateError('Unexpected request')),
    );
    final s = await h.source.open();
    await expectLater(
      s.read(SearchFilters('Syn'), stillWanted: () => false),
      throwsA(isA<SearchUnavailable>()),
    );
    h.guard.invalidate();
    await expectLater(
      s.read(SearchFilters('Syn')),
      throwsA(isA<SearchUnavailable>()),
    );
  });
  test('all routes are fixed templates, collection links honest', () async {
    final h = Harness(
      MockClient(
        (_) async =>
            f.ok(page([for (final t in searchResources.keys) hit(type: t)])),
      ),
    );
    final s = await h.source.open();
    final p = await s.read(SearchFilters('Syn'));
    expect(p.items.map((x) => x.routeFor('doctor')), [
      '/patients/7',
      '/appointments/patient/7',
      '/patients/7/lab-orders',
      '/patients/7/prescriptions/7',
      '/clinical-orders/patient/7/imaging',
      '/clinical-orders/patient/7/procedures',
      '/clinical-orders/patient/7/blood-products',
      '/clinical-orders/patient/7/diets',
      '/clinical-orders/patient/7/nursing-orders',
      '/wards',
      '/emar/patient/7',
    ]);
    expect(p.items.where((x) => x.exactRoute).length, 2);
    expect(p.items[3].routeFor('nurse'), isNull);
    expect(
      p.items[6].routeFor('lab_technician'),
      '/clinical-orders/blood-products',
    );
  });
}
