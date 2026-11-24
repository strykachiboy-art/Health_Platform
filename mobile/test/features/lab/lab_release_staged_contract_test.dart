import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/lab/data/lab_release_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/presentation/lab_release_workspace_screen.dart';

const stamp = '2026-11-24T10:00:00+00:00';
const digest =
    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
AuthSessionState session(String role, {int clinic = 4, int user = 8}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'test-access',
        refreshToken: 'test-refresh',
        userId: user,
        role: role,
        clinicContextId: clinic,
      ),
    );
Map<String, dynamic> report({
  int clinic = 4,
  int id = 9,
  int version = 1,
  String flag = 'normal',
  bool published = false,
}) => {
  'order_id': id,
  'clinic_id': clinic,
  'patient_id': 2,
  'verified_at': stamp,
  'completed_at': stamp,
  if (version > 1) ...{
    'record_version': version,
    'correction_verified_at': stamp,
  },
  if (published) ...{'release_status': 'released', 'released_at': stamp},
  'results': [
    {
      'test_name': 'Synthetic test',
      'loinc_code': null,
      'unit': 'mg/dL',
      'reference_range': '1 - 10',
      'result_value': '5',
      'flag': flag,
      'resulted_at': stamp,
    },
  ],
};
Map<String, dynamic> preview({
  int version = 1,
  String flag = 'normal',
  int clinic = 4,
}) => {
  'report': report(version: version, flag: flag, clinic: clinic),
  'preview_sha256': digest,
  if (flag == 'critical') 'requires_direct_communication': true,
};
Map<String, dynamic> status({
  String state = 'unreleased',
  int version = 0,
  bool pending = false,
  bool rejected = false,
}) => {
  'order_id': 9,
  'release_status': state,
  'record_version': version,
  'correction_pending': pending,
  if (rejected) 'correction_rejected': true,
  'released_at': state == 'unreleased' ? null : stamp,
  'withdrawn_at': state == 'withdrawn' ? stamp : null,
};
Map<String, dynamic> page({int id = 9}) => {
  'items': [
    {
      'order_id': id,
      'release_status': 'released',
      'record_version': 1,
      'completed_at': stamp,
      'released_at': stamp,
      'test_count': 1,
    },
  ],
  'page': 1,
  'per_page': 20,
  'total': 1,
  'pages': 1,
  'has_next': false,
};
http.Response receipt(Object data) => http.Response(
  jsonEncode({'success': true, 'data': data}),
  200,
  headers: {'content-type': 'application/json'},
);
SessionNavigationGuard ready() {
  final guard = SessionNavigationGuard()..markSessionAvailable();
  addTearDown(guard.dispose);
  return guard;
}

LabReleaseRemoteDataSource source(
  SessionNavigationGuard guard,
  MockClient client,
  Future<AuthSessionState> Function() load,
) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  addTearDown(api.close);
  addTearDown(client.close);
  return LabReleaseRemoteDataSource(
    apiClient: api,
    loadSession: load,
    guard: guard,
  );
}

void main() {
  test('all other roles and missing clinic denied before HTTP', () async {
    final g = ready();
    var role = 'admin', clinic = 4, calls = 0;
    final ds = source(
      g,
      MockClient((r) async {
        calls++;
        return receipt({});
      }),
      () async => session(role, clinic: clinic),
    );
    for (final x in [
      'admin',
      'super_admin',
      'nurse',
      'pharmacist',
      'receptionist',
      'accountant',
      'paramedic',
      'emt',
      'ambulance_dispatcher',
      'ambulance_coordinator',
      'driver',
      'other',
    ]) {
      role = x;
      await expectLater(ds.connect(), throwsA(isA<LabReleaseUnavailable>()));
    }
    role = 'doctor';
    clinic = 0;
    await expectLater(ds.connect(), throwsA(isA<LabReleaseUnavailable>()));
    expect(calls, 0);
  });
  test('normal exact digest release once, no idempotency or replay', () async {
    final g = ready();
    var calls = 0;
    final ds = source(
      g,
      MockClient((r) async {
        calls++;
        expect(r.headers['authorization'], 'Bearer test-access');
        if (r.method == 'GET') {
          expect(r.url.path, '/api/v1/lab/orders/9/patient-release-preview');
          return receipt(preview());
        }
        expect(r.url.path, '/api/v1/lab/orders/9/patient-release');
        expect(jsonDecode(r.body), {'preview_sha256': digest});
        expect(r.headers.containsKey('idempotency-key'), isFalse);
        return receipt({
          'order_id': 9,
          'release_status': 'released',
          'released_at': stamp,
        });
      }),
      () async => session('doctor'),
    );
    final s = await ds.connect(),
        p = await (await ds.connect()).preview(9, LabPreviewKind.initial);
    await expectLater(
      s.publish(p, confirmed: true),
      throwsA(isA<LabReleaseUnavailable>()),
    );
    final own = await s.preview(9, LabPreviewKind.initial);
    await expectLater(
      s.publish(own, confirmed: false),
      throwsA(isA<LabReleaseUnavailable>()),
    );
    await s.publish(own, confirmed: true);
    await expectLater(
      s.publish(own, confirmed: true),
      throwsA(isA<LabReleaseUnavailable>()),
    );
    expect(calls, 3);
  });
  test('critical communication is explicit and uses separate route', () async {
    final g = ready();
    var posts = 0;
    final ds = source(
      g,
      MockClient((r) async {
        if (r.method == 'GET') {
          expect(
            r.url.path.endsWith('/patient-critical-release-preview'),
            isTrue,
          );
          return receipt(preview(flag: 'critical'));
        }
        posts++;
        expect(r.url.path.endsWith('/patient-critical-release'), isTrue);
        expect(jsonDecode(r.body), {
          'preview_sha256': digest,
          'communication_confirmed': true,
          'communication_method': 'telephone',
        });
        return receipt({
          'order_id': 9,
          'release_status': 'released',
          'released_at': stamp,
        });
      }),
      () async => session('doctor'),
    );
    final s = await ds.connect();
    final p = await s.preview(9, LabPreviewKind.critical);
    await expectLater(
      s.publish(p, confirmed: true),
      throwsA(isA<LabReleaseUnavailable>()),
    );
    await expectLater(
      s.publish(p, confirmed: true, communicationMethod: 'notification'),
      throwsA(isA<LabReleaseUnavailable>()),
    );
    await s.publish(p, confirmed: true, communicationMethod: 'telephone');
    expect(posts, 1);
  });
  test('critical preview cannot masquerade as ordinary report', () async {
    final ds = source(
      ready(),
      MockClient((r) async => receipt(preview(flag: 'critical'))),
      () async => session('doctor'),
    );
    final s = await ds.connect();
    await expectLater(
      s.preview(9, LabPreviewKind.initial),
      throwsFormatException,
    );
  });
  test('replacement publication and rejection exact payloads', () async {
    final g = ready();
    final paths = <String>[];
    final ds = source(
      g,
      MockClient((r) async {
        if (r.method == 'GET') {
          return receipt(preview(version: 2));
        }
        paths.add(r.url.path);
        if (r.url.path.endsWith('-rejection')) {
          expect(jsonDecode(r.body), {
            'preview_sha256': digest,
            'reason_code': 'incorrect_correction',
          });
          return receipt({
            'order_id': 9,
            'record_version': 2,
            'release_status': 'withdrawn',
          });
        }
        expect(jsonDecode(r.body), {'preview_sha256': digest});
        return receipt({
          'order_id': 9,
          'record_version': 2,
          'release_status': 'released',
          'released_at': stamp,
        });
      }),
      () async => session('doctor'),
    );
    final s = await ds.connect();
    await s.reject(
      await s.preview(9, LabPreviewKind.replacement),
      'incorrect_correction',
      confirmed: true,
    );
    await s.publish(
      await s.preview(9, LabPreviewKind.replacement),
      confirmed: true,
    );
    expect(paths, [
      '/api/v1/lab/orders/9/patient-replacement-rejection',
      '/api/v1/lab/orders/9/patient-replacement',
    ]);
  });
  test('technician uses canonical order items and expected version, no release rights', () async {
    final g = ready();
    var posts = 0;
    final ds = source(
      g,
      MockClient((r) async {
        if (r.method == 'POST') {
          posts++;
          expect(r.url.path.endsWith('/patient-release-correction'), isTrue);
          expect(jsonDecode(r.body), {
            'expected_version': 2,
            'changes': [
              {'item_id': 17, 'result_value': '6', 'flag': 'normal'},
            ],
            'reason': 'Verified correction',
          });
          return receipt({
            'order_id': 9,
            'record_version': 3,
            'release_status': 'correction_pending',
          });
        }
        if (r.url.path.endsWith('/patient-release')) {
          return receipt(
            status(state: 'withdrawn', version: 2, rejected: true),
          );
        }
        expect(r.url.path, '/api/v1/lab/orders/9');
        return receipt({
          'id': 9,
          'clinic_id': 4,
          'patient_id': 2,
          'status': 'completed',
          'items': [
            {
              'id': 17,
              'order_id': 9,
              'test_id': 3,
              'test': {'id': 3, 'clinic_id': null, 'name': 'Synthetic test'},
              'result_value': '5',
            },
          ],
        });
      }),
      () async => session('lab_technician'),
    );
    final s = await ds.connect();
    final st = await s.status(9);
    expect(st.rejected, isTrue);
    await expectLater(
      s.preview(9, LabPreviewKind.initial),
      throwsA(isA<LabReleaseUnavailable>()),
    );
    await expectLater(
      s.correct(
        st,
        [const LabCorrection(itemId: 99, value: '6', flag: 'normal')],
        'Verified correction',
        confirmed: true,
      ),
      throwsA(isA<LabReleaseUnavailable>()),
    );
    await s.correct(
      st,
      [const LabCorrection(itemId: 17, value: '6', flag: 'normal')],
      'Verified correction',
      confirmed: true,
    );
    expect(posts, 1);
  });
  test('withdrawal refreshes status and refuses changed version', () async {
    final g = ready();
    var reads = 0, posts = 0;
    final ds = source(
      g,
      MockClient((r) async {
        if (r.method == 'POST') {
          posts++;
        }
        reads++;
        return receipt(status(state: 'released', version: reads));
      }),
      () async => session('doctor'),
    );
    final s = await ds.connect();
    final st = await s.status(9);
    await expectLater(
      s.withdraw(st, 'clinical_correction', confirmed: true),
      throwsA(isA<LabReleaseUnavailable>()),
    );
    expect(posts, 0);
  });
  test('patient list and detail use only self endpoints', () async {
    final g = ready();
    var calls = 0;
    final ds = source(
      g,
      MockClient((r) async {
        calls++;
        expect(r.method, 'GET');
        if (r.url.query.isNotEmpty) {
          expect(r.url.path, '/api/v1/lab/me/released-results');
          expect(r.url.queryParameters, {'page': '1'});
          return receipt(page());
        }
        expect(r.url.path, '/api/v1/lab/me/released-results/9');
        return receipt(report(published: true));
      }),
      () async => session('patient'),
    );
    final s = await ds.connect();
    expect((await s.patientPage(1)).items.single.orderId, 9);
    expect((await s.patientDetail(9)).results.single['unit'], 'mg/dL');
    await expectLater(s.status(9), throwsA(isA<LabReleaseUnavailable>()));
    expect(calls, 2);
  });
  test(
    'foreign clinic, wrong record, bad flags and withdrawn report rejected',
    () {
      for (final r in [
        report(clinic: 5, published: true),
        report(id: 8, published: true),
        report(flag: 'unknown', published: true),
        {...report(published: true), 'release_status': 'withdrawn'},
        report(version: 2, flag: 'critical', published: true),
      ]) {
        expect(
          () => LabReleasedReport.parse(
            r,
            orderId: 9,
            clinicId: 4,
            published: true,
          ),
          throwsFormatException,
        );
      }
    },
  );
  test('session change during response discards clinical data', () async {
    final g = ready();
    var clinic = 4;
    final ds = source(
      g,
      MockClient((r) async {
        clinic = 5;
        return receipt(report(published: true));
      }),
      () async => session('patient', clinic: clinic),
    );
    final s = await ds.connect();
    await expectLater(
      s.patientDetail(9),
      throwsA(isA<LabReleaseUnavailable>()),
    );
  });
  test('uncertain receipt holds every subsequent mutation', () async {
    final g = ready();
    var posts = 0;
    final ds = source(
      g,
      MockClient((r) async {
        if (r.method == 'GET') {
          return receipt(preview());
        }
        posts++;
        return receipt({
          'order_id': 999,
          'release_status': 'released',
          'released_at': stamp,
        });
      }),
      () async => session('doctor'),
    );
    final s = await ds.connect();
    await expectLater(
      s.publish(await s.preview(9, LabPreviewKind.initial), confirmed: true),
      throwsA(isA<LabReleaseUncertain>()),
    );
    await expectLater(
      s.publish(await s.preview(9, LabPreviewKind.initial), confirmed: true),
      throwsA(isA<LabReleaseUncertain>()),
    );
    expect(posts, 1);
  });
  test('backend hold does not fall back to staff endpoint or retry', () async {
    final g = ready();
    var calls = 0;
    final ds = source(
      g,
      MockClient((r) async {
        calls++;
        expect(r.url.path, '/api/v1/lab/me/released-results');
        return http.Response(
          '{"error":"held","code":"PATIENT_LAB_RELEASE_DISABLED"}',
          503,
          headers: {'content-type': 'application/json'},
        );
      }),
      () async => session('patient'),
    );
    final s = await ds.connect();
    await expectLater(s.patientPage(1), throwsA(isA<ApiException>()));
    expect(calls, 1);
  });
  test(
    'withdrawal sends only the approved reason after current status check',
    () async {
      final g = ready();
      var reads = 0, posts = 0;
      final ds = source(
        g,
        MockClient((r) async {
          if (r.method == 'GET') {
            reads++;
            return receipt(status(state: 'released', version: 2));
          }
          posts++;
          expect(r.url.path.endsWith('/patient-release-withdrawal'), isTrue);
          expect(jsonDecode(r.body), {'reason_code': 'identity_review'});
          return receipt({
            'order_id': 9,
            'record_version': 2,
            'release_status': 'withdrawn',
          });
        }),
        () async => session('doctor'),
      );
      final s = await ds.connect();
      final st = await s.status(9);
      await s.withdraw(st, 'identity_review', confirmed: true);
      expect(reads, 2);
      expect(posts, 1);
    },
  );
  test(
    'patient pagination cannot invent or expose duplicate reports',
    () async {
      final data = page();
      data['items'] = [...(data['items'] as List), ...(data['items'] as List)];
      data['total'] = 2;
      final ds = source(
        ready(),
        MockClient((r) async => receipt(data)),
        () async => session('patient'),
      );
      final s = await ds.connect();
      await expectLater(s.patientPage(1), throwsFormatException);
    },
  );
  testWidgets(
    'patient detail rendered, PDF held, session invalidation clears data',
    (tester) async {
      final g = ready();
      final ds = source(
        g,
        MockClient((r) async => receipt(report(published: true))),
        () async => session('patient'),
      );
      await tester.pumpWidget(
        MaterialApp(
          home: LabReleaseWorkspaceScreen(source: ds, initialOrderId: 9),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('My Lab Results'), findsOneWidget);
      expect(find.text('Synthetic test'), findsOneWidget);
      final b = tester.widget<OutlinedButton>(
        find.widgetWithText(OutlinedButton, 'Released PDF copy (held)'),
      );
      expect(b.onPressed, isNull);
      g.invalidate();
      await tester.pumpAndSettle();
      expect(find.text('Synthetic test'), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );
  testWidgets('technician cannot see doctor release actions', (tester) async {
    final g = ready();
    final ds = source(
      g,
      MockClient((r) async => receipt(status())),
      () async => session('lab_technician'),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: LabReleaseWorkspaceScreen(source: ds, initialOrderId: 9),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Order 9 · unreleased · Version 0'), findsOneWidget);
    expect(find.text('Review initial report'), findsNothing);
    expect(find.text('Authorize patient release'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
