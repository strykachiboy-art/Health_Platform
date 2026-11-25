import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/blood_bank/presentation/blood_bank_workspace.dart';
import 'package:clinic_system_pro/features/blood_bank/presentation/blood_bank_bedside.dart';

import 'blood_bank_contract_test.dart' show policyRow, orderRow;
import 'blood_bank_source_test.dart' show ready, source, response;

void main() {
  testWidgets('admin workspace does not offer bedside administration', (
    tester,
  ) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    final client = MockClient((_) async => response(policyRow()));
    final src = source(client, guard, () async => ready(role: 'admin'));
    await tester.pumpWidget(MaterialApp(home: BloodBankWorkspace(source: src)));
    await tester.pumpAndSettle();
    expect(find.text('Blood Bank'), findsOneWidget);
    expect(find.text('Stock summary'), findsOneWidget);
    expect(find.text('Bedside blood barcode verification'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    guard.dispose();
    client.close();
  });
  testWidgets(
    'issued component does not enable transfusion start before camera proof',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(900, 1600));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final guard = SessionNavigationGuard()..markSessionAvailable();
      final client = MockClient(
        (req) async => response(
          req.url.path.endsWith('/offline-policy') ? policyRow() : orderRow(),
        ),
      );
      final src = source(client, guard, () async => ready(role: 'nurse'));
      final session = await tester.runAsync(src.open);
      await tester.pumpWidget(
        MaterialApp(home: BbBedside(session: session!, orderId: 19)),
      );
      await tester.pumpAndSettle();
      expect(find.text('Verify both barcodes with server'), findsOneWidget);
      final start = find.widgetWithText(
        FilledButton,
        'Record verified transfusion start',
      );
      await tester.ensureVisible(start);
      expect(tester.widget<FilledButton>(start).onPressed, isNull);
      await tester.pumpWidget(const SizedBox());
      guard.dispose();
      client.close();
    },
  );
}
