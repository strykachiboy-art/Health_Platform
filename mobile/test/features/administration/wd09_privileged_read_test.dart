import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/administration/data/wd09_privileged_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/administration/presentation/wd09_privileged_read_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _ready(
  String role, {
  int? clinic = 7,
  String token = 'wd09-test',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'wd09-refresh',
    userId: 55,
    role: role,
    clinicContextId: clinic,
  ),
);

Wd09PrivilegedReadRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard,
  Future<AuthSessionState> Function() session,
) => Wd09PrivilegedReadRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: session,
  sessionNavigationGuard: guard,
);

Map<String, dynamic> _audit({int? clinic = 7}) => {
  'id': 11,
  'clinic_id': clinic,
  'user_id': 5,
  'patient_id': null,
  'action': 'create',
  'entity_type': 'patient',
  'entity_id': 22,
  'created_at': '2026-11-22T08:00:00Z',
  'description': 'Record created',
  'reason': null,
  'operation_id': null,
  'old_value': {'confidential': 'DO_NOT_SHOW'},
  'new_value': {'secret': 'DO_NOT_SHOW'},
};

Map<String, dynamic> _user() => {
  'id': 8,
  'email': 'staff@example.com',
  'role': 'doctor',
  'is_active': true,
  'clinic_id': 7,
};

http.Response _page(Wd09ReadKind kind, {int? auditClinic = 7}) => http.Response(
  jsonEncode({
    'success': true,
    'data': kind == Wd09ReadKind.auditLogs
        ? {
            'items': [_audit(clinic: auditClinic)],
            'page': 1,
            'per_page': 20,
            'total': 1,
            'pages': 1,
            'has_next': false,
          }
        : {
            'items': [_user()],
            'pagination': {'page': 1, 'per_page': 20, 'total': 1, 'pages': 1},
          },
  }),
  200,
);

void main() {
  test(
    'WD09 real Flask paths and bearer read for Admin audit and SA users',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      for (final kind in Wd09ReadKind.values) {
        final client = MockClient((request) async {
          expect(request.method, 'GET');
          expect(
            request.url.path,
            kind == Wd09ReadKind.auditLogs
                ? '/api/v1/audit-logs'
                : '/api/v1/access-control/users',
          );
          expect(request.url.queryParameters, containsPair('page', '1'));
          expect(request.url.queryParameters, containsPair('per_page', '20'));
          expect(request.url.queryParameters, isNot(contains('clinic_id')));
          expect(request.headers['authorization'], 'Bearer wd09-test');
          return _page(kind);
        });
        addTearDown(client.close);
        final source = _source(
          client,
          guard,
          () async => _ready(
            kind == Wd09ReadKind.auditLogs ? 'admin' : 'super_admin',
            clinic: kind == Wd09ReadKind.auditLogs ? 7 : null,
          ),
        );
        final data = await source.loadPage(kind: kind);
        expect(data.items, hasLength(1));
        expect(data.total, 1);
        expect(data.hasNext, isFalse);
        expect(data.items.first.details, isNot(contains('DO_NOT_SHOW')));
      }
    },
  );

  test(
    'WD09 super admin reads system-wide audit with no selected clinic',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final client = MockClient(
        (request) async => _page(Wd09ReadKind.auditLogs, auditClinic: null),
      );
      addTearDown(client.close);
      final data = await _source(
        client,
        guard,
        () async => _ready('super_admin', clinic: null),
      ).loadPage(kind: Wd09ReadKind.auditLogs);
      expect(data.items.single.subtitle, contains('system'));
    },
  );

  test(
    'WD09 unauthorized roles and Admin without clinic produce zero HTTP',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return _page(Wd09ReadKind.auditLogs);
      });
      addTearDown(client.close);
      for (final role in <String>[
        'doctor',
        'nurse',
        'patient',
        'pharmacist',
        'accountant',
        'receptionist',
        'lab_technician',
        'paramedic',
        'emt',
        'driver',
        'ambulance_dispatcher',
        'ambulance_coordinator',
        'other',
      ]) {
        for (final kind in Wd09ReadKind.values) {
          await expectLater(
            _source(
              client,
              guard,
              () async => _ready(role),
            ).loadPage(kind: kind),
            throwsA(isA<Wd09ReadUnavailable>()),
          );
        }
      }
      await expectLater(
        _source(
          client,
          guard,
          () async => _ready('admin'),
        ).loadPage(kind: Wd09ReadKind.users),
        throwsA(isA<Wd09ReadUnavailable>()),
      );
      await expectLater(
        _source(
          client,
          guard,
          () async => _ready('admin', clinic: null),
        ).loadPage(kind: Wd09ReadKind.auditLogs),
        throwsA(isA<Wd09ReadUnavailable>()),
      );
      guard.invalidate();
      await expectLater(
        _source(
          client,
          guard,
          () async => _ready('super_admin'),
        ).loadPage(kind: Wd09ReadKind.users),
        throwsA(isA<Wd09ReadUnavailable>()),
      );
      expect(calls, 0);
    },
  );

  test(
    'WD09 rejects an other-clinic audit row even if HTTP returned 200',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final client = MockClient(
        (request) async => _page(Wd09ReadKind.auditLogs, auditClinic: 8),
      );
      addTearDown(client.close);
      await expectLater(
        _source(
          client,
          guard,
          () async => _ready('admin'),
        ).loadPage(kind: Wd09ReadKind.auditLogs),
        throwsA(isA<FormatException>()),
      );
    },
  );

  test(
    'WD09 session switch during an in-flight request never publishes data',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final requested = Completer<void>();
      final pendingResponse = Completer<http.Response>();
      final client = MockClient((request) {
        requested.complete();
        return pendingResponse.future;
      });
      addTearDown(client.close);
      var session = _ready('admin');
      final future = _source(
        client,
        guard,
        () async => session,
      ).loadPage(kind: Wd09ReadKind.auditLogs);
      await requested.future;
      session = _ready('admin', clinic: 8);
      pendingResponse.complete(_page(Wd09ReadKind.auditLogs));
      await expectLater(future, throwsA(isA<Wd09ReadUnavailable>()));
    },
  );

  test('WD09 rejects detail identity mismatch', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient(
      (request) async =>
          http.Response(jsonEncode({'success': true, 'data': _user()}), 200),
    );
    addTearDown(client.close);
    await expectLater(
      _source(
        client,
        guard,
        () async => _ready('super_admin', clinic: null),
      ).loadDetail(kind: Wd09ReadKind.users, id: 19),
      throwsA(isA<FormatException>()),
    );
  });

  testWidgets('WD09 audit screen shows verified rows and clears on logout', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final client = MockClient((request) async => _page(Wd09ReadKind.auditLogs));
    addTearDown(client.close);
    final source = _source(client, guard, () async => _ready('admin'));
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Wd09PrivilegedReadScreen(
            kind: Wd09ReadKind.auditLogs,
            source: source,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('wd09-audit-workspace')), findsOneWidget);
    expect(find.byKey(const ValueKey('wd09-item-11')), findsOneWidget);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('wd09-item-11')), findsNothing);
  });
}
