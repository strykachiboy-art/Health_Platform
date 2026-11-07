import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/models/appointment_record.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

AuthSessionState _ready({
  String role = 'DOCTOR',
  int? clinicId = 3,
  String token = 's04d-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 's04d-refresh',
    userId: 42,
    role: role,
    clinicContextId: clinicId,
  ),
);

Map<String, dynamic> _row({
  int id = 9,
  int clinic = 3,
  String status = 'scheduled',
  String? notes,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': 7,
  'staff_id': 11,
  'scheduled_start': '2026-12-10T09:00:00Z',
  'scheduled_end': '2026-12-10T10:00:00Z',
  'status': status,
  'appointment_type': 'in_person',
  'reason': null,
  'notes': notes,
  'google_calendar_event_id': null,
  'reminder_sent': false,
  'created_at': '2026-12-01T10:00:00Z',
  'updated_at': '2026-12-01T10:00:00Z',
  'cancelled_at': null,
  'cancellation_reason': null,
};

AppointmentRecord _record({String status = 'scheduled'}) =>
    AppointmentRecord.fromJson(
      _row(status: status),
      expectedClinicId: 3,
      expectedPatientId: 7,
    );

http.Response _ok(Map<String, dynamic> row) => http.Response(
  jsonEncode(<String, dynamic>{'success': true, 'data': row}),
  200,
);

http.Response _error(int code) => http.Response(
  jsonEncode(<String, dynamic>{'success': false, 'error': 'Not allowed'}),
  code,
);

class _Rig {
  _Rig(
    Future<http.Response> Function(http.Request) handler, {
    Future<AuthSessionState> Function()? loader,
    bool available = true,
  }) : guard = SessionNavigationGuard(
         protectedRoutePrefixes: const <String>['/appointments'],
       ) {
    if (available) guard.markSessionAvailable();
    client = MockClient(handler);
    api = ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
      requestTimeout: const Duration(seconds: 2),
    );
    source = AppointmentMutationRemoteDataSource(
      apiClient: api,
      loadSession: loader ?? () async => _ready(),
      sessionNavigationGuard: guard,
    );
  }

  final SessionNavigationGuard guard;
  late final MockClient client;
  late final ApiClient api;
  late final AppointmentMutationRemoteDataSource source;

  void close() {
    client.close();
    guard.dispose();
  }
}

void main() {
  test(
    'confirm only scheduled: authenticated one-shot POST and validated row',
    () async {
      var count = 0;
      final rig = _Rig((request) async {
        count++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/appointments/9/confirm');
        expect(request.headers['authorization'], 'Bearer s04d-token');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        expect(jsonDecode(request.body), isEmpty);
        return _ok(_row(status: 'confirmed'));
      });
      addTearDown(rig.close);
      final result = await rig.source.confirm(appointment: _record());
      expect(result.isConfirmed, isTrue);
      expect(result.appointment?.status, AppointmentRecordStatus.confirmed);
      expect(count, 1);
    },
  );

  test(
    'complete requires confirmed; optional notes are absent by default',
    () async {
      final rig = _Rig((request) async {
        expect(request.url.path, '/api/v1/appointments/9/complete');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        expect(jsonDecode(request.body), isEmpty);
        return _ok(_row(status: 'completed', notes: 'Existing notes'));
      });
      addTearDown(rig.close);
      final result = await rig.source.complete(
        appointment: _record(status: 'confirmed'),
      );
      expect(result.isConfirmed, isTrue);
      expect(result.appointment?.status, AppointmentRecordStatus.completed);
    },
  );

  test(
    'complete trims explicit notes and validates saved server notes',
    () async {
      final rig = _Rig((request) async {
        expect(jsonDecode(request.body), <String, dynamic>{
          'notes': 'Attended',
        });
        return _ok(_row(status: 'completed', notes: 'Attended'));
      });
      addTearDown(rig.close);
      final result = await rig.source.complete(
        appointment: _record(status: 'confirmed'),
        notes: '  Attended  ',
      );
      expect(result.isConfirmed, isTrue);
    },
  );

  test(
    'no-show requires confirmed and validates terminal server status',
    () async {
      var count = 0;
      final rig = _Rig((request) async {
        count++;
        expect(request.url.path, '/api/v1/appointments/9/no-show');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        expect(jsonDecode(request.body), isEmpty);
        return _ok(_row(status: 'no_show'));
      });
      addTearDown(rig.close);
      final result = await rig.source.markNoShow(
        appointment: _record(status: 'confirmed'),
      );
      expect(result.isConfirmed, isTrue);
      expect(result.appointment?.status, AppointmentRecordStatus.noShow);
      expect(count, 1);
    },
  );

  test(
    'illegal original statuses are rejected before any HTTP request',
    () async {
      var hits = 0;
      final rig = _Rig((_) async {
        hits++;
        return _error(503);
      });
      addTearDown(rig.close);
      for (final status in ['confirmed', 'cancelled', 'completed', 'no_show']) {
        expect(
          (await rig.source.confirm(appointment: _record(status: status)))
              .outcome,
          AppointmentMutationOutcome.rejected,
        );
      }
      for (final status in ['scheduled', 'cancelled', 'completed', 'no_show']) {
        expect(
          (await rig.source.complete(appointment: _record(status: status)))
              .outcome,
          AppointmentMutationOutcome.rejected,
        );
        expect(
          (await rig.source.markNoShow(appointment: _record(status: status)))
              .outcome,
          AppointmentMutationOutcome.rejected,
        );
      }
      expect(hits, 0);
    },
  );

  test('completion notes reject whitespace and overlength before HTTP', () {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(503);
    });
    addTearDown(rig.close);
    final record = _record(status: 'confirmed');
    expect(
      () => rig.source.complete(appointment: record, notes: ' '),
      throwsArgumentError,
    );
    expect(
      () => rig.source.complete(
        appointment: record,
        notes: List<String>.filled(2001, 'x').join(),
      ),
      throwsArgumentError,
    );
    expect(hits, 0);
  });

  test('HTTP 409 is definitive rejection, not server confirmation', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(409);
    });
    addTearDown(rig.close);
    expect(
      (await rig.source.confirm(appointment: _record())).outcome,
      AppointmentMutationOutcome.rejected,
    );
    expect(hits, 1);
  });

  test(
    'HTTP 503 makes confirmation uncertain and forbids same-ID follow-up',
    () async {
      var hits = 0;
      final rig = _Rig((_) async {
        hits++;
        return _error(503);
      });
      addTearDown(rig.close);
      expect(
        (await rig.source.confirm(appointment: _record())).outcome,
        AppointmentMutationOutcome.reconciliationRequired,
      );
      expect(
        (await rig.source.complete(appointment: _record(status: 'confirmed')))
            .outcome,
        AppointmentMutationOutcome.reconciliationRequired,
      );
      expect(
        (await rig.source.markNoShow(appointment: _record(status: 'confirmed')))
            .outcome,
        AppointmentMutationOutcome.reconciliationRequired,
      );
      expect(hits, 1);
    },
  );

  test('existing cancellation uncertainty blocks later confirmation', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(503);
    });
    addTearDown(rig.close);
    expect(
      (await rig.source.cancel(appointment: _record())).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
    expect(
      (await rig.source.confirm(appointment: _record())).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
    expect(hits, 1);
  });

  test('existing reschedule uncertainty blocks no-show on same row', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(503);
    });
    addTearDown(rig.close);
    expect(
      (await rig.source.reschedule(
        appointment: _record(status: 'confirmed'),
        newStart: DateTime.utc(2026, 12, 12, 9),
        newEnd: DateTime.utc(2026, 12, 12, 10),
      )).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
    expect(
      (await rig.source.markNoShow(appointment: _record(status: 'confirmed')))
          .outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
    expect(hits, 1);
  });

  test('simultaneous status actions for same ID send only one POST', () async {
    var hits = 0;
    final response = Completer<http.Response>();
    final started = Completer<void>();
    final rig = _Rig((_) async {
      hits++;
      started.complete();
      return response.future;
    });
    addTearDown(rig.close);
    final first = rig.source.confirm(appointment: _record());
    await started.future;
    final second = await rig.source.confirm(appointment: _record());
    expect(second.outcome, AppointmentMutationOutcome.alreadyInFlight);
    response.complete(_ok(_row(status: 'confirmed')));
    expect((await first).isConfirmed, isTrue);
    expect(hits, 1);
  });

  test('malformed success response cannot confirm status', () async {
    final rig = _Rig((_) async => _ok(_row(status: 'scheduled')));
    addTearDown(rig.close);
    expect(
      (await rig.source.confirm(appointment: _record())).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
  });

  test('completion notes mismatch cannot confirm status', () async {
    final rig = _Rig(
      (_) async => _ok(_row(status: 'completed', notes: 'Other')),
    );
    addTearDown(rig.close);
    expect(
      (await rig.source.complete(
        appointment: _record(status: 'confirmed'),
        notes: 'Expected',
      )).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
  });

  test('different clinic record cannot confirm status', () async {
    final rig = _Rig((_) async => _ok(_row(status: 'confirmed', clinic: 4)));
    addTearDown(rig.close);
    expect(
      (await rig.source.confirm(appointment: _record())).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
  });

  test('different appointment ID cannot confirm status', () async {
    final rig = _Rig((_) async => _ok(_row(id: 99, status: 'no_show')));
    addTearDown(rig.close);
    expect(
      (await rig.source.markNoShow(appointment: _record(status: 'confirmed')))
          .outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
  });

  test('wrong clinic context blocks all status POSTs', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(503);
    }, loader: () async => _ready(clinicId: 4));
    addTearDown(rig.close);
    expect(
      (await rig.source.confirm(appointment: _record())).outcome,
      AppointmentMutationOutcome.sessionUnavailable,
    );
    expect(
      (await rig.source.complete(appointment: _record(status: 'confirmed')))
          .outcome,
      AppointmentMutationOutcome.sessionUnavailable,
    );
    expect(
      (await rig.source.markNoShow(appointment: _record(status: 'confirmed')))
          .outcome,
      AppointmentMutationOutcome.sessionUnavailable,
    );
    expect(hits, 0);
  });

  test('wrong role blocks status mutation before HTTP', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(503);
    }, loader: () async => _ready(role: 'PATIENT'));
    addTearDown(rig.close);
    expect(
      (await rig.source.confirm(appointment: _record())).outcome,
      AppointmentMutationOutcome.sessionUnavailable,
    );
    expect(hits, 0);
  });

  test('stale navigation guard blocks status mutation before HTTP', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(503);
    }, available: false);
    addTearDown(rig.close);
    expect(
      (await rig.source.confirm(appointment: _record())).outcome,
      AppointmentMutationOutcome.sessionUnavailable,
    );
    expect(hits, 0);
  });

  test('token rotation after dispatch never confirms status', () async {
    var hits = 0;
    var token = 's04d-token';
    final rig = _Rig((_) async {
      hits++;
      token = 'changed-token';
      return _ok(_row(status: 'confirmed'));
    }, loader: () async => _ready(token: token));
    addTearDown(rig.close);
    expect(
      (await rig.source.confirm(appointment: _record())).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
    expect(hits, 1);
  });

  test('invalid server JSON cannot confirm status and never retries', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return http.Response('{broken', 200);
    });
    addTearDown(rig.close);
    expect(
      (await rig.source.confirm(appointment: _record())).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
    expect(
      (await rig.source.confirm(appointment: _record())).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
    expect(hits, 1);
  });
}
