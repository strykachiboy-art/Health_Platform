import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_create_queue_state_read_model.dart';
import 'package:clinic_system_pro/features/appointments/presentation/appointment_create_sync_status_panel.dart';

AppointmentCreateQueueSummary _summary(
  AppointmentCreateQueueDisplayState state, {
  String id = 'sensitive-operation-reference',
  int attempts = 0,
}) {
  return AppointmentCreateQueueSummary(
    operationId: id,
    state: state,
    attemptsCompleted: attempts,
    createdAt: DateTime.utc(2026, 11, 7),
  );
}

AppointmentCreateQueuePage _ready(
  List<AppointmentCreateQueueSummary> items, {
  int page = 1,
  bool hasNext = false,
}) {
  return AppointmentCreateQueuePage.ready(
    items: items,
    page: page,
    hasNext: hasNext,
  );
}

Widget _app(
  SessionNavigationGuard guard,
  AppointmentCreateSyncPageLoader loadPage,
) {
  return MaterialApp(
    home: Scaffold(
      body: SingleChildScrollView(
        child: AppointmentCreateSyncStatusPanel(
          sessionNavigationGuard: guard,
          loadPage: loadPage,
        ),
      ),
    ),
  );
}

void main() {
  late SessionNavigationGuard guard;

  setUp(() {
    guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/appointments'],
    );
  });
  tearDown(() => guard.dispose());

  testWidgets('signed out never calls scoped read', (tester) async {
    guard.markSignedOut();
    var calls = 0;
    Future<AppointmentCreateQueuePage> load({
      required int page,
      required int pageSize,
    }) async {
      calls++;
      return _ready(<AppointmentCreateQueueSummary>[]);
    }

    await tester.pumpWidget(_app(guard, load));
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(find.byKey(const ValueKey('s05c-session-denied')), findsOneWidget);
    expect(find.byKey(const ValueKey('s05c-refresh')), findsNothing);
  });

  testWidgets('checking session never reads queue', (tester) async {
    var calls = 0;
    Future<AppointmentCreateQueuePage> load({
      required int page,
      required int pageSize,
    }) async {
      calls++;
      return _ready(<AppointmentCreateQueueSummary>[]);
    }

    await tester.pumpWidget(_app(guard, load));
    await tester.pumpAndSettle();
    expect(calls, 0);
  });

  testWidgets('initial valid session loads pending but never claims booking', (
    tester,
  ) async {
    guard.markSessionAvailable();
    var calls = 0;
    Future<AppointmentCreateQueuePage> load({
      required int page,
      required int pageSize,
    }) async {
      calls++;
      expect(page, 1);
      expect(pageSize, 20);
      return _ready(<AppointmentCreateQueueSummary>[
        _summary(AppointmentCreateQueueDisplayState.pending),
      ]);
    }

    await tester.pumpWidget(_app(guard, load));
    await tester.pumpAndSettle();
    expect(calls, 1);
    expect(find.textContaining('Queued locally'), findsOneWidget);
    expect(find.textContaining('not yet confirmed'), findsOneWidget);
    expect(find.textContaining('Booked'), findsNothing);
    expect(find.textContaining('sensitive-operation-reference'), findsNothing);
  });

  testWidgets('all nine lifecycle projections remain visibly distinct', (
    tester,
  ) async {
    guard.markSessionAvailable();
    Future<AppointmentCreateQueuePage> load({
      required int page,
      required int pageSize,
    }) async {
      return _ready(<AppointmentCreateQueueSummary>[
        for (final state in AppointmentCreateQueueDisplayState.values)
          _summary(state, id: 'private-${state.name}'),
      ]);
    }

    await tester.pumpWidget(_app(guard, load));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('s05c-entry-8')), findsOneWidget);
    expect(find.textContaining('Sending — outcome'), findsOneWidget);
    expect(find.textContaining('Retry scheduled'), findsOneWidget);
    expect(find.textContaining('reconciliation required'), findsOneWidget);
    expect(find.textContaining('Conflict requires review'), findsOneWidget);
    expect(find.textContaining('Server acknowledgement'), findsOneWidget);
    expect(find.textContaining('Request rejected'), findsOneWidget);
    expect(find.textContaining('User intervention'), findsOneWidget);
    expect(find.textContaining('cancelled'), findsOneWidget);
    expect(find.textContaining('private-'), findsNothing);
  });

  testWidgets('empty page is not an error or booking', (tester) async {
    guard.markSessionAvailable();
    Future<AppointmentCreateQueuePage> load({
      required int page,
      required int pageSize,
    }) async {
      return _ready(<AppointmentCreateQueueSummary>[]);
    }

    await tester.pumpWidget(_app(guard, load));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('s05c-empty')), findsOneWidget);
    expect(find.byKey(const ValueKey('s05c-unavailable')), findsNothing);
  });

  testWidgets('model-unavailable does not leak raw errors', (tester) async {
    guard.markSessionAvailable();
    Future<AppointmentCreateQueuePage> load({
      required int page,
      required int pageSize,
    }) async {
      return AppointmentCreateQueuePage.unavailable(page: page);
    }

    await tester.pumpWidget(_app(guard, load));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('s05c-unavailable')), findsOneWidget);
    expect(find.byKey(const ValueKey('s05c-entry-0')), findsNothing);
  });

  testWidgets(
    'raw storage failure is hidden behind generic unavailable state',
    (tester) async {
      guard.markSessionAvailable();
      Future<AppointmentCreateQueuePage> load({
        required int page,
        required int pageSize,
      }) async {
        throw StateError('SECRET_DATABASE_ROW');
      }

      await tester.pumpWidget(_app(guard, load));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('s05c-unavailable')), findsOneWidget);
      expect(find.textContaining('SECRET_DATABASE_ROW'), findsNothing);
    },
  );

  testWidgets('logout while pending discards stale response', (tester) async {
    guard.markSessionAvailable();
    final pending = Completer<AppointmentCreateQueuePage>();
    Future<AppointmentCreateQueuePage> load({
      required int page,
      required int pageSize,
    }) => pending.future;
    await tester.pumpWidget(_app(guard, load));
    await tester.pump();
    guard.markSignedOut();
    await tester.pump();
    pending.complete(
      _ready(<AppointmentCreateQueueSummary>[
        _summary(AppointmentCreateQueueDisplayState.pending),
      ]),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('s05c-session-denied')), findsOneWidget);
    expect(find.byKey(const ValueKey('s05c-entry-0')), findsNothing);
  });

  testWidgets('invalidated session clears already shown metadata', (
    tester,
  ) async {
    guard.markSessionAvailable();
    Future<AppointmentCreateQueuePage> load({
      required int page,
      required int pageSize,
    }) async {
      return _ready(<AppointmentCreateQueueSummary>[
        _summary(AppointmentCreateQueueDisplayState.pending),
      ]);
    }

    await tester.pumpWidget(_app(guard, load));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('s05c-entry-0')), findsOneWidget);
    guard.invalidate();
    await tester.pump();
    expect(find.byKey(const ValueKey('s05c-session-denied')), findsOneWidget);
    expect(find.byKey(const ValueKey('s05c-entry-0')), findsNothing);
  });

  testWidgets('switching session revision discards first response', (
    tester,
  ) async {
    guard.markSessionAvailable();
    final old = Completer<AppointmentCreateQueuePage>();
    var calls = 0;
    Future<AppointmentCreateQueuePage> load({
      required int page,
      required int pageSize,
    }) {
      calls++;
      if (calls == 1) return old.future;
      return Future<AppointmentCreateQueuePage>.value(
        _ready(<AppointmentCreateQueueSummary>[]),
      );
    }

    await tester.pumpWidget(_app(guard, load));
    await tester.pump();
    guard.markSignedOut();
    guard.markSessionAvailable();
    await tester.pumpAndSettle();
    old.complete(
      _ready(<AppointmentCreateQueueSummary>[
        _summary(AppointmentCreateQueueDisplayState.pending),
      ]),
    );
    await tester.pumpAndSettle();
    expect(calls, 2);
    expect(find.byKey(const ValueKey('s05c-empty')), findsOneWidget);
    expect(find.byKey(const ValueKey('s05c-entry-0')), findsNothing);
  });

  testWidgets('refresh reloads but cannot send queued operation', (
    tester,
  ) async {
    guard.markSessionAvailable();
    var calls = 0;
    Future<AppointmentCreateQueuePage> load({
      required int page,
      required int pageSize,
    }) async {
      calls++;
      return _ready(<AppointmentCreateQueueSummary>[]);
    }

    await tester.pumpWidget(_app(guard, load));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('s05c-refresh')));
    await tester.pumpAndSettle();
    expect(calls, 2);
    expect(find.textContaining('Retry now'), findsNothing);
    expect(find.textContaining('Send again'), findsNothing);
  });

  testWidgets('next and previous page are strictly bounded', (tester) async {
    guard.markSessionAvailable();
    final requested = <int>[];
    Future<AppointmentCreateQueuePage> load({
      required int page,
      required int pageSize,
    }) async {
      requested.add(page);
      return _ready(
        <AppointmentCreateQueueSummary>[],
        page: page,
        hasNext: page == 1,
      );
    }

    await tester.pumpWidget(_app(guard, load));
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<TextButton>(find.byKey(const ValueKey('s05c-previous')))
          .onPressed,
      isNull,
    );
    await tester.tap(find.byKey(const ValueKey('s05c-next')));
    await tester.pumpAndSettle();
    expect(requested, <int>[1, 2]);
    expect(
      tester
          .widget<TextButton>(find.byKey(const ValueKey('s05c-next')))
          .onPressed,
      isNull,
    );
    await tester.tap(find.byKey(const ValueKey('s05c-previous')));
    await tester.pumpAndSettle();
    expect(requested, <int>[1, 2, 1]);
  });

  testWidgets('new loader resets current page to one', (tester) async {
    guard.markSessionAvailable();
    Future<AppointmentCreateQueuePage> oldLoader({
      required int page,
      required int pageSize,
    }) async {
      return _ready(
        <AppointmentCreateQueueSummary>[],
        page: page,
        hasNext: true,
      );
    }

    final requested = <int>[];
    Future<AppointmentCreateQueuePage> newLoader({
      required int page,
      required int pageSize,
    }) async {
      requested.add(page);
      return _ready(<AppointmentCreateQueueSummary>[], page: page);
    }

    await tester.pumpWidget(_app(guard, oldLoader));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('s05c-next')));
    await tester.pumpAndSettle();
    await tester.pumpWidget(_app(guard, newLoader));
    await tester.pumpAndSettle();
    expect(requested, <int>[1]);
    expect(find.text('Page 1'), findsOneWidget);
  });
}
