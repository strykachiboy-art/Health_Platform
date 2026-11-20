import 'dart:async';
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
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_directory_screen.dart';

AuthSessionState _session({
  int userId = 42,
  int clinicId = 3,
  String role = 'doctor',
  String token = 'wd03-access',
  String tenantId = '11111111-1111-1111-1111-111111111111',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'wd03-refresh',
    userId: userId,
    role: role,
    clinicContextId: clinicId,
    tenantId: tenantId,
  ),
);

Map<String, dynamic> _patient({int id = 7, int clinicId = 3}) => {
  'id': id,
  'clinic_id': clinicId,
  'patient_number': 'WD03-0007',
  'first_name': 'Ada',
  'last_name': 'Lovelace',
  'date_of_birth': null,
  'gender': null,
  'blood_type': null,
  'phone': 'private-phone',
  'email': null,
  'address': 'private-address',
  'allergies': 'private-allergies',
  'chronic_conditions': null,
  'emirates_id': null,
  'umrn': null,
  'is_active': true,
  'created_at': '2026-01-01T10:00:00+00:00',
  'updated_at': '2026-01-01T10:00:00+00:00',
};

http.Response _page({List<Map<String, dynamic>>? rows, int page = 1}) =>
    http.Response(
      jsonEncode({
        'success': true,
        'data': {
          'items': rows ?? [_patient()],
          'total': (rows ?? [_patient()]).length,
          'page': page,
          'per_page': 25,
        },
      }),
      200,
    );

http.Response _detail({int id = 7, int clinicId = 3}) => http.Response(
  jsonEncode({'success': true, 'data': _patient(id: id, clinicId: clinicId)}),
  200,
);

PatientRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  required Future<AuthSessionState> Function() loadSession,
}) => PatientRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  // Scoped reads must obtain their bearer from the same checked session.
  loadAccessToken: () async => throw StateError('legacy token path used'),
  loadSession: loadSession,
  sessionNavigationGuard: guard,
);

void main() {
  test('scoped patient list accepts authorized clinic only', () async {
    final client = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/patients');
      expect(request.headers['authorization'], 'Bearer wd03-access');
      return _page();
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final page = await _source(
      client,
      guard,
      loadSession: () async => _session(),
    ).fetchPatients();
    expect(page.items.single.clinicId, 3);
    expect(page.items.single.id, 7);
  });

  test(
    'cross-clinic row rejects entire page; no partial patient result',
    () async {
      final client = MockClient(
        (_) async => _page(rows: [_patient(), _patient(id: 8, clinicId: 999)]),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(
          client,
          guard,
          loadSession: () async => _session(),
        ).fetchPatients(),
        throwsA(isA<PatientReadScopeViolationException>()),
      );
    },
  );

  test(
    'patient detail verifies both requested ID and selected clinic',
    () async {
      final client = MockClient((request) async {
        expect(request.url.path, '/api/v1/patients/7');
        expect(request.headers['x-emergency-access-id'], '13');
        return _detail();
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final patient = await _source(
        client,
        guard,
        loadSession: () async => _session(),
      ).fetchPatient(patientId: 7, emergencyAccessId: 13);
      expect(patient.id, 7);
      expect(patient.clinicId, 3);
    },
  );

  test(
    'wrong detail patient ID is rejected before it can be displayed',
    () async {
      final client = MockClient((_) async => _detail(id: 8));
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(
          client,
          guard,
          loadSession: () async => _session(),
        ).fetchPatient(patientId: 7),
        throwsA(isA<PatientReadScopeViolationException>()),
      );
    },
  );

  test('wrong detail clinic is rejected before it can be displayed', () async {
    final client = MockClient((_) async => _detail(clinicId: 999));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(
        client,
        guard,
        loadSession: () async => _session(),
      ).fetchPatient(patientId: 7),
      throwsA(isA<PatientReadScopeViolationException>()),
    );
  });

  test('signed-out guard blocks HTTP without exposing patient data', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return _page();
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSignedOut();
    addTearDown(guard.dispose);
    await expectLater(
      _source(
        client,
        guard,
        loadSession: () async => _session(),
      ).fetchPatients(),
      throwsA(isA<PatientReadSessionMismatchException>()),
    );
    expect(calls, 0);
  });

  test('logout while patient GET is pending drops the late response', () async {
    final entered = Completer<void>();
    final response = Completer<http.Response>();
    final client = MockClient((_) {
      entered.complete();
      return response.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final load = _source(
      client,
      guard,
      loadSession: () async => _session(),
    ).fetchPatient(patientId: 7);
    await entered.future;
    guard.markSignedOut();
    response.complete(_detail());
    await expectLater(
      load,
      throwsA(isA<PatientReadSessionMismatchException>()),
    );
  });

  test(
    'clinic switch while patient GET is pending drops the late response',
    () async {
      final entered = Completer<void>();
      final response = Completer<http.Response>();
      var current = _session();
      final client = MockClient((_) {
        entered.complete();
        return response.future;
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final load = _source(
        client,
        guard,
        loadSession: () async => current,
      ).fetchPatients();
      await entered.future;
      current = _session(clinicId: 9);
      response.complete(_page());
      await expectLater(
        load,
        throwsA(isA<PatientReadSessionMismatchException>()),
      );
    },
  );

  test('token rotation without guard change drops the late response', () async {
    final entered = Completer<void>();
    final response = Completer<http.Response>();
    var current = _session();
    final client = MockClient((_) {
      entered.complete();
      return response.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final load = _source(
      client,
      guard,
      loadSession: () async => current,
    ).fetchPatient(patientId: 7);
    await entered.future;
    current = _session(token: 'another-access-token');
    response.complete(_detail());
    await expectLater(
      load,
      throwsA(isA<PatientReadSessionMismatchException>()),
    );
  });

  test(
    'role and tenant change without guard change drops the late response',
    () async {
      final entered = Completer<void>();
      final response = Completer<http.Response>();
      var current = _session();
      final client = MockClient((_) {
        entered.complete();
        return response.future;
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final load = _source(
        client,
        guard,
        loadSession: () async => current,
      ).fetchPatients();
      await entered.future;
      current = _session(
        role: 'receptionist',
        tenantId: '22222222-2222-2222-2222-222222222222',
      );
      response.complete(_page());
      await expectLater(
        load,
        throwsA(isA<PatientReadSessionMismatchException>()),
      );
    },
  );

  test(
    'backend HTTP 403 remains a denial; client does not grant access',
    () async {
      final client = MockClient(
        (_) async => http.Response(
          jsonEncode({'success': false, 'message': 'Access denied'}),
          403,
        ),
      );
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(
          client,
          guard,
          loadSession: () async => _session(),
        ).fetchPatients(),
        throwsA(isA<ApiException>().having((e) => e.statusCode, 'status', 403)),
      );
    },
  );

  test('invalid session is denied before HTTP begins', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return _page();
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(
        client,
        guard,
        loadSession: () async => const AuthSessionState.signedOut(),
      ).fetchPatients(),
      throwsA(isA<PatientReadSessionMismatchException>()),
    );
    expect(calls, 0);
  });

  testWidgets(
    'patient directory clears prior PHI when a pending refresh loses its session',
    (tester) async {
      final refreshEntered = Completer<void>();
      final lateResponse = Completer<http.Response>();
      var requests = 0;
      final client = MockClient((_) {
        requests++;
        if (requests == 1) return Future.value(_page());
        refreshEntered.complete();
        return lateResponse.future;
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: PatientDirectoryScreen(
            dataSource: _source(
              client,
              guard,
              loadSession: () async => _session(),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Ada Lovelace'), findsOneWidget);

      await tester.drag(
        find.byKey(const ValueKey('patient-directory-list')),
        const Offset(0, 400),
      );
      // WD03 R02: advance the widget test's fake clock while the refresh
      // animation schedules its callback. Awaiting the completer here without
      // pumping can deadlock this test indefinitely.
      for (var frame = 0; frame < 20 && !refreshEntered.isCompleted; frame++) {
        await tester.pump(const Duration(milliseconds: 100));
      }
      expect(
        refreshEntered.isCompleted,
        isTrue,
        reason: 'Refresh must begin within a bounded number of pumped frames.',
      );
      guard.markSignedOut();
      lateResponse.complete(_page());
      await tester.pumpAndSettle();

      expect(requests, 2);
      expect(find.text('Ada Lovelace'), findsNothing);
      expect(find.text('Patient no. WD03-0007'), findsNothing);
      expect(find.textContaining('Session or clinic changed'), findsWidgets);
    },
  );

  testWidgets(
    'cross-clinic refresh response removes previously displayed directory',
    (tester) async {
      var requests = 0;
      final client = MockClient((_) async {
        requests++;
        if (requests == 1) return _page();
        return _page(rows: [_patient(id: 8, clinicId: 999)]);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: PatientDirectoryScreen(
            dataSource: _source(
              client,
              guard,
              loadSession: () async => _session(),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Ada Lovelace'), findsOneWidget);
      await tester.drag(
        find.byKey(const ValueKey('patient-directory-list')),
        const Offset(0, 400),
      );
      await tester.pumpAndSettle();
      expect(requests, 2);
      expect(find.text('Ada Lovelace'), findsNothing);
      expect(
        find.textContaining('outside the authorized clinic'),
        findsWidgets,
      );
    },
  );
}
