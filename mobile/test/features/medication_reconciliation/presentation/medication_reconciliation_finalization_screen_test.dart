import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/presentation/medication_reconciliation_finalization_screen.dart';

import '../data/medication_reconciliation_finalization_remote_data_source_test.dart';

Widget s04TestApp(S04FakeTransport transport) => MaterialApp(
  home: MedicationReconciliationFinalizationScreen(
    source: s04Source(transport),
    reconciliationId: 19,
    clinicId: 3,
    patientId: 7,
    admissionId: 11,
  ),
);

void main() {
  testWidgets('reviewed draft requires explicit finalization confirmation', (
    tester,
  ) async {
    final transport = S04FakeTransport(s04ExampleRecord());
    await tester.pumpWidget(s04TestApp(transport));
    await tester.pumpAndSettle();
    expect(find.textContaining('Status: draft'), findsOneWidget);
    expect(find.textContaining('Decision: continue'), findsOneWidget);
    final button = find.byKey(const Key('s04-finalize'));
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pumpAndSettle();
    expect(transport.posts, 0);
    expect(find.text('Finalize medication reconciliation?'), findsOneWidget);
    await tester.tap(find.text('Confirm finalization'));
    await tester.pumpAndSettle();
    expect(transport.posts, 1);
    expect(find.textContaining('Status: final'), findsOneWidget);
    expect(find.text('Finalization confirmed by the server.'), findsOneWidget);
    expect(find.byKey(const Key('s04-finalize')), findsNothing);
    expect(find.textContaining('write attempt locked'), findsOneWidget);
  });

  testWidgets('unresolved medication blocks clinical finalization', (
    tester,
  ) async {
    final transport = S04FakeTransport(s04ExampleRecord(decision: 'unknown'));
    await tester.pumpWidget(s04TestApp(transport));
    await tester.pumpAndSettle();
    expect(find.textContaining('Finalization is blocked'), findsOneWidget);
    expect(find.byKey(const Key('s04-finalize')), findsNothing);
    expect(transport.posts, 0);
  });

  testWidgets(
    'finalized reconciliation accepts reasoned notes-only amendment',
    (tester) async {
      final transport = S04FakeTransport(s04ExampleRecord(status: 'final'));
      await tester.pumpWidget(s04TestApp(transport));
      await tester.pumpAndSettle();
      final amend = find.byKey(const Key('s04-amend'));
      await tester.ensureVisible(amend);
      await tester.tap(amend);
      await tester.pumpAndSettle();
      expect(transport.posts, 0);
      await tester.enterText(
        find.byKey(const Key('s04-amend-reason')),
        'Reviewed corrected note',
      );
      await tester.enterText(
        find.byKey(const Key('s04-amend-notes')),
        'Updated notes',
      );
      final submit = find.text('Submit amendment');
      await tester.ensureVisible(submit);
      await tester.tap(submit);
      await tester.pumpAndSettle();
      expect(transport.posts, 1);
      expect(find.textContaining('Status: amended'), findsOneWidget);
      expect(
        find.text('Amendment confirmed by server readback.'),
        findsOneWidget,
      );
    },
  );

  testWidgets('unknown POST outcome cannot be resubmitted from screen', (
    tester,
  ) async {
    final transport = S04FakeTransport(s04ExampleRecord())..failWrite = true;
    await tester.pumpWidget(s04TestApp(transport));
    await tester.pumpAndSettle();
    final button = find.byKey(const Key('s04-finalize'));
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Confirm finalization'));
    await tester.pumpAndSettle();
    expect(transport.posts, 1);
    expect(find.textContaining('Finalization unconfirmed'), findsOneWidget);
    expect(find.byKey(const Key('s04-finalize')), findsNothing);
    final refresh = find.byKey(const Key('s04-refresh'));
    await tester.ensureVisible(refresh);
    await tester.tap(refresh);
    await tester.pumpAndSettle();
    expect(transport.posts, 1);
    expect(find.byKey(const Key('s04-finalize')), findsNothing);
  });
}
