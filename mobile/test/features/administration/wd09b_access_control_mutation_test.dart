import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/administration/data/wd09b_access_control_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/administration/presentation/wd09b_access_control_mutation_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _ready(
  String role, {
  int actorId = 55,
  String token = 'wd09b-test',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'wd09b-refresh',
    userId: actorId,
    role: role,
    clinicContextId: null,
  ),
);

Map<String, dynamic> _user({
  int id = 8,
  String role = 'doctor',
  bool active = true,
  int? clinic = 7,
}) => {
  'id': id,
  'email': 'staff@example.invalid',
  'role': role,
  'is_active': active,
  'clinic_id': clinic,
};

http.Response _get({
  int id = 8,
  String role = 'doctor',
  bool active = true,
  int? clinic = 7,
}) => http.Response(
  jsonEncode({
    'success': true,
    'data': _user(id: id, role: role, active: active, clinic: clinic),
  }),
  200,
);

http.Response _receipt(
  Wd09bChangeKind kind, {
  String newRole = 'nurse',
  bool newStatus = false,
  int newClinic = 9,
}) {
  final changed = kind == Wd09bChangeKind.role
      ? _user(role: newRole)
      : kind == Wd09bChangeKind.status
      ? _user(active: newStatus)
      : _user(clinic: newClinic);
  return http.Response(
    jsonEncode({
      'success': true,
      'data': {
        'user': changed,
        if (kind == Wd09bChangeKind.role) ...{
          'previous_role': 'doctor',
          'new_role': newRole,
        },
        if (kind == Wd09bChangeKind.status) ...{
          'previous_status': true,
          'new_status': newStatus,
        },
        if (kind == Wd09bChangeKind.clinic) ...{
          'previous_clinic_id': 7,
          'new_clinic_id': newClinic,
        },
        'reason': 'Verified by focused test',
      },
    }),
    200,
  );
}

Wd09bAccessControlMutationRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard,
  Future<AuthSessionState> Function() state,
  Wd09bOneShotGate gate,
) => Wd09bAccessControlMutationRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: state,
  sessionNavigationGuard: guard,
  oneShotGate: gate,
);

void main() {
  test('WD09B SA role/status/clinic use exact Flask PATCH contracts after fresh GET', () async {
    for (final kind in Wd09bChangeKind.values) {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var getCount = 0;
      var patchCount = 0;
      final client = MockClient((request) async {
        expect(request.headers['authorization'], 'Bearer wd09b-test');
        expect(request.url.queryParameters, isEmpty);
        if (request.method == 'GET') {
          expect(request.url.path, '/api/v1/access-control/users/8');
          getCount++;
          return _get();
        }
        expect(request.method, 'PATCH');
        expect(request.url.path, '/api/v1/access-control/users/8/${kind.name}');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        final json = jsonDecode(request.body) as Map<String, dynamic>;
        expect(json['reason'], 'Verified by focused test');
        expect(json.keys, hasLength(2));
        if (kind == Wd09bChangeKind.role) {
          expect(json['role'], 'nurse');
        }
        if (kind == Wd09bChangeKind.status) {
          expect(json['is_active'], false);
        }
        if (kind == Wd09bChangeKind.clinic) {
          expect(json['destination_clinic_id'], 9);
        }
        patchCount++;
        return _receipt(kind);
      });
      addTearDown(client.close);
      final source = _source(
        client,
        guard,
        () async => _ready('super_admin'),
        Wd09bOneShotGate(),
      );
      final before = await source.readUser(8);
      final after = await source.change(
        expected: before,
        kind: kind,
        newRole: kind == Wd09bChangeKind.role ? 'nurse' : null,
        newStatus: kind == Wd09bChangeKind.status ? false : null,
        destinationClinicId: kind == Wd09bChangeKind.clinic ? 9 : null,
        reason: ' Verified by focused test ',
      );
      expect(after.id, before.id);
      expect(after.sameAs(before), isFalse);
      expect(getCount, 2);
      expect(patchCount, 1);
    }
  });

  test('WD09B non-SA, revoked session, self and super-admin target send zero PATCH', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var patches = 0;
    final client = MockClient((request) async {
      if (request.method == 'PATCH') {
        patches++;
      }
      return _get();
    });
    addTearDown(client.close);
    for (final role in <String>['admin', 'doctor', 'patient', 'accountant']) {
      final source = _source(
        client,
        guard,
        () async => _ready(role),
        Wd09bOneShotGate(),
      );
      await expectLater(
        source.readUser(8),
        throwsA(isA<Wd09bAccessUnavailable>()),
      );
    }
    final self = _source(
      client,
      guard,
      () async => _ready('super_admin', actorId: 8),
      Wd09bOneShotGate(),
    );
    await expectLater(
      self.change(
        expected: Wd09bUser.fromJson(_user()),
        kind: Wd09bChangeKind.status,
        newStatus: false,
      ),
      throwsA(isA<Wd09bMutationRejected>()),
    );
    final superTarget = _source(
      client,
      guard,
      () async => _ready('super_admin'),
      Wd09bOneShotGate(),
    );
    await expectLater(
      superTarget.change(
        expected: Wd09bUser.fromJson(_user(role: 'super_admin')),
        kind: Wd09bChangeKind.status,
        newStatus: false,
      ),
      throwsA(isA<Wd09bMutationRejected>()),
    );
    guard.invalidate();
    await expectLater(
      superTarget.readUser(8),
      throwsA(isA<Wd09bAccessUnavailable>()),
    );
    expect(patches, 0);
  });

  test(
    'WD09B prohibits super privilege assignment and patient clinic transfer',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return _get();
      });
      addTearDown(client.close);
      final source = _source(
        client,
        guard,
        () async => _ready('super_admin'),
        Wd09bOneShotGate(),
      );
      await expectLater(
        source.change(
          expected: Wd09bUser.fromJson(_user()),
          kind: Wd09bChangeKind.role,
          newRole: 'super_admin',
        ),
        throwsA(isA<Wd09bMutationRejected>()),
      );
      await expectLater(
        source.change(
          expected: Wd09bUser.fromJson(_user(role: 'patient')),
          kind: Wd09bChangeKind.clinic,
          destinationClinicId: 9,
        ),
        throwsA(isA<Wd09bMutationRejected>()),
      );
      expect(requests, 0);
    },
  );

  test('WD09B rejects stale target after fresh GET without PATCH', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var patches = 0;
    final client = MockClient((request) async {
      if (request.method == 'PATCH') {
        patches++;
      }
      return _get(role: 'nurse');
    });
    addTearDown(client.close);
    final source = _source(
      client,
      guard,
      () async => _ready('super_admin'),
      Wd09bOneShotGate(),
    );
    await expectLater(
      source.change(
        expected: Wd09bUser.fromJson(_user()),
        kind: Wd09bChangeKind.status,
        newStatus: false,
      ),
      throwsA(isA<Wd09bMutationRejected>()),
    );
    expect(patches, 0);
  });

  test(
    'WD09B session change during fresh preflight cannot send PATCH',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var state = _ready('super_admin');
      var patchCount = 0;
      final client = MockClient((request) async {
        if (request.method == 'PATCH') {
          patchCount++;
        }
        state = _ready('admin');
        return _get();
      });
      addTearDown(client.close);
      final source = _source(
        client,
        guard,
        () async => state,
        Wd09bOneShotGate(),
      );
      await expectLater(
        source.change(
          expected: Wd09bUser.fromJson(_user()),
          kind: Wd09bChangeKind.status,
          newStatus: false,
        ),
        throwsA(isA<Wd09bAccessUnavailable>()),
      );
      expect(patchCount, 0);
    },
  );

  test(
    'WD09B unknown PATCH outcome consumes one-shot gate; no auto retry',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var patches = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return _get();
        }
        patches++;
        return http.Response('{"error":"unavailable"}', 503);
      });
      addTearDown(client.close);
      final gate = Wd09bOneShotGate();
      final source = _source(
        client,
        guard,
        () async => _ready('super_admin'),
        gate,
      );
      final user = Wd09bUser.fromJson(_user());
      await expectLater(
        source.change(
          expected: user,
          kind: Wd09bChangeKind.status,
          newStatus: false,
        ),
        throwsA(isA<ApiException>()),
      );
      await expectLater(
        source.change(
          expected: user,
          kind: Wd09bChangeKind.status,
          newStatus: false,
        ),
        throwsA(isA<Wd09bMutationRejected>()),
      );
      expect(patches, 1);
    },
  );

  test(
    'WD09B rejects mismatched mutation receipt, never declares success',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return _get();
        }
        return http.Response(
          jsonEncode({
            'success': true,
            'data': {
              'user': _user(id: 999),
              'previous_status': true,
              'new_status': false,
            },
          }),
          200,
        );
      });
      addTearDown(client.close);
      final source = _source(
        client,
        guard,
        () async => _ready('super_admin'),
        Wd09bOneShotGate(),
      );
      await expectLater(
        source.change(
          expected: Wd09bUser.fromJson(_user()),
          kind: Wd09bChangeKind.status,
          newStatus: false,
        ),
        throwsA(isA<FormatException>()),
      );
    },
  );

  testWidgets('WD09B screen never PATCHes before explicit confirmation', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var patches = 0;
    final client = MockClient((request) async {
      if (request.method == 'PATCH') {
        patches++;
        return _receipt(Wd09bChangeKind.status);
      }
      return _get();
    });
    addTearDown(client.close);
    final source = _source(
      client,
      guard,
      () async => _ready('super_admin'),
      Wd09bOneShotGate(),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Wd09bAccessControlMutationScreen(userId: 8, source: source),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('wd09b-user')), findsOneWidget);
    expect(patches, 0);
    final button = find.byKey(const ValueKey('wd09b-change-status'));
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('wd09b-confirm-change')), findsOneWidget);
    expect(patches, 0);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(patches, 0);
    await tester.tap(button);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('wd09b-confirm-change')));
    await tester.pumpAndSettle();
    expect(patches, 1);
    expect(find.textContaining('Server confirmed the change'), findsOneWidget);
    expect(tester.widget<FilledButton>(button).onPressed, isNull);
  });

  testWidgets('WD09B anonymous direct management link redirects to login', (
    tester,
  ) async {
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    final client = MockClient(
      (request) async => throw StateError('Unexpected HTTP: ${request.url}'),
    );
    addTearDown(client.close);
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
      ),
      loginScreen: const Scaffold(body: Center(child: Text('Login sentinel'))),
      sessionNavigationGuard: guard,
      loadHomeSession: () async => _ready('super_admin'),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/administration/users/8/manage');
    await tester.pumpAndSettle();
    expect(find.text('Login sentinel'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('wd09b-management-workspace')),
      findsNothing,
    );
  });

  testWidgets('WD09B revocation clears user data, never reopens mutation', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final pending = Completer<http.Response>();
    final client = MockClient((request) => pending.future);
    addTearDown(client.close);
    final source = _source(
      client,
      guard,
      () async => _ready('super_admin'),
      Wd09bOneShotGate(),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Wd09bAccessControlMutationScreen(userId: 8, source: source),
        ),
      ),
    );
    guard.invalidate();
    pending.complete(_get());
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('wd09b-user')), findsNothing);
    expect(find.byKey(const ValueKey('wd09b-change-status')), findsNothing);
  });
}
