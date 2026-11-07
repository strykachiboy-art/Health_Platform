import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/sync/appointment_create_queue_producer.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_create_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

final _start = DateTime(2030, 2, 12, 9);
final _end = DateTime(2030, 2, 12, 9, 30);

AuthSessionState _ready({
  String role = 'doctor',
  int? clinic = 3,
  int user = 42,
  String token = 's03c-test-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 's03c-test-refresh',
    userId: user,
    role: role,
    clinicContextId: clinic,
  ),
);

SessionNavigationGuard _guard() => SessionNavigationGuard(
  protectedRoutePrefixes: const <String>['/appointments'],
)..markSessionAvailable();

Widget _screen({
  required SessionNavigationGuard guard,
  required AppointmentCreateEnqueue enqueue,
  Future<AuthSessionState> Function()? loadSession,
  int? patientId = 7,
  int? staffId = 8,
  DateTime? start,
  DateTime? end,
}) => MaterialApp(
  home: AppointmentCreateScreen(
    loadSession: loadSession ?? () async => _ready(),
    sessionNavigationGuard: guard,
    enqueue: enqueue,
    initialPatientId: patientId,
    initialStaffId: staffId,
    initialStart: start ?? _start,
    initialEnd: end ?? _end,
  ),
);

AppointmentCreateEnqueueResult _result(
  AppointmentCreateCommand command,
  AppointmentCreateEnqueueStatus status,
) => AppointmentCreateEnqueueResult(
  status: status,
  operationId: command.operationId,
  idempotencyKey: command.idempotencyKey,
);

Future<void> _submit(WidgetTester tester) async {
  await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
  await tester.pumpAndSettle();
}

void main() {
  for (final role in <String>['ADMIN', 'DOCTOR', 'NURSE', 'RECEPTIONIST']) {
    testWidgets('allowlisted $role may queue but never claim booking', (
      tester,
    ) async {
      final guard = _guard();
      addTearDown(guard.dispose);
      var count = 0;
      await tester.pumpWidget(
        _screen(
          guard: guard,
          loadSession: () async => _ready(role: role),
          enqueue: (command) async {
            count++;
            return _result(command, AppointmentCreateEnqueueStatus.queued);
          },
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('appointment-create-ready')),
        findsOneWidget,
      );
      await _submit(tester);
      expect(count, 1);
      expect(
        find.byKey(const ValueKey('appointment-create-queued')),
        findsOneWidget,
      );
      expect(find.textContaining('NOT yet confirmed'), findsOneWidget);
      expect(find.textContaining('Appointment confirmed'), findsNothing);
    });
  }

  for (final clinic in <int?>[null, 0, -1]) {
    testWidgets('clinic context $clinic fails closed before enqueue', (
      tester,
    ) async {
      final guard = _guard();
      addTearDown(guard.dispose);
      var count = 0;
      await tester.pumpWidget(
        _screen(
          guard: guard,
          loadSession: () async => _ready(clinic: clinic),
          enqueue: (command) async {
            count++;
            return _result(command, AppointmentCreateEnqueueStatus.queued);
          },
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('appointment-create-denied')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('appointment-create-submit')),
        findsNothing,
      );
      expect(count, 0);
    });
  }

  testWidgets('zero user ID fails closed before enqueue', (tester) async {
    final guard = _guard();
    addTearDown(guard.dispose);
    var count = 0;
    await tester.pumpWidget(
      _screen(
        guard: guard,
        loadSession: () async => _ready(user: 0),
        enqueue: (command) async {
          count++;
          return _result(command, AppointmentCreateEnqueueStatus.queued);
        },
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointment-create-denied')),
      findsOneWidget,
    );
    expect(count, 0);
  });

  testWidgets('empty bearer token fails closed before enqueue', (tester) async {
    final guard = _guard();
    addTearDown(guard.dispose);
    var count = 0;
    await tester.pumpWidget(
      _screen(
        guard: guard,
        loadSession: () async => _ready(token: '   '),
        enqueue: (command) async {
          count++;
          return _result(command, AppointmentCreateEnqueueStatus.queued);
        },
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointment-create-denied')),
      findsOneWidget,
    );
    expect(count, 0);
  });

  for (final kind in <String>['user', 'clinic', 'role']) {
    testWidgets('$kind change without guard notice fails closed at submit', (
      tester,
    ) async {
      final guard = _guard();
      addTearDown(guard.dispose);
      var current = _ready();
      var count = 0;
      await tester.pumpWidget(
        _screen(
          guard: guard,
          loadSession: () async => current,
          enqueue: (command) async {
            count++;
            return _result(command, AppointmentCreateEnqueueStatus.queued);
          },
        ),
      );
      await tester.pumpAndSettle();
      current = switch (kind) {
        'user' => _ready(user: 56),
        'clinic' => _ready(clinic: 9),
        _ => _ready(role: 'nurse'),
      };
      await _submit(tester);
      expect(count, 0);
      expect(
        find.byKey(const ValueKey('appointment-create-denied')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('appointment-create-queued')),
        findsNothing,
      );
    });
  }

  testWidgets('expired guard during async authorization ignores late session', (
    tester,
  ) async {
    final guard = _guard();
    addTearDown(guard.dispose);
    final pending = Completer<AuthSessionState>();
    var count = 0;
    await tester.pumpWidget(
      _screen(
        guard: guard,
        loadSession: () => pending.future,
        enqueue: (command) async {
          count++;
          return _result(command, AppointmentCreateEnqueueStatus.queued);
        },
      ),
    );
    await tester.pump();
    expect(
      find.byKey(const ValueKey('appointment-create-loading')),
      findsOneWidget,
    );
    guard.markSignedOut();
    pending.complete(_ready());
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointment-create-denied')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('appointment-create-ready')),
      findsNothing,
    );
    expect(count, 0);
  });

  testWidgets(
    'clinic guard turnover while queue response pending clears status',
    (tester) async {
      final guard = _guard();
      addTearDown(guard.dispose);
      final pending = Completer<AppointmentCreateEnqueueResult>();
      AppointmentCreateCommand? captured;
      await tester.pumpWidget(
        _screen(
          guard: guard,
          enqueue: (command) {
            captured = command;
            return pending.future;
          },
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
      await tester.pump();
      await tester.pump();
      expect(captured, isNotNull);
      guard.markSignedOut();
      guard.markSessionAvailable();
      pending.complete(
        _result(captured!, AppointmentCreateEnqueueStatus.queued),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('appointment-create-queued')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('appointment-create-status')),
        findsNothing,
      );
      expect(find.textContaining('NOT yet confirmed'), findsNothing);
    },
  );

  testWidgets(
    'three uncertain storage attempts reuse identical logical command',
    (tester) async {
      final guard = _guard();
      addTearDown(guard.dispose);
      final commands = <AppointmentCreateCommand>[];
      await tester.pumpWidget(
        _screen(
          guard: guard,
          enqueue: (command) async {
            commands.add(command);
            throw StateError('SENSITIVE_INTERNAL_PERSISTENCE_FAILURE');
          },
        ),
      );
      await tester.pumpAndSettle();
      for (var i = 0; i < 3; i++) {
        await _submit(tester);
        expect(find.textContaining('persistence is uncertain'), findsOneWidget);
        expect(find.textContaining('SENSITIVE_INTERNAL'), findsNothing);
        expect(
          find.byKey(const ValueKey('appointment-create-queued')),
          findsNothing,
        );
      }
      expect(commands, hasLength(3));
      expect(identical(commands[0], commands[1]), isTrue);
      expect(identical(commands[1], commands[2]), isTrue);
      expect(commands[0].idempotencyKey, commands[2].idempotencyKey);
      expect(commands[0].operationId, commands[2].operationId);
      expect(
        tester
            .widget<TextField>(
              find.byKey(const ValueKey('appointment-create-patient-id')),
            )
            .enabled,
        isFalse,
      );
    },
  );

  for (final mismatch in <String>['operation', 'idempotency']) {
    testWidgets(
      'mismatched $mismatch queue acknowledgement must not claim queued',
      (tester) async {
        final guard = _guard();
        addTearDown(guard.dispose);
        final commands = <AppointmentCreateCommand>[];
        await tester.pumpWidget(
          _screen(
            guard: guard,
            enqueue: (command) async {
              commands.add(command);
              return AppointmentCreateEnqueueResult(
                status: AppointmentCreateEnqueueStatus.queued,
                operationId: mismatch == 'operation'
                    ? 'wrong-operation'
                    : command.operationId,
                idempotencyKey: mismatch == 'idempotency'
                    ? 'wrong-key'
                    : command.idempotencyKey,
              );
            },
          ),
        );
        await tester.pumpAndSettle();
        await _submit(tester);
        expect(
          find.textContaining('acknowledgement was inconsistent'),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('appointment-create-queued')),
          findsNothing,
        );
        await _submit(tester);
        expect(commands, hasLength(2));
        expect(identical(commands[0], commands[1]), isTrue);
      },
    );
  }

  testWidgets(
    'session-unavailable queue response retries only original command',
    (tester) async {
      final guard = _guard();
      addTearDown(guard.dispose);
      final commands = <AppointmentCreateCommand>[];
      await tester.pumpWidget(
        _screen(
          guard: guard,
          enqueue: (command) async {
            commands.add(command);
            return _result(
              command,
              commands.length == 1
                  ? AppointmentCreateEnqueueStatus.sessionUnavailable
                  : AppointmentCreateEnqueueStatus.alreadyQueued,
            );
          },
        ),
      );
      await tester.pumpAndSettle();
      await _submit(tester);
      expect(find.textContaining('Not queued'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('appointment-create-queued')),
        findsNothing,
      );
      await _submit(tester);
      expect(commands, hasLength(2));
      expect(identical(commands[0], commands[1]), isTrue);
      expect(
        find.byKey(const ValueKey('appointment-create-queued')),
        findsOneWidget,
      );
      expect(find.textContaining('NOT yet confirmed'), findsOneWidget);
    },
  );

  testWidgets('equal start and end time blocks queue mutation', (tester) async {
    final guard = _guard();
    addTearDown(guard.dispose);
    var count = 0;
    await tester.pumpWidget(
      _screen(
        guard: guard,
        start: _start,
        end: _start,
        enqueue: (command) async {
          count++;
          return _result(command, AppointmentCreateEnqueueStatus.queued);
        },
      ),
    );
    await tester.pumpAndSettle();
    await _submit(tester);
    expect(count, 0);
    expect(find.textContaining('start before the end'), findsOneWidget);
  });

  test('command serializes UTC timestamps and excludes clinical free-text', () {
    final command = AppointmentCreateCommand(
      patientId: 7,
      staffId: 8,
      scheduledStart: _start,
      scheduledEnd: _end,
      appointmentType: 'follow_up',
      reason: 'SENSITIVE_REASON_NOT_FOR_PERSISTENCE',
      notes: 'SENSITIVE_NOTES_NOT_FOR_PERSISTENCE',
    );
    final payload = command.toPayload();
    expect(payload['patient_id'], 7);
    expect(payload['staff_id'], 8);
    expect(payload['appointment_type'], 'follow_up');
    expect(payload['scheduled_start'], _start.toUtc().toIso8601String());
    expect(payload['scheduled_end'], _end.toUtc().toIso8601String());
    expect(payload.containsKey('reason'), isFalse);
    expect(payload.containsKey('notes'), isFalse);
    expect(payload.toString().contains('SENSITIVE_'), isFalse);
    expect(command.operationId, isNotEmpty);
    expect(command.idempotencyKey, isNotEmpty);
  });

  test(
    'invalid command identity or schedule fails before queue persistence',
    () {
      expect(
        () => AppointmentCreateCommand(
          patientId: 0,
          staffId: 8,
          scheduledStart: _start,
          scheduledEnd: _end,
        ),
        throwsArgumentError,
      );
      expect(
        () => AppointmentCreateCommand(
          patientId: 7,
          staffId: -1,
          scheduledStart: _start,
          scheduledEnd: _end,
        ),
        throwsArgumentError,
      );
      expect(
        () => AppointmentCreateCommand(
          patientId: 7,
          staffId: 8,
          scheduledStart: _end,
          scheduledEnd: _start,
        ),
        throwsArgumentError,
      );
      expect(
        () => AppointmentCreateCommand(
          patientId: 7,
          staffId: 8,
          scheduledStart: _start,
          scheduledEnd: _end,
          operationId: '   ',
        ),
        throwsArgumentError,
      );
      expect(
        () => AppointmentCreateCommand(
          patientId: 7,
          staffId: 8,
          scheduledStart: _start,
          scheduledEnd: _end,
          idempotencyKey: '   ',
        ),
        throwsArgumentError,
      );
    },
  );
}
