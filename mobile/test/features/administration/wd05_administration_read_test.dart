import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/administration/data/wd05_administration_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/administration/presentation/wd05_administration_read_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _ready({
  String role = 'admin',
  int clinic = 7,
  String token = 'wd05-r03-test-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'wd05-r03-test-refresh',
    userId: 41,
    role: role,
    clinicContextId: clinic,
    tenantId: '11111111-1111-1111-1111-111111111111',
  ),
);

Wd05AdministrationReadRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard,
  Future<AuthSessionState> Function() load,
) => Wd05AdministrationReadRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  sessionNavigationGuard: guard,
  loadSession: load,
);

Map<String, dynamic> _row(
  Wd05AdministrationSection section, {
  int clinic = 7,
}) => switch (section) {
  Wd05AdministrationSection.staff => {
    'id': 12,
    'clinic_id': clinic,
    'first_name': 'Ada',
    'last_name': 'Obi',
    'status': 'active',
    'email': 'never-display@example.invalid',
  },
  Wd05AdministrationSection.departments => {
    'id': 3,
    'clinic_id': clinic,
    'name': 'Laboratory',
    'code': 'LAB',
    'status': 'active',
  },
  Wd05AdministrationSection.inventory => {
    'id': 20,
    'clinic_id': clinic,
    'name': 'Gauze',
    'quantity_on_hand': 9,
    'reorder_level': 5,
  },
  Wd05AdministrationSection.assets => {
    'id': 31,
    'clinic_id': clinic,
    'name': 'Microscope',
    'asset_tag': 'MIC-7',
    'status': 'active',
  },
  Wd05AdministrationSection.settings => {
    'id': 44,
    'clinic_id': clinic,
    'language': 'en',
    'date_format': 'YYYY-MM-DD',
    'time_format': '24h',
    'security_preferences': {'secret': 'never-display'},
  },
};

http.Response _response(Wd05AdministrationSection section, {int clinic = 7}) {
  final data = _row(section, clinic: clinic);
  final json = switch (section) {
    Wd05AdministrationSection.staff => {
      'data': {
        'items': [data],
        'page': 1,
        'per_page': 20,
        'total': 1,
      },
    },
    Wd05AdministrationSection.departments => {
      'data': [data],
    },
    Wd05AdministrationSection.inventory => {
      'success': true,
      'data': {
        'items': [data],
        'page': 1,
        'per_page': 20,
        'total': 1,
      },
    },
    Wd05AdministrationSection.assets => {
      'success': true,
      'items': [data],
      'page': 1,
      'per_page': 20,
      'total': 1,
    },
    Wd05AdministrationSection.settings => {'success': true, 'data': data},
  };
  return http.Response(jsonEncode(json), 200);
}

void main() {
  test(
    'WD05 five worklists GET only real Flask paths with bearer token',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var calls = 0;
      for (final section in Wd05AdministrationSection.values) {
        final client = MockClient((request) async {
          calls++;
          expect(request.method, 'GET');
          expect(request.url.path, '/${section.path}');
          expect(
            request.headers['authorization'],
            'Bearer wd05-r03-test-token',
          );
          expect(request.url.queryParameters.containsKey('clinic_id'), isFalse);
          if (section.paged) {
            expect(request.url.queryParameters['page'], '1');
            expect(request.url.queryParameters['per_page'], '20');
          }
          return _response(section);
        });
        addTearDown(client.close);
        final result = await _source(
          client,
          guard,
          () async => _ready(),
        ).load(section: section);
        expect(result.items, hasLength(1));
        expect(result.total, 1);
        expect(result.items.single.title, isNotEmpty);
        expect(
          result.items.single.description,
          isNot(contains('never-display')),
        );
      }
      expect(calls, 5);
    },
  );

  test(
    'WD05 role/clinic preflight blocks HTTP, including super admin',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return _response(Wd05AdministrationSection.assets);
      });
      addTearDown(client.close);
      for (final session in <AuthSessionState>[
        _ready(role: 'super_admin'),
        _ready(role: 'patient'),
        _ready(role: 'accountant', clinic: 0),
      ]) {
        await expectLater(
          _source(
            client,
            guard,
            () async => session,
          ).load(section: Wd05AdministrationSection.assets),
          throwsA(isA<Wd05AdministrationReadUnavailable>()),
        );
      }
      await expectLater(
        _source(
          client,
          guard,
          () async => _ready(role: 'pharmacist'),
        ).load(section: Wd05AdministrationSection.staff),
        throwsA(isA<Wd05AdministrationReadUnavailable>()),
      );
      guard.invalidate();
      await expectLater(
        _source(
          client,
          guard,
          () async => _ready(),
        ).load(section: Wd05AdministrationSection.assets),
        throwsA(isA<Wd05AdministrationReadUnavailable>()),
      );
      expect(calls, 0);
    },
  );

  test(
    'WD05 all five directories reject forged other-clinic records',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      for (final section in Wd05AdministrationSection.values) {
        final client = MockClient(
          (request) async => _response(section, clinic: 8),
        );
        addTearDown(client.close);
        await expectLater(
          _source(client, guard, () async => _ready()).load(section: section),
          throwsA(isA<FormatException>()),
        );
      }
    },
  );

  test(
    'WD05 session switch before HTTP readback never publishes data',
    () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final response = Completer<http.Response>();
      final requested = Completer<void>();
      final client = MockClient((request) {
        requested.complete();
        return response.future;
      });
      addTearDown(client.close);
      var state = _ready();
      final pending = _source(
        client,
        guard,
        () async => state,
      ).load(section: Wd05AdministrationSection.assets);
      await requested.future;
      state = _ready(clinic: 8);
      response.complete(_response(Wd05AdministrationSection.assets));
      await expectLater(
        pending,
        throwsA(isA<Wd05AdministrationReadUnavailable>()),
      );
    },
  );

  test('WD05 rejects malformed quantity, count, and wrong page', () async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final wrongQty = _row(Wd05AdministrationSection.inventory);
    wrongQty['quantity_on_hand'] = -1;
    var data = {
      'success': true,
      'data': {
        'items': [wrongQty],
        'page': 1,
        'per_page': 20,
        'total': 1,
      },
    };
    final client = MockClient(
      (request) async => http.Response(jsonEncode(data), 200),
    );
    addTearDown(client.close);
    final source = _source(client, guard, () async => _ready());
    await expectLater(
      source.load(section: Wd05AdministrationSection.inventory),
      throwsA(isA<FormatException>()),
    );
    data = {
      'success': true,
      'data': {'items': [], 'page': 2, 'per_page': 20, 'total': 0},
    };
    await expectLater(
      source.load(section: Wd05AdministrationSection.inventory),
      throwsA(isA<FormatException>()),
    );
    await expectLater(
      source.load(section: Wd05AdministrationSection.inventory, perPage: 101),
      throwsA(isA<ArgumentError>()),
    );
  });

  testWidgets('WD05 worklist clears after revocation without replay', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return _response(Wd05AdministrationSection.departments);
    });
    addTearDown(client.close);
    final source = _source(client, guard, () async => _ready());
    await tester.pumpWidget(
      MaterialApp(
        home: Wd05AdministrationReadScreen(
          section: Wd05AdministrationSection.departments,
          source: source,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Laboratory'), findsOneWidget);
    expect(calls, 1);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Laboratory'), findsNothing);
    expect(find.byKey(const ValueKey('wd05-admin-read-error')), findsOneWidget);
    expect(calls, 1);
  });
}
