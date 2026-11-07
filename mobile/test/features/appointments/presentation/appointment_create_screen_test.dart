import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/core/sync/appointment_create_queue_producer.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_create_screen.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

AuthSessionState _ready({
  String role = 'doctor',
  int? clinic = 3,
  int user = 42,
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's03-test-only-token',
    refreshToken: 's03-refresh',
    userId: user,
    role: role,
    clinicContextId: clinic,
  ),
);

SessionNavigationGuard _guard({bool ready = true}) {
  final guard = SessionNavigationGuard(
    protectedRoutePrefixes: const <String>['/appointments'],
  );
  if (ready) {
    guard.markSessionAvailable();
  } else {
    guard.markSignedOut();
  }
  return guard;
}

final _start = DateTime(2030, 1, 14, 9);
final _end = DateTime(2030, 1, 14, 9, 30);

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

void main() {
  testWidgets(
    'valid submit creates one queued intent, not a confirmed appointment',
    (tester) async {
      final guard = _guard();
      addTearDown(guard.dispose);
      final commands = <AppointmentCreateCommand>[];
      await tester.pumpWidget(
        _screen(
          guard: guard,
          enqueue: (command) async {
            commands.add(command);
            return _result(command, AppointmentCreateEnqueueStatus.queued);
          },
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
      await tester.pumpAndSettle();
      expect(commands, hasLength(1));
      final command = commands.single;
      expect(command.patientId, 7);
      expect(command.staffId, 8);
      expect(command.appointmentType, 'in_person');
      expect(
        command.toPayload().keys,
        containsAll([
          'patient_id',
          'staff_id',
          'scheduled_start',
          'scheduled_end',
        ]),
      );
      expect(command.toPayload().containsKey('reason'), isFalse);
      expect(command.toPayload().containsKey('notes'), isFalse);
      expect(
        find.byKey(const ValueKey('appointment-create-queued')),
        findsOneWidget,
      );
      expect(find.textContaining('NOT yet confirmed'), findsOneWidget);
      expect(find.textContaining('Booked'), findsNothing);
      final button = tester.widget<FilledButton>(
        find.byKey(const ValueKey('appointment-create-submit')),
      );
      expect(button.onPressed, isNull);
    },
  );

  testWidgets(
    'second tap during in-flight persistence never creates another command',
    (tester) async {
      final guard = _guard();
      addTearDown(guard.dispose);
      final pending = Completer<AppointmentCreateEnqueueResult>();
      final commands = <AppointmentCreateCommand>[];
      await tester.pumpWidget(
        _screen(
          guard: guard,
          enqueue: (command) {
            commands.add(command);
            return pending.future;
          },
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
      await tester.pump();
      await tester.pump();
      expect(commands, hasLength(1));
      expect(
        tester
            .widget<FilledButton>(
              find.byKey(const ValueKey('appointment-create-submit')),
            )
            .onPressed,
        isNull,
      );
      pending.complete(
        _result(commands.single, AppointmentCreateEnqueueStatus.queued),
      );
      await tester.pumpAndSettle();
      expect(commands, hasLength(1));
    },
  );

  testWidgets(
    'storage failure retry preserves command, operation ID and idempotency key',
    (tester) async {
      final guard = _guard();
      addTearDown(guard.dispose);
      final commands = <AppointmentCreateCommand>[];
      await tester.pumpWidget(
        _screen(
          guard: guard,
          enqueue: (command) async {
            commands.add(command);
            if (commands.length == 1) throw StateError('secret-storage-error');
            return _result(
              command,
              AppointmentCreateEnqueueStatus.alreadyQueued,
            );
          },
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
      await tester.pumpAndSettle();
      expect(find.textContaining('persistence is uncertain'), findsOneWidget);
      expect(find.textContaining('secret-storage-error'), findsNothing);
      expect(
        find.byKey(const ValueKey('appointment-create-queued')),
        findsNothing,
      );
      expect(
        tester
            .widget<TextField>(
              find.byKey(const ValueKey('appointment-create-patient-id')),
            )
            .enabled,
        isFalse,
      );
      await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
      await tester.pumpAndSettle();
      expect(commands, hasLength(2));
      expect(identical(commands[0], commands[1]), isTrue);
      expect(commands[1].operationId, commands[0].operationId);
      expect(commands[1].idempotencyKey, commands[0].idempotencyKey);
      expect(
        find.byKey(const ValueKey('appointment-create-queued')),
        findsOneWidget,
      );
    },
  );

  for (final role in <String>['patient', 'pharmacist', 'super_admin']) {
    testWidgets('role $role cannot submit an appointment request', (
      tester,
    ) async {
      final guard = _guard();
      addTearDown(guard.dispose);
      var calls = 0;
      await tester.pumpWidget(
        _screen(
          guard: guard,
          loadSession: () async => _ready(role: role),
          enqueue: (command) async {
            calls++;
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
      expect(calls, 0);
    });
  }

  testWidgets('missing clinic cannot submit', (tester) async {
    final guard = _guard();
    addTearDown(guard.dispose);
    var calls = 0;
    await tester.pumpWidget(
      _screen(
        guard: guard,
        loadSession: () async => _ready(clinic: null),
        enqueue: (command) async {
          calls++;
          return _result(command, AppointmentCreateEnqueueStatus.queued);
        },
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointment-create-denied')),
      findsOneWidget,
    );
    expect(calls, 0);
  });

  testWidgets('signed-out guard does not load or enqueue', (tester) async {
    final guard = _guard(ready: false);
    addTearDown(guard.dispose);
    var loaders = 0;
    var calls = 0;
    await tester.pumpWidget(
      _screen(
        guard: guard,
        loadSession: () async {
          loaders++;
          return _ready();
        },
        enqueue: (command) async {
          calls++;
          return _result(command, AppointmentCreateEnqueueStatus.queued);
        },
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointment-create-denied')),
      findsOneWidget,
    );
    expect(loaders, 0);
    expect(calls, 0);
  });

  testWidgets('invalid patient and staff IDs never enqueue', (tester) async {
    final guard = _guard();
    addTearDown(guard.dispose);
    var calls = 0;
    await tester.pumpWidget(
      _screen(
        guard: guard,
        enqueue: (command) async {
          calls++;
          return _result(command, AppointmentCreateEnqueueStatus.queued);
        },
      ),
    );
    await tester.pumpAndSettle();
    for (final bad in <String>['0', '07', '-1', 'text', '2147483648', ' 7']) {
      await tester.enterText(
        find.byKey(const ValueKey('appointment-create-patient-id')),
        bad,
      );
      await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
      await tester.pumpAndSettle();
      expect(
        find.textContaining('Enter valid patient/staff IDs'),
        findsOneWidget,
      );
    }
    await tester.enterText(
      find.byKey(const ValueKey('appointment-create-patient-id')),
      '7',
    );
    await tester.enterText(
      find.byKey(const ValueKey('appointment-create-staff-id')),
      '0',
    );
    await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
    await tester.pumpAndSettle();
    expect(calls, 0);
  });

  testWidgets('end before start cannot enqueue', (tester) async {
    final guard = _guard();
    addTearDown(guard.dispose);
    var calls = 0;
    await tester.pumpWidget(
      _screen(
        guard: guard,
        start: _end,
        end: _start,
        enqueue: (command) async {
          calls++;
          return _result(command, AppointmentCreateEnqueueStatus.queued);
        },
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
    await tester.pumpAndSettle();
    expect(find.textContaining('start before the end'), findsOneWidget);
    expect(calls, 0);
  });

  testWidgets(
    'session changes without a guard notification before submit fail closed',
    (tester) async {
      final guard = _guard();
      addTearDown(guard.dispose);
      var session = _ready();
      var calls = 0;
      await tester.pumpWidget(
        _screen(
          guard: guard,
          loadSession: () async => session,
          enqueue: (command) async {
            calls++;
            return _result(command, AppointmentCreateEnqueueStatus.queued);
          },
        ),
      );
      await tester.pumpAndSettle();
      session = _ready(clinic: 9);
      await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('appointment-create-denied')),
        findsOneWidget,
      );
      expect(calls, 0);
    },
  );

  testWidgets(
    'logout during queue response clears draft and discards late status',
    (tester) async {
      final guard = _guard();
      addTearDown(guard.dispose);
      final pending = Completer<AppointmentCreateEnqueueResult>();
      AppointmentCreateCommand? command;
      await tester.pumpWidget(
        _screen(
          guard: guard,
          enqueue: (value) {
            command = value;
            return pending.future;
          },
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
      await tester.pump();
      await tester.pump();
      expect(command, isNotNull);
      guard.invalidate();
      pending.complete(
        _result(command!, AppointmentCreateEnqueueStatus.queued),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('appointment-create-denied')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('appointment-create-queued')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('appointment-create-patient-id')),
        findsNothing,
      );
    },
  );

  testWidgets('conflict requires manual review and disables new submit', (
    tester,
  ) async {
    final guard = _guard();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _screen(
        guard: guard,
        enqueue: (command) async =>
            _result(command, AppointmentCreateEnqueueStatus.conflict),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('appointment-create-conflict')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('appointment-create-queued')),
      findsNothing,
    );
    expect(
      tester
          .widget<FilledButton>(
            find.byKey(const ValueKey('appointment-create-submit')),
          )
          .onPressed,
      isNull,
    );
  });

  testWidgets('sessionUnavailable result is not shown as queued', (
    tester,
  ) async {
    final guard = _guard();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _screen(
        guard: guard,
        enqueue: (command) async =>
            _result(command, AppointmentCreateEnqueueStatus.sessionUnavailable),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Not queued'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('appointment-create-queued')),
      findsNothing,
    );
  });

  testWidgets('inconsistent queue acknowledgement never claims success', (
    tester,
  ) async {
    final guard = _guard();
    addTearDown(guard.dispose);
    await tester.pumpWidget(
      _screen(
        guard: guard,
        enqueue: (command) async => AppointmentCreateEnqueueResult(
          status: AppointmentCreateEnqueueStatus.queued,
          operationId: 'not-the-same-operation',
          idempotencyKey: 'not-the-same-key',
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('appointment-create-submit')));
    await tester.pumpAndSettle();
    expect(
      find.textContaining('acknowledgement was inconsistent'),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('appointment-create-queued')),
      findsNothing,
    );
  });
}
