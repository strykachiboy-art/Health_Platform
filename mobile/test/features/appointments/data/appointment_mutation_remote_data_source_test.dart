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
import 'package:clinic_system_pro/features/appointments/data/appointment_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/models/appointment_record.dart';

final _newStart = DateTime.utc(2026, 12, 11, 9);
final _newEnd = DateTime.utc(2026, 12, 11, 10);

AuthSessionState _ready({
  String role = 'DOCTOR',
  int user = 42,
  int? clinic = 3,
  String access = 'test-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: access,
    refreshToken: 'test-refresh',
    userId: user,
    role: role,
    clinicContextId: clinic,
  ),
);

Map<String, dynamic> _row({
  int id = 9,
  int clinic = 3,
  int patient = 7,
  int staff = 11,
  String status = 'scheduled',
  String start = '2026-12-10T09:00:00Z',
  String end = '2026-12-10T10:00:00Z',
  String? cancelledAt,
  String? cancellationReason,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'staff_id': staff,
  'scheduled_start': start,
  'scheduled_end': end,
  'status': status,
  'appointment_type': 'in_person',
  'reason': null,
  'notes': null,
  'google_calendar_event_id': null,
  'reminder_sent': false,
  'created_at': '2026-12-01T10:00:00Z',
  'updated_at': '2026-12-01T10:00:00Z',
  'cancelled_at': cancelledAt,
  'cancellation_reason': cancellationReason,
};

AppointmentRecord _appointment({String status = 'scheduled', int id = 9}) =>
    AppointmentRecord.fromJson(
      _row(status: status, id: id),
      expectedClinicId: 3,
      expectedPatientId: 7,
    );

http.Response _success(Map<String, dynamic> data) => http.Response(
  jsonEncode(<String, dynamic>{'success': true, 'data': data}),
  200,
);

http.Response _error(int code) => http.Response(
  jsonEncode(<String, dynamic>{'success': false, 'error': 'Denied'}),
  code,
);

class _Rig {
  _Rig(
    Future<http.Response> Function(http.Request) handler, {
    Future<AuthSessionState> Function()? loader,
    bool available = true,
    Duration timeout = const Duration(seconds: 2),
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
      requestTimeout: timeout,
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

Future<AppointmentMutationResult> _cancel(
  _Rig rig, {
  AppointmentRecord? appointment,
  String? reason,
}) => rig.source.cancel(
  appointment: appointment ?? _appointment(),
  reason: reason,
);

Future<AppointmentMutationResult> _reschedule(
  _Rig rig, {
  AppointmentRecord? appointment,
}) => rig.source.reschedule(
  appointment: appointment ?? _appointment(),
  newStart: _newStart,
  newEnd: _newEnd,
);

void main() {
  test(
    'reschedule POST has exact path/body, bearer and no idempotency header',
    () async {
      var hits = 0;
      final rig = _Rig((request) async {
        hits++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/appointments/9/reschedule');
        expect(request.headers['authorization'], 'Bearer test-token');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        expect(jsonDecode(request.body), <String, dynamic>{
          'scheduled_start': _newStart.toIso8601String(),
          'scheduled_end': _newEnd.toIso8601String(),
        });
        return _success(
          _row(
            start: _newStart.toIso8601String(),
            end: _newEnd.toIso8601String(),
          ),
        );
      });
      addTearDown(rig.close);
      final result = await _reschedule(rig);
      expect(result.outcome, AppointmentMutationOutcome.confirmed);
      expect(result.isConfirmed, isTrue);
      expect(result.appointment?.id, 9);
      expect(hits, 1);
    },
  );

  test(
    'cancel POST trims reason, returns only server-confirmed cancelled row',
    () async {
      var hits = 0;
      final rig = _Rig((request) async {
        hits++;
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/appointments/9/cancel');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        expect(jsonDecode(request.body), <String, dynamic>{
          'cancellation_reason': 'Reschedule declined',
        });
        return _success(
          _row(
            status: 'cancelled',
            cancelledAt: '2026-12-04T12:00:00Z',
            cancellationReason: 'Reschedule declined',
          ),
        );
      });
      addTearDown(rig.close);
      final result = await _cancel(rig, reason: '  Reschedule declined  ');
      expect(result.isConfirmed, isTrue);
      expect(result.appointment?.status, AppointmentRecordStatus.cancelled);
      expect(hits, 1);
    },
  );

  test('cancel without reason posts an empty object, not null', () async {
    final rig = _Rig((request) async {
      expect(jsonDecode(request.body), isEmpty);
      return _success(
        _row(status: 'cancelled', cancelledAt: '2026-12-04T12:00:00Z'),
      );
    });
    addTearDown(rig.close);
    expect((await _cancel(rig)).isConfirmed, isTrue);
  });

  test('terminal status blocks reschedule and cancel locally', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(500);
    });
    addTearDown(rig.close);
    for (final status in <String>['cancelled', 'completed', 'no_show']) {
      final record = _appointment(status: status);
      expect(
        (await _cancel(rig, appointment: record)).outcome,
        AppointmentMutationOutcome.rejected,
      );
      expect(
        (await _reschedule(rig, appointment: record)).outcome,
        AppointmentMutationOutcome.rejected,
      );
    }
    expect(hits, 0);
  });

  test('nonpositive appointment ID blocks before HTTP', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(500);
    });
    addTearDown(rig.close);
    final valid = _appointment();
    final invalid = AppointmentRecord(
      id: 0,
      clinicId: valid.clinicId,
      patientId: valid.patientId,
      staffId: valid.staffId,
      scheduledStart: valid.scheduledStart,
      scheduledEnd: valid.scheduledEnd,
      status: valid.status,
      appointmentType: valid.appointmentType,
      reason: valid.reason,
      notes: valid.notes,
      googleCalendarEventId: valid.googleCalendarEventId,
      reminderSent: valid.reminderSent,
      createdAt: valid.createdAt,
      updatedAt: valid.updatedAt,
      cancelledAt: valid.cancelledAt,
      cancellationReason: valid.cancellationReason,
    );
    await expectLater(_cancel(rig, appointment: invalid), throwsArgumentError);
    expect(hits, 0);
  });

  test('invalid reschedule range blocks before HTTP', () {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(500);
    });
    addTearDown(rig.close);
    expect(
      () => rig.source.reschedule(
        appointment: _appointment(),
        newStart: _newEnd,
        newEnd: _newStart,
      ),
      throwsArgumentError,
    );
    expect(hits, 0);
  });

  test('empty and overlength cancellation reasons fail before HTTP', () {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(500);
    });
    addTearDown(rig.close);
    expect(
      () => rig.source.cancel(appointment: _appointment(), reason: '   '),
      throwsArgumentError,
    );
    expect(
      () => rig.source.cancel(
        appointment: _appointment(),
        reason: List<String>.filled(256, 'x').join(),
      ),
      throwsArgumentError,
    );
    expect(hits, 0);
  });

  for (final role in <String>[
    'PATIENT',
    'SUPER_ADMIN',
    'PHARMACIST',
    'ACCOUNTANT',
    'UNKNOWN',
  ]) {
    test('role $role cannot send appointment mutation', () async {
      var hits = 0;
      final rig = _Rig((_) async {
        hits++;
        return _error(500);
      }, loader: () async => _ready(role: role));
      addTearDown(rig.close);
      expect(
        (await _cancel(rig)).outcome,
        AppointmentMutationOutcome.sessionUnavailable,
      );
      expect(hits, 0);
    });
  }

  test('no clinic context denies before send', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(500);
    }, loader: () async => _ready(clinic: null));
    addTearDown(rig.close);
    expect(
      (await _reschedule(rig)).outcome,
      AppointmentMutationOutcome.sessionUnavailable,
    );
    expect(hits, 0);
  });

  test('wrong clinic context denies before send', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(500);
    }, loader: () async => _ready(clinic: 4));
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.sessionUnavailable,
    );
    expect(hits, 0);
  });

  test('invalid signed-in user denies before send', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(500);
    }, loader: () async => _ready(user: 0));
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.sessionUnavailable,
    );
    expect(hits, 0);
  });

  test('malformed bearer token denies before send', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(500);
    }, loader: () async => _ready(access: '  token  '));
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.sessionUnavailable,
    );
    expect(hits, 0);
  });

  test('signed-out navigation guard prevents all requests', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      return _error(500);
    }, available: false);
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.sessionUnavailable,
    );
    expect(hits, 0);
  });

  test(
    'clinic rotates between two pre-send restorations: zero posts',
    () async {
      var loads = 0;
      var hits = 0;
      final rig = _Rig((_) async {
        hits++;
        return _error(500);
      }, loader: () async => ++loads == 1 ? _ready() : _ready(clinic: 4));
      addTearDown(rig.close);
      expect(
        (await _reschedule(rig)).outcome,
        AppointmentMutationOutcome.sessionUnavailable,
      );
      expect(loads, 2);
      expect(hits, 0);
    },
  );

  test('guard invalidated during initial restore: zero posts', () async {
    var hits = 0;
    late _Rig rig;
    rig = _Rig(
      (_) async {
        hits++;
        return _error(500);
      },
      loader: () async {
        rig.guard.invalidate();
        return _ready();
      },
    );
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.sessionUnavailable,
    );
    expect(hits, 0);
  });

  test('duplicate tap while first POST in flight sends only once', () async {
    var hits = 0;
    final pending = Completer<http.Response>();
    final rig = _Rig((_) {
      hits++;
      return pending.future;
    });
    addTearDown(rig.close);
    final first = _cancel(rig);
    await Future<void>.delayed(Duration.zero);
    expect(hits, 1);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.alreadyInFlight,
    );
    pending.complete(
      _success(_row(status: 'cancelled', cancelledAt: '2026-12-04T12:00:00Z')),
    );
    expect((await first).isConfirmed, isTrue);
    expect(hits, 1);
  });

  test('different actions for same ID cannot overlap', () async {
    var hits = 0;
    final pending = Completer<http.Response>();
    final rig = _Rig((_) {
      hits++;
      return pending.future;
    });
    addTearDown(rig.close);
    final first = _reschedule(rig);
    await Future<void>.delayed(Duration.zero);
    expect(hits, 1);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.alreadyInFlight,
    );
    pending.complete(
      _success(
        _row(
          start: _newStart.toIso8601String(),
          end: _newEnd.toIso8601String(),
        ),
      ),
    );
    expect((await first).isConfirmed, isTrue);
    expect(hits, 1);
  });

  test('connection failure is uncertain and cannot auto-replay', () async {
    var hits = 0;
    final rig = _Rig((_) async {
      hits++;
      throw http.ClientException('socket unavailable');
    });
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
    expect(hits, 1);
  });

  test('timeout is uncertain (no retry)', () async {
    final pending = Completer<http.Response>();
    var hits = 0;
    final rig = _Rig((_) {
      hits++;
      return pending.future;
    }, timeout: const Duration(milliseconds: 20));
    addTearDown(rig.close);
    expect(
      (await _reschedule(rig)).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
    expect(hits, 1);
  });

  for (final code in <int>[408, 429, 500, 502, 503]) {
    test('HTTP $code remains uncertain and is never auto-retried', () async {
      var hits = 0;
      final rig = _Rig((_) async {
        hits++;
        return _error(code);
      });
      addTearDown(rig.close);
      expect(
        (await _cancel(rig)).outcome,
        AppointmentMutationOutcome.reconciliationRequired,
      );
      expect(
        (await _cancel(rig)).outcome,
        AppointmentMutationOutcome.reconciliationRequired,
      );
      expect(hits, 1);
    });
  }

  for (final code in <int>[400, 403, 404, 409, 422]) {
    test('HTTP $code is a definitive rejected mutation', () async {
      final rig = _Rig((_) async => _error(code));
      addTearDown(rig.close);
      final result = await _cancel(rig);
      expect(result.outcome, AppointmentMutationOutcome.rejected);
      expect(result.appointment, isNull);
    });
  }

  test(
    'HTTP 401 rejects request and triggers app authentication invalidation',
    () async {
      final rig = _Rig((_) async => _error(401));
      addTearDown(rig.close);
      rig.api.onAuthenticationRejected = rig.guard.invalidate;
      expect((await _cancel(rig)).outcome, AppointmentMutationOutcome.rejected);
      expect(rig.guard.canAccessProtectedRoutes, isFalse);
    },
  );

  test('HTTP 200 malformed JSON is uncertain', () async {
    final rig = _Rig((_) async => http.Response('not json', 200));
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
  });

  test(
    'HTTP 200 unsuccessful envelope cannot be treated as confirmed',
    () async {
      final rig = _Rig(
        (_) async => http.Response(
          jsonEncode(<String, dynamic>{'success': false, 'data': _row()}),
          200,
        ),
      );
      addTearDown(rig.close);
      expect(
        (await _cancel(rig)).outcome,
        AppointmentMutationOutcome.reconciliationRequired,
      );
    },
  );

  test('HTTP 200 other-clinic record cannot confirm mutation', () async {
    final rig = _Rig(
      (_) async => _success(
        _row(
          clinic: 4,
          status: 'cancelled',
          cancelledAt: '2026-12-04T12:00:00Z',
        ),
      ),
    );
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
  });

  test('HTTP 200 other-patient record cannot confirm mutation', () async {
    final rig = _Rig(
      (_) async => _success(
        _row(
          patient: 8,
          status: 'cancelled',
          cancelledAt: '2026-12-04T12:00:00Z',
        ),
      ),
    );
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
  });

  test('HTTP 200 wrong appointment ID cannot confirm mutation', () async {
    final rig = _Rig(
      (_) async => _success(
        _row(id: 10, status: 'cancelled', cancelledAt: '2026-12-04T12:00:00Z'),
      ),
    );
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
  });

  test('HTTP 200 wrong staff ID cannot confirm mutation', () async {
    final rig = _Rig(
      (_) async => _success(
        _row(
          staff: 13,
          status: 'cancelled',
          cancelledAt: '2026-12-04T12:00:00Z',
        ),
      ),
    );
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
  });

  test('cancel 200 with unchanged scheduled status is uncertain', () async {
    final rig = _Rig((_) async => _success(_row()));
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
  });

  test('cancel 200 without server cancelled timestamp is uncertain', () async {
    final rig = _Rig((_) async => _success(_row(status: 'cancelled')));
    addTearDown(rig.close);
    expect(
      (await _cancel(rig)).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
  });

  test('reschedule 200 with mismatched times is uncertain', () async {
    final rig = _Rig((_) async => _success(_row()));
    addTearDown(rig.close);
    expect(
      (await _reschedule(rig)).outcome,
      AppointmentMutationOutcome.reconciliationRequired,
    );
  });

  test(
    'token changes after server response: never show confirmed state',
    () async {
      var loads = 0;
      final rig = _Rig(
        (_) async => _success(
          _row(status: 'cancelled', cancelledAt: '2026-12-04T12:00:00Z'),
        ),
        loader: () async {
          loads++;
          return loads > 2 ? _ready(access: 'new-token') : _ready();
        },
      );
      addTearDown(rig.close);
      expect(
        (await _cancel(rig)).outcome,
        AppointmentMutationOutcome.reconciliationRequired,
      );
      expect(loads, 3);
    },
  );

  test(
    'clinic changes after server response: never show confirmed state',
    () async {
      var loads = 0;
      final rig = _Rig(
        (_) async => _success(
          _row(status: 'cancelled', cancelledAt: '2026-12-04T12:00:00Z'),
        ),
        loader: () async {
          loads++;
          return loads > 2 ? _ready(clinic: 4) : _ready();
        },
      );
      addTearDown(rig.close);
      expect(
        (await _cancel(rig)).outcome,
        AppointmentMutationOutcome.reconciliationRequired,
      );
    },
  );

  test(
    'guard invalidated after dispatch: never show confirmed state',
    () async {
      late _Rig rig;
      rig = _Rig((_) async {
        rig.guard.invalidate();
        return _success(
          _row(status: 'cancelled', cancelledAt: '2026-12-04T12:00:00Z'),
        );
      });
      addTearDown(rig.close);
      expect(
        (await _cancel(rig)).outcome,
        AppointmentMutationOutcome.reconciliationRequired,
      );
    },
  );

  test(
    'unexpected transport exception cannot trigger automatic replay',
    () async {
      var hits = 0;
      final rig = _Rig((_) async {
        hits++;
        throw StateError('failure after possible send');
      });
      addTearDown(rig.close);
      expect(
        (await _reschedule(rig)).outcome,
        AppointmentMutationOutcome.reconciliationRequired,
      );
      expect(
        (await _reschedule(rig)).outcome,
        AppointmentMutationOutcome.reconciliationRequired,
      );
      expect(hits, 1);
    },
  );
}
