import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/clinical_timeline/data/timeline_source.dart';

import '../problems/problem_contract_test.dart' as f;

const timelineRoot = '/api/v1/patients/7/clinical-timeline';
Map<String, dynamic> event({
  int row = 9,
  String type = 'consultation_started',
  String date = f.stamp,
  int? sourceId,
  int? version,
  Map<String, dynamic>? staff,
}) {
  final definition = timelineEvents[type]!;
  return {
    'event_id': '$type:$row',
    'category': definition.$1,
    'event_type': type,
    'phase': definition.$2,
    'occurred_at': date,
    'source': {'kind': definition.$3, 'id': sourceId ?? row},
    'record_version': version,
    'related_staff': staff,
  };
}

Map<String, dynamic> page(
  List<Object> rows, {
  String? next,
  int patient = 7,
  int clinic = 3,
  String org = f.tenant,
  Set<String> available = timelineCategories,
  bool release = true,
}) => {
  'patient_id': patient,
  'clinic_id': clinic,
  'organization_id': org,
  'items': rows,
  'next_cursor': next,
  'available_categories': available.toList(),
  'source_limitations': timelineLimitations.toList(),
  'lab_release_events_enabled': release,
};

class Harness {
  Harness(MockClient client, {AuthSessionState? auth})
    : state = auth ?? f.ready() {
    source = TimelineSource(
      apiClient: f.apiFor(client),
      loadSession: () async => state,
      guard: guard,
    );
    addTearDown(guard.dispose);
    addTearDown(client.close);
  }
  AuthSessionState state;
  final guard = SessionNavigationGuard()..markSessionAvailable();
  late final TimelineSource source;
}

void main() {
  test(
    'verified route, bearer, filters and chronological tie ordering',
    () async {
      final h = Harness(
        MockClient((r) async {
          expect(r.method, 'GET');
          expect(r.url.path, timelineRoot);
          expect(r.headers['authorization'], 'Bearer bound-token');
          expect(r.headers.containsKey('x-emergency-access-id'), false);
          expect(r.url.queryParameters, {
            'limit': '2',
            'categories': 'consultations',
            'start': '2026-11-01T00:00:00.000Z',
            'end': '2026-11-02T00:00:00.000Z',
          });
          return f.ok(
            page([
              event(row: 12, sourceId: 3),
              event(row: 9, sourceId: 2),
            ], next: 'opaque-token'),
          );
        }),
      );
      final session = await h.source.open(7, 3);
      final p = await session.read(
        TimelineFilters(
          category: 'consultations',
          start: DateTime.utc(2026, 11),
          end: DateTime.utc(2026, 11, 2),
          limit: 2,
        ),
      );
      expect(p.items.map((e) => e.rowId), [12, 9]);
      expect(p.cursor, 'opaque-token');
      expect(p.items.first.route(7), '/consultations/patient/7/3');
      expect(p.items.first.exactRoute, true);
    },
  );
  test('opaque continuation is carried unchanged and ordering verified across pages', () async {
    var count = 0;
    final h = Harness(
      MockClient((r) async {
        count++;
        if (count == 1) {
          expect(r.url.queryParameters.containsKey('cursor'), false);
          return f.ok(
            page([
              event(row: 12),
              event(row: 9),
            ], next: 'signed.continuation-1'),
          );
        }
        expect(r.url.queryParameters['cursor'], 'signed.continuation-1');
        return f.ok(page([event(row: 7)]));
      }),
    );
    final session = await h.source.open(7, 3);
    final filter = TimelineFilters(limit: 2);
    final first = await session.read(filter);
    final second = await session.read(filter, previous: first);
    expect(second.items.single.rowId, 7);
    expect(second.cursor, isNull);
    expect(count, 2);
  });
  test(
    'continuation cannot be reused with another filter or another session',
    () async {
      var count = 0;
      final h = Harness(
        MockClient((_) async {
          count++;
          return f.ok(page([event()], next: 'token'));
        }),
      );
      final s = await h.source.open(7, 3);
      final filter = TimelineFilters(limit: 1);
      final first = await s.read(filter);
      await expectLater(
        s.read(
          TimelineFilters(limit: 1, category: 'consultations'),
          previous: first,
        ),
        throwsFormatException,
      );
      final other = await h.source.open(7, 3);
      await expectLater(
        other.read(filter, previous: first),
        throwsFormatException,
      );
      expect(count, 1);
    },
  );
  for (final role in [
    'admin',
    'super_admin',
    'patient',
    'pharmacist',
    'lab_technician',
    'receptionist',
    'unknown',
  ]) {
    test('$role cannot read timeline', () async {
      final h = Harness(
        MockClient((_) async => throw StateError('Unexpected transport')),
        auth: f.ready(role: role),
      );
      await expectLater(
        h.source.open(7, 3),
        throwsA(isA<TimelineUnavailable>()),
      );
    });
  }
  test(
    'unbound, foreign clinic and invalidated session denied before transport',
    () async {
      final h = Harness(
        MockClient((_) async => throw StateError('Unexpected transport')),
        auth: f.ready(organization: null),
      );
      await expectLater(
        h.source.open(7, 3),
        throwsA(isA<TimelineUnavailable>()),
      );
      h.state = f.ready(clinic: 4);
      await expectLater(
        h.source.open(7, 3),
        throwsA(isA<TimelineUnavailable>()),
      );
      h.state = f.ready();
      final s = await h.source.open(7, 3);
      h.guard.invalidate();
      await expectLater(
        s.read(TimelineFilters()),
        throwsA(isA<TimelineUnavailable>()),
      );
    },
  );
  for (final entry in <String, Map<String, dynamic>>{
    'wrong patient': page([], patient: 8),
    'wrong clinic': page([], clinic: 4),
    'wrong organization': page([], org: f.foreignTenant),
    'unknown event': page([event()..['event_type'] = 'invented_check_in']),
    'wrong phase': page([event()..['phase'] = 'completed']),
    'foreign kind': page([
      event()..['source'] = {'kind': 'prescription', 'id': 9},
    ]),
    'invalid timestamp': page([event(date: '2026-11-31T09:00:00Z')]),
    'no timezone': page([event(date: '2026-11-01T09:00:00')]),
    'duplicates': page([event(), event()]),
    'wrong ordering': page([event(row: 7), event(row: 9)]),
    'unexpected cursor': page([], next: 'token'),
    'missing amendment version': page([
      event(type: 'problem_review_or_change_recorded'),
    ]),
    'unavailable category': page([event()], available: {'vitals'}),
    'release held': page([
      event(type: 'lab_patient_release_recorded'),
    ], release: false),
  }.entries) {
    test('reject ${entry.key}', () async {
      final h = Harness(MockClient((_) async => f.ok(entry.value)));
      final s = await h.source.open(7, 3);
      await expectLater(s.read(TimelineFilters()), throwsFormatException);
    });
  }
  test('nurse cannot consume prescription or lab release visibility', () async {
    final h = Harness(
      MockClient((_) async => f.ok(page([event(type: 'prescription_issued')]))),
      auth: f.ready(role: 'nurse'),
    );
    final s = await h.source.open(7, 3);
    await expectLater(s.read(TimelineFilters()), throwsFormatException);
  });
  test('nurse receives only authorized documented events and safe source references', () async {
    final allowed = timelineCategories.difference({'prescriptions'});
    final h = Harness(
      MockClient(
        (_) async => f.ok(
          page(
            [
              event(
                type: 'vitals_recorded',
                staff: {
                  'id': 2,
                  'name': 'Synthetic clinician',
                  'current_department': 'Ward',
                },
              ),
            ],
            available: allowed,
            release: false,
          ),
        ),
      ),
      auth: f.ready(role: 'nurse'),
    );
    final s = await h.source.open(7, 3);
    final p = await s.read(TimelineFilters());
    expect(p.items.single.staff?.department, 'Ward');
    expect(p.items.single.route(7), isNull);
    expect(p.labReleaseEnabled, false);
  });
  test(
    'out-of-filter event is rejected rather than silently discarded',
    () async {
      final h = Harness(MockClient((_) async => f.ok(page([event()]))));
      final s = await h.source.open(7, 3);
      await expectLater(
        s.read(TimelineFilters(category: 'vitals')),
        throwsFormatException,
      );
      await expectLater(
        s.read(TimelineFilters(end: DateTime.utc(2026, 11))),
        throwsFormatException,
      );
    },
  );
  test('late response after session or token change is rejected', () async {
    final sent = Completer<void>();
    final response = Completer<Map<String, dynamic>>();
    final h = Harness(
      MockClient((_) async {
        sent.complete();
        return f.ok(await response.future);
      }),
    );
    final s = await h.source.open(7, 3);
    final result = s.read(TimelineFilters());
    final check = expectLater(result, throwsA(isA<TimelineUnavailable>()));
    await sent.future;
    h.state = f.ready(token: 'replacement');
    response.complete(page([event()]));
    await check;
  });
  test('invalid filter ranges and unknown categories rejected locally', () {
    expect(() => TimelineFilters(limit: 0), throwsFormatException);
    expect(() => TimelineFilters(category: 'audit'), throwsFormatException);
    expect(
      () => TimelineFilters(start: DateTime.utc(2026), end: DateTime.utc(2025)),
      throwsFormatException,
    );
  });
}
