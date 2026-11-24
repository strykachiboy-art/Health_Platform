import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:clinic_system_pro/features/notifications/data/notification_remote_data_source.dart';
import 'package:clinic_system_pro/features/radiology/data/radc_staged_remote_data_source.dart';
import 'package:clinic_system_pro/features/radiology/presentation/radc_workspace_screen.dart';

import '../lab/lab_release_staged_contract_test.dart' as f;
import '../lab/lab_release_navigation_test.dart' as nav;

const root = '/api/v1/radiology';
const reportRoot = '$root/exams/9/staged-report/12';
const sample = 'SYNTHETIC test report';
const revised = 'SYNTHETIC corrected report';

class Net implements DeviceConnectivitySource {
  DeviceNetworkStatus value = DeviceNetworkStatus.connected;
  @override
  Future<DeviceNetworkStatus> checkStatus() async => value;
  @override
  Stream<DeviceNetworkStatus> get statusChanges =>
      const Stream<DeviceNetworkStatus>.empty();
}

RadcSource source(
  SessionNavigationGuard g,
  MockClient client,
  Future<AuthSessionState> Function() load, {
  Net? net,
}) => RadcSource(
  apiClient: nav.apiFor(client),
  guard: g,
  loadSession: load,
  connectivity: net ?? Net(),
);
http.Response jsonResponse(Object value) => http.Response(
  jsonEncode(value),
  200,
  headers: {'content-type': 'application/json'},
);
http.Response staged(Map<String, dynamic> d) => jsonResponse({
  'success': true,
  'synthetic_only': true,
  'diagnostic_report_signed': false,
  'released_to_chart': false,
  'data': d,
});
http.Response ris(Map<String, dynamic> d) => jsonResponse({
  'success': true,
  'ris_foundation_only': true,
  'mwl_published': false,
  'archive_verified': false,
  'diagnostic_report_signed': false,
  'released_to_chart': false,
  'critical_result_delivery_authorized': false,
  'data': d,
});
Map<String, dynamic> work({bool published = false}) => {
  'exam_id': 9,
  'exam_version': 2,
  'order_version': 1,
  'local_state': published ? 'local_published' : 'not_published',
  'publication_id': published ? 12 : null,
  'published_exam_version': published ? 2 : null,
  'payload_sha256': published ? f.digest : null,
  'external_publication_confirmed': false,
  'external_delivery': 'held',
  'archive_verified': false,
  'diagnostic_report_signed': false,
};
Map<String, dynamic> report({
  int e = 9,
  int version = 1,
  bool released = false,
  String text = sample,
  bool patient = false,
}) => {
  'exam_id': e,
  'report_id': 15,
  'record_version': version,
  'report_text': text,
  'modality': 'ct',
  'synthetic_only': true,
  'staged_finalized': true,
  'finalized_at': f.stamp,
  'staged_patient_released': released,
  'diagnostic_report_signed': false,
  'archive_verified': false,
  'released_to_chart': false,
  if (patient) 'released_at': f.stamp,
};
Map<String, dynamic> freezePreview() => {
  'draft_revision': 2,
  'report_text': sample,
  'preview_sha256': f.digest,
  'synthetic_only': true,
  'diagnostic_report_signed': false,
};
Map<String, dynamic> descriptor() => {
  'study_instance_uid': '2.25.123',
  'instance_count': 2,
  'dicomweb_root': '$root/exams/9/staged-ct/12/viewer',
  'authorization': 'bearer_per_request',
  'browser_handoff_authorized': false,
  'diagnostic_use_authorized': false,
};
Map<String, dynamic> patientPage() => {
  'synthetic_only': true,
  'items': [
    {
      'exam_id': 9,
      'record_version': 1,
      'released_at': f.stamp,
      'synthetic_only': true,
    },
  ],
};
Map<String, dynamic> notification({
  String type = 'general',
  String reference = 'staged_radiology_report',
  String channel = 'in_app',
  int user = 8,
}) => nav.notification(
  type: type,
  reference: reference,
  channel: channel,
  user: user,
  target: 9,
);
void main() {
  test('only patient and doctor with active clinic can connect', () async {
    final g = f.ready();
    var role = 'admin', clinic = 4, calls = 0;
    final ds = source(
      g,
      MockClient((r) async {
        calls++;
        return staged({});
      }),
      () async => f.session(role, clinic: clinic),
    );
    for (final r in nav.roles.where((r) => r != 'doctor' && r != 'patient')) {
      role = r;
      await expectLater(ds.connect(), throwsA(isA<RadcUnavailable>()));
    }
    role = 'doctor';
    clinic = 0;
    await expectLater(ds.connect(), throwsA(isA<RadcUnavailable>()));
    expect(calls, 0);
  });
  test('local worklist uses exact version and no external dispatch', () async {
    final g = f.ready();
    var calls = 0;
    final ds = source(
      g,
      MockClient((r) async {
        calls++;
        expect(r.url.path, '$root/exams/9/clinical-worklist');
        expect(r.headers['authorization'], 'Bearer test-access');
        expect(r.headers['idempotency-key'], isNull);
        if (r.method == 'GET') {
          return ris(work());
        }
        expect(r.method, 'POST');
        expect(jsonDecode(r.body), {'expected_version': 2});
        return ris(work(published: true));
      }),
      () async => f.session('doctor'),
    );
    final s = await ds.connect();
    final w = await s.worklist(9);
    final p = await s.publish(w);
    expect(p.publicationId, 12);
    expect(p.current, isTrue);
    await expectLater(s.publish(w), throwsA(isA<RadcUncertain>()));
    expect(calls, 2);
  });
  test('external delivery or wrong identity claims rejected', () async {
    final g = f.ready();
    var d = work();
    final ds = source(
      g,
      MockClient((r) async => ris(d)),
      () async => f.session('doctor'),
    );
    final s = await ds.connect();
    d['external_publication_confirmed'] = true;
    await expectLater(s.worklist(9), throwsFormatException);
    d = work()..['exam_id'] = 99;
    await expectLater(s.worklist(9), throwsFormatException);
  });
  test('offline write denied before transport', () async {
    final g = f.ready(), net = Net()..value = DeviceNetworkStatus.disconnected;
    var calls = 0;
    final ds = source(
      g,
      MockClient((r) async {
        calls++;
        return ris(work());
      }),
      () async => f.session('doctor'),
      net: net,
    );
    final s = await ds.connect();
    final w = await s.worklist(9);
    await expectLater(s.publish(w), throwsA(isA<RadcUnavailable>()));
    expect(calls, 1);
  });
  test('SC and CT are distinct one-shot acquisition contracts', () async {
    final g = f.ready();
    final ds = source(
      g,
      MockClient((r) async {
        expect(r.method, 'POST');
        final ct = r.url.path.endsWith('/staged-ct');
        expect(jsonDecode(r.body), {'publication_id': ct ? 12 : 13});
        return ris({
          'exam_id': 9,
          'publication_id': ct ? 12 : 13,
          'state': 'staged_validated',
          'archive_verified': false,
          'diagnostic_report_signed': false,
          'ohif_browser_authorized': false,
          if (ct) ...{
            'instance_count': 2,
            'study_instance_uid': '2.25.123',
          } else
            'staged_instance_available': true,
        });
      }),
      () async => f.session('doctor'),
    );
    final s = await ds.connect();
    expect(await s.acquire(9, 12, ct: true), 'staged_validated');
    expect(await s.acquire(9, 13, ct: false), 'staged_validated');
  });
  test('quarantine is not viewer permission', () async {
    final g = f.ready();
    final ds = source(
      g,
      MockClient(
        (r) async => ris({
          'exam_id': 9,
          'publication_id': 12,
          'state': 'quarantined',
          'archive_verified': false,
          'diagnostic_report_signed': false,
          'ohif_browser_authorized': false,
          'instance_count': 0,
          'study_instance_uid': null,
        }),
      ),
      () async => f.session('doctor'),
    );
    expect(await (await ds.connect()).acquire(9, 12, ct: true), 'quarantined');
  });
  test('descriptor root and diagnostic boundary are strict', () async {
    final g = f.ready();
    var d = descriptor();
    final ds = source(
      g,
      MockClient((r) async {
        expect(r.url.path, '$root/exams/9/staged-ct/12/viewer/descriptor');
        return jsonResponse(d);
      }),
      () async => f.session('doctor'),
    );
    final s = await ds.connect();
    expect((await s.viewer(9, 12)).instanceCount, 2);
    d = descriptor()..['dicomweb_root'] = 'https://foreign.invalid';
    await expectLater(s.viewer(9, 12), throwsFormatException);
    d = descriptor()..['browser_handoff_authorized'] = true;
    await expectLater(s.viewer(9, 12), throwsFormatException);
  });
  test('freeze preview digest and independent copy approval', () async {
    final g = f.ready();
    var calls = 0;
    final ds = source(
      g,
      MockClient((r) async {
        calls++;
        expect(r.headers['idempotency-key'], isNull);
        if (r.url.path == '$reportRoot/preview') {
          expect(r.method, 'GET');
          return staged(freezePreview());
        }
        if (r.url.path == '$reportRoot/freeze') {
          expect(jsonDecode(r.body), {
            'preview_sha256': f.digest,
            'expected_version': 2,
          });
          return staged(report());
        }
        if (r.url.path == '$reportRoot/copy-preview') {
          return staged({...report(), 'preview_sha256': f.digest});
        }
        expect(r.url.path, '$reportRoot/copy');
        expect(jsonDecode(r.body), {'preview_sha256': f.digest});
        return staged(report(released: true));
      }),
      () async => f.session('doctor'),
    );
    final s = await ds.connect();
    final v = await s.preview(9, 12, copy: false);
    expect(v.text, sample);
    expect((await s.approve(v)).released, isFalse);
    await expectLater(s.approve(v), throwsA(isA<RadcUnavailable>()));
    final p = await s.preview(9, 12, copy: true);
    expect((await s.approve(p)).released, isTrue);
    expect(calls, 4);
  });
  test(
    'active copy cannot amend and withdrawal carries exact version',
    () async {
      final g = f.ready();
      var released = true, calls = 0;
      final ds = source(
        g,
        MockClient((r) async {
          calls++;
          if (r.method == 'GET') {
            return staged(report(released: released));
          }
          if (r.url.path.endsWith('/withdraw')) {
            expect(jsonDecode(r.body), {'expected_version': 1});
            released = false;
            return staged({
              'exam_id': 9,
              'record_version': 1,
              'synthetic_only': true,
              'staged_patient_released': false,
              'diagnostic_report_signed': false,
              'released_to_chart': false,
            });
          }
          expect(r.url.path, '$reportRoot/amend');
          expect(jsonDecode(r.body), {
            'expected_version': 1,
            'report_text': revised,
            'reason': 'Correct test note',
          });
          return staged(report(version: 2, text: revised));
        }),
        () async => f.session('doctor'),
      );
      final s = await ds.connect();
      final r = await s.status(9, 12);
      await expectLater(
        s.amend(r, 12, revised, 'Correct test note'),
        throwsA(isA<RadcUnavailable>()),
      );
      await s.withdraw(9, 12, 1);
      final fresh = await s.status(9, 12);
      expect(
        (await s.amend(fresh, 12, revised, 'Correct test note')).version,
        2,
      );
      expect(calls, 4);
    },
  );
  test('patient can only list and read self copies', () async {
    final g = f.ready();
    var calls = 0;
    final ds = source(
      g,
      MockClient((r) async {
        calls++;
        expect(r.method, 'GET');
        if (r.url.path == '$root/me/staged-reports') {
          return staged(patientPage());
        }
        expect(r.url.path, '$root/me/staged-reports/9');
        return staged(report(released: true, patient: true));
      }),
      () async => f.session('patient'),
    );
    final s = await ds.connect();
    expect((await s.patientList()).single.examId, 9);
    expect((await s.patientDetail(9)).text, sample);
    await expectLater(s.worklist(9), throwsA(isA<RadcUnavailable>()));
    await expectLater(
      s.acquire(9, 12, ct: true),
      throwsA(isA<RadcUnavailable>()),
    );
    expect(calls, 2);
  });
  test(
    'patient rejects mismatched exam, unreleased copy and clinical signature',
    () async {
      final g = f.ready();
      var d = report(e: 99, released: true, patient: true);
      final ds = source(
        g,
        MockClient((r) async => staged(d)),
        () async => f.session('patient'),
      );
      final s = await ds.connect();
      await expectLater(s.patientDetail(9), throwsFormatException);
      d = report(patient: true);
      await expectLater(s.patientDetail(9), throwsFormatException);
      d = report(released: true, patient: true)
        ..['diagnostic_report_signed'] = true;
      await expectLater(s.patientDetail(9), throwsFormatException);
    },
  );
  test('session identity changes discard a pending response', () async {
    final g = f.ready();
    var clinic = 4;
    final started = Completer<void>(), resume = Completer<void>();
    final ds = source(
      g,
      MockClient((r) async {
        started.complete();
        await resume.future;
        return staged(report(released: true, patient: true));
      }),
      () async => f.session('patient', clinic: clinic),
    );
    final s = await ds.connect();
    final pending = s.patientDetail(9);
    await started.future;
    clinic = 5;
    resume.complete();
    await expectLater(pending, throwsA(isA<RadcUnavailable>()));
  });
  test(
    'uncertain POST holds future writes across sessions without replay',
    () async {
      final g = f.ready();
      var posts = 0;
      final ds = source(
        g,
        MockClient((r) async {
          if (r.method == 'GET') {
            return staged(freezePreview());
          }
          posts++;
          return http.Response('unconfirmed', 503);
        }),
        () async => f.session('doctor'),
      );
      final s = await ds.connect();
      await expectLater(
        s.approve(await s.preview(9, 12, copy: false)),
        throwsA(isA<RadcUncertain>()),
      );
      expect(ds.writesHeld, isTrue);
      final next = await ds.connect();
      await expectLater(
        next.approve(await next.preview(9, 12, copy: false)),
        throwsA(isA<RadcUncertain>()),
      );
      expect(posts, 1);
    },
  );
  test('preview cannot cross session owner', () async {
    final g = f.ready();
    var posts = 0;
    final ds = source(
      g,
      MockClient((r) async {
        if (r.method == 'POST') {
          posts++;
        }
        return staged(freezePreview());
      }),
      () async => f.session('doctor'),
    );
    final a = await ds.connect(), b = await ds.connect();
    final p = await a.preview(9, 12, copy: false);
    await expectLater(b.approve(p), throwsA(isA<RadcUnavailable>()));
    expect(posts, 0);
  });
  test(
    'home links only doctor and patient with clinic, lab last unchanged',
    () {
      for (final role in nav.roles) {
        for (final clinic in <int?>[null, 0, 4]) {
          final all = homeDestinationsFor(role: role, clinicContextId: clinic);
          final links = all.where((d) => d.id == 'radc_staged').toList();
          final allowed =
              clinic == 4 && const {'doctor', 'patient'}.contains(role);
          expect(links.length, allowed ? 1 : 0);
          if (allowed) {
            expect(
              links.single.path,
              role == 'patient'
                  ? '/patient-radiology-copies'
                  : '/radiology-staged',
            );
            expect(all.last.id, 'lab_release');
          }
        }
      }
    },
  );
  testWidgets('signed-out staged routes redirect before API', (tester) async {
    final g = SessionNavigationGuard();
    addTearDown(g.dispose);
    var calls = 0;
    final api = nav.apiFor(
      MockClient((r) async {
        calls++;
        return staged({});
      }),
    );
    final router = nav.routerFor(g, api, () async => f.session('patient'));
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    for (final p in [
      '/radiology-staged',
      '/radiology-staged/9',
      '/patient-radiology-copies',
      '/patient-radiology-copies/9',
    ]) {
      router.go(p);
      await tester.pumpAndSettle();
      expect(find.text('Login sentinel'), findsOneWidget);
    }
    expect(calls, 0);
  });
  testWidgets('wrong role and malformed routes cannot read staged records', (
    tester,
  ) async {
    final g = f.ready();
    var calls = 0;
    final api = nav.apiFor(
      MockClient((r) async {
        calls++;
        return staged({});
      }),
    );
    final router = nav.routerFor(g, api, () async => f.session('nurse'));
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/radiology-staged');
    await tester.pumpAndSettle();
    expect(find.text('Select exam scope'), findsNothing);
    router.go('/patient-radiology-copies/9');
    await tester.pumpAndSettle();
    expect(find.text(sample), findsNothing);
    router.go('/patient-radiology-copies/09');
    await tester.pumpAndSettle();
    expect(
      find.text('Staged radiology navigation unavailable.'),
      findsOneWidget,
    );
    expect(calls, 0);
  });
  testWidgets('patient detail clears on guard invalidation', (tester) async {
    final g = f.ready();
    final api = nav.apiFor(
      MockClient((r) async {
        expect(r.url.path, '$root/me/staged-reports/9');
        return staged(report(released: true, patient: true));
      }),
    );
    final router = nav.routerFor(g, api, () async => f.session('patient'));
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/patient-radiology-copies/9');
    await tester.pumpAndSettle();
    expect(find.text(sample), findsOneWidget);
    expect(find.byType(RadcWorkspaceScreen), findsOneWidget);
    g.invalidate();
    await tester.pumpAndSettle();
    expect(find.text(sample), findsNothing);
  });
  test(
    'notification reference requires fresh owner and exact general in-app type',
    () async {
      final g = f.ready();
      var row = notification();
      final api = nav.apiFor(
        MockClient((r) async {
          expect(r.url.path, '/api/v1/notifications/21');
          return f.receipt(row);
        }),
      );
      final ds = NotificationRemoteDataSource(
        apiClient: api,
        loadSession: () async => f.session('patient'),
        sessionNavigationGuard: g,
      );
      expect(await ds.stagedRadiologyTarget(21), 9);
      row = notification(type: 'lab');
      expect(await ds.stagedRadiologyTarget(21), isNull);
      row = notification(reference: 'released_lab_result');
      expect(await ds.stagedRadiologyTarget(21), isNull);
      row = notification(channel: 'push');
      expect(await ds.stagedRadiologyTarget(21), isNull);
    },
  );
  testWidgets('notification rechecks patient release before navigation', (
    tester,
  ) async {
    nav.viewport(tester);
    final g = f.ready();
    final paths = <String>[];
    final api = nav.apiFor(
      MockClient((r) async {
        paths.add(r.url.path);
        expect(r.method, 'GET');
        if (r.url.path == '/api/v1/notifications/21') {
          return f.receipt(notification());
        }
        expect(r.url.path, '$root/me/staged-reports/9');
        return http.Response('{"error":"withdrawn"}', 404);
      }),
    );
    final router = nav.routerFor(g, api, () async => f.session('patient'));
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/notifications/21');
    await tester.pumpAndSettle();
    final button = find.byKey(
      const ValueKey('notification-open-staged-radiology'),
    );
    expect(button, findsOneWidget);
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pumpAndSettle();
    expect(
      find.text('Staged radiology copy unavailable. Refresh detail online.'),
      findsOneWidget,
    );
    expect(find.byType(RadcWorkspaceScreen), findsNothing);
    expect(paths, [
      '/api/v1/notifications/21',
      '/api/v1/notifications/21',
      '$root/me/staged-reports/9',
    ]);
  });
}
