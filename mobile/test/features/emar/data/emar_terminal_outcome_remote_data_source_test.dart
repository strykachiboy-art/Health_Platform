import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/emar_terminal_outcome_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';

AuthSessionState ready({String role = 'nurse', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'terminal-token',
        refreshToken: 'terminal-refresh',
        userId: 71,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> row({
  String status = 'due',
  String? reason,
  int version = 1,
  int patient = 5,
}) => {
  'id': 21,
  'clinic_id': 3,
  'patient_id': patient,
  'admission_id': 7,
  'prescription_id': 17,
  'prescription_item_id': 19,
  'status': status,
  'record_status': status == 'due' ? 'draft' : 'final',
  'record_version': version,
  'source_order_version': 2,
  'dose_value': '1.25',
  'dose_unit': 'mg',
  'route_code': 'oral',
  'scheduled_at': '2026-11-09T11:00:00Z',
  'held_at': status == 'held' ? '2026-11-09T11:01:00Z' : null,
  'hold_reason': status == 'held' ? reason : null,
  'refused_at': status == 'refused' ? '2026-11-09T11:01:00Z' : null,
  'refusal_reason': status == 'refused' ? reason : null,
  'missed_at': status == 'missed' ? '2026-11-09T11:01:00Z' : null,
  'missed_reason': status == 'missed' ? reason : null,
};

EmarAdministration reviewed() => EmarAdministration.fromJson(
  row(),
  expectedClinicId: 3,
  expectedPatientId: 5,
  expectedAdmissionId: 7,
  expectedId: 21,
);

EmarTerminalOutcomeRemoteDataSource adapter(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  final load = loader ?? () async => ready();
  return EmarTerminalOutcomeRemoteDataSource(
    apiClient: api,
    readSource: EmarReadRemoteDataSource(
      apiClient: api,
      loadSession: load,
      sessionNavigationGuard: guard,
    ),
    loadSession: load,
    sessionNavigationGuard: guard,
  );
}

Future<EmarAdministration> submit(
  EmarTerminalOutcomeRemoteDataSource source, {
  EmarTerminalAction action = EmarTerminalAction.hold,
  String reason = 'Documented clinical reason',
}) => source.recordOnce(
  reviewed: reviewed(),
  action: action,
  reason: reason,
  operationKey: 'terminal-once',
);

void main() {
  for (final action in EmarTerminalAction.values) {
    test(
      '${action.endpoint}: correct POST, one-shot and verified readback',
      () async {
        var posts = 0;
        var details = 0;
        final client = MockClient((request) async {
          expect(request.headers['authorization'], 'Bearer terminal-token');
          if (request.method == 'GET') {
            details++;
            expect(request.url.path, '/api/v1/emar/21');
            return http.Response(
              jsonEncode({
                'administration': details == 1
                    ? row()
                    : row(
                        status: action.resultingStatus.name,
                        reason: 'Documented clinical reason',
                      ),
              }),
              200,
            );
          }
          posts++;
          expect(request.method, 'POST');
          expect(request.url.path, '/api/v1/emar/21/${action.endpoint}');
          expect(request.headers['idempotency-key'], 'terminal-once');
          expect(jsonDecode(request.body), {
            'reason': 'Documented clinical reason',
          });
          return http.Response(
            jsonEncode({
              'administration': row(
                status: action.resultingStatus.name,
                reason: 'Documented clinical reason',
              ),
            }),
            200,
          );
        });
        addTearDown(client.close);
        final guard = SessionNavigationGuard()..markSessionAvailable();
        addTearDown(guard.dispose);
        final source = adapter(client, guard);
        final result = await submit(source, action: action);
        expect(result.status, action.resultingStatus);
        expect(result.terminalReason, 'Documented clinical reason');
        expect(result.terminalAt, isNotNull);
        expect(details, 2);
        expect(posts, 1);
        expect(source.attemptStarted, isTrue);
        await expectLater(
          submit(source, action: action),
          throwsA(isA<EmarTerminalOutcomeUnconfirmed>()),
        );
        expect(posts, 1);
      },
    );
  }

  test('invalid or blank reason blocks requests', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = adapter(client, guard);
    for (final reason in [
      '',
      '   ',
      ' padded ',
      List<String>.filled(501, 'A').join(),
    ]) {
      await expectLater(
        submit(source, reason: reason),
        throwsA(isA<EmarTerminalEvidenceChanged>()),
      );
    }
    expect(calls, 0);
  });

  test('doctor and other-clinic roles cannot submit', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 500);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      submit(adapter(client, guard, loader: () async => ready(role: 'doctor'))),
      throwsA(isA<EmarTerminalScopeUnavailable>()),
    );
    await expectLater(
      submit(adapter(client, guard, loader: () async => ready(clinic: 4))),
      throwsA(isA<EmarTerminalScopeUnavailable>()),
    );
    expect(calls, 0);
  });

  test('changed prescription/order snapshot blocks POST', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') posts++;
      return http.Response(
        jsonEncode({'administration': row(version: 3)}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      submit(adapter(client, guard)),
      throwsA(isA<EmarTerminalEvidenceChanged>()),
    );
    expect(posts, 0);
  });

  test('uncertain POST cannot be retried', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return http.Response(jsonEncode({'administration': row()}), 200);
      }
      posts++;
      throw http.ClientException('uncertain clinical outcome');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = adapter(client, guard);
    await expectLater(
      submit(source),
      throwsA(isA<EmarTerminalOutcomeUnconfirmed>()),
    );
    await expectLater(
      submit(source),
      throwsA(isA<EmarTerminalOutcomeUnconfirmed>()),
    );
    expect(posts, 1);
  });

  test('logout in-flight invalidates POST result', () async {
    var posts = 0;
    final pending = Completer<http.Response>();
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return http.Response(jsonEncode({'administration': row()}), 200);
      }
      posts++;
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = adapter(client, guard);
    final operation = submit(source);
    for (var i = 0; i < 40 && posts == 0; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 1));
    }
    expect(posts, 1);
    guard.markSignedOut();
    pending.complete(
      http.Response(
        jsonEncode({
          'administration': row(
            status: 'held',
            reason: 'Documented clinical reason',
          ),
        }),
        200,
      ),
    );
    await expectLater(
      operation,
      throwsA(isA<EmarTerminalOutcomeUnconfirmed>()),
    );
    expect(posts, 1);
  });

  test('server missing reason/time is not confirmed', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'GET') {
        return http.Response(jsonEncode({'administration': row()}), 200);
      }
      posts++;
      return http.Response(
        jsonEncode({'administration': row(status: 'held')}),
        200,
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = adapter(client, guard);
    await expectLater(
      submit(source),
      throwsA(isA<EmarTerminalOutcomeUnconfirmed>()),
    );
    expect(posts, 1);
  });

  test(
    'terminal history model reads server timestamps/reasons, not client clock',
    () {
      for (final status in ['held', 'refused', 'missed']) {
        final record = EmarAdministration.fromJson(
          row(status: status, reason: 'Recorded by server'),
          expectedClinicId: 3,
        );
        expect(record.status.name, status);
        expect(record.terminalReason, 'Recorded by server');
        expect(record.terminalAt, isNotNull);
      }
      expect(reviewed().terminalReason, isNull);
      expect(reviewed().terminalAt, isNull);
    },
  );
}
