// P23G.S05 - clinician UI fail-closed and unknown-outcome safety tests.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/presentation/medication_reconciliation_finalization_screen.dart';

import '../data/medication_reconciliation_finalization_remote_data_source_test.dart';

Widget _app(
  S04FakeTransport transport, {
  AuthSessionState Function()? session,
}) => MaterialApp(
  home: MedicationReconciliationFinalizationScreen(
    source: s04Source(transport, session: session),
    reconciliationId: 19,
    clinicId: 3,
    patientId: 7,
    admissionId: 11,
  ),
);

class _OfflineTransport extends S04FakeTransport {
  _OfflineTransport(super.record);
  @override
  Future<Map<String, dynamic>> get(String path, String token) async =>
      throw StateError('Offline read');
}

void main() {
  testWidgets('finalization confirmation can be cancelled without a POST', (
    tester,
  ) async {
    final transport = S04FakeTransport(s04ExampleRecord());
    await tester.pumpWidget(_app(transport));
    await tester.pumpAndSettle();
    final action = find.byKey(const Key('s04-finalize'));
    await tester.ensureVisible(action);
    await tester.tap(action);
    await tester.pumpAndSettle();
    expect(find.text('Finalize medication reconciliation?'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(transport.posts, 0);
    expect(find.byKey(const Key('s04-finalize')), findsOneWidget);
  });

  testWidgets('draft changed after dialog opens is rejected, no write sent', (
    tester,
  ) async {
    final transport = S04FakeTransport(s04ExampleRecord());
    await tester.pumpWidget(_app(transport));
    await tester.pumpAndSettle();
    final action = find.byKey(const Key('s04-finalize'));
    await tester.ensureVisible(action);
    await tester.tap(action);
    await tester.pumpAndSettle();
    transport.record['updated_at'] = '2026-11-10T10:01:00+00:00';
    await tester.tap(find.text('Confirm finalization'));
    await tester.pumpAndSettle();
    expect(transport.posts, 0);
    expect(
      find.textContaining('The draft changed or is unresolved'),
      findsOneWidget,
    );
    expect(find.byKey(const Key('s04-finalize')), findsNothing);
    expect(find.textContaining('write attempt locked'), findsOneWidget);
  });

  testWidgets(
    'nurse and cross-clinic context cannot see clinical mutation actions',
    (tester) async {
      for (final session in <AuthSessionState>[
        const AuthSessionState.ready(
          AuthTokenResponse(
            accessToken: 'token',
            refreshToken: 'refresh',
            userId: 5,
            role: 'nurse',
            clinicContextId: 3,
          ),
        ),
        const AuthSessionState.ready(
          AuthTokenResponse(
            accessToken: 'token',
            refreshToken: 'refresh',
            userId: 5,
            role: 'doctor',
            clinicContextId: 99,
          ),
        ),
      ]) {
        final transport = S04FakeTransport(s04ExampleRecord());
        await tester.pumpWidget(_app(transport, session: () => session));
        await tester.pumpAndSettle();
        expect(
          find.textContaining('Unable to verify current server record'),
          findsOneWidget,
        );
        expect(find.byKey(const Key('s04-finalize')), findsNothing);
        expect(find.byKey(const Key('s04-amend')), findsNothing);
        expect(transport.posts, 0);
        await tester.pumpWidget(const SizedBox.shrink());
        await tester.pumpAndSettle();
      }
    },
  );

  testWidgets('offline GET leaves no write action and sends no POST', (
    tester,
  ) async {
    final transport = _OfflineTransport(s04ExampleRecord());
    await tester.pumpWidget(_app(transport));
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Unable to verify current server record'),
      findsOneWidget,
    );
    expect(find.byKey(const Key('s04-finalize')), findsNothing);
    expect(transport.posts, 0);
  });

  testWidgets('wrong-patient readback is not displayed as finalizable', (
    tester,
  ) async {
    final transport = S04FakeTransport(
      s04ExampleRecord()..['patient_id'] = 200,
    );
    await tester.pumpWidget(_app(transport));
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Unable to verify current server record'),
      findsOneWidget,
    );
    expect(find.byKey(const Key('s04-finalize')), findsNothing);
    expect(transport.posts, 0);
  });

  testWidgets('amendment requires both a reason and genuinely changed notes', (
    tester,
  ) async {
    final transport = S04FakeTransport(s04ExampleRecord(status: 'final'));
    await tester.pumpWidget(_app(transport));
    await tester.pumpAndSettle();
    final amend = find.byKey(const Key('s04-amend'));
    await tester.ensureVisible(amend);
    await tester.tap(amend);
    await tester.pumpAndSettle();
    final submit = find.text('Submit amendment');
    await tester.ensureVisible(submit);
    await tester.tap(submit);
    await tester.pumpAndSettle();
    expect(find.text('Amend finalized reconciliation notes'), findsOneWidget);
    expect(transport.posts, 0);
    await tester.enterText(
      find.byKey(const Key('s04-amend-reason')),
      'Correction',
    );
    await tester.ensureVisible(submit);
    await tester.tap(submit);
    await tester.pumpAndSettle();
    expect(find.text('Amend finalized reconciliation notes'), findsOneWidget);
    expect(transport.posts, 0);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(transport.posts, 0);
  });

  testWidgets(
    'amendment timeout remains unconfirmed and locked after refresh',
    (tester) async {
      final transport = S04FakeTransport(s04ExampleRecord(status: 'final'))
        ..failWrite = true;
      await tester.pumpWidget(_app(transport));
      await tester.pumpAndSettle();
      final amend = find.byKey(const Key('s04-amend'));
      await tester.ensureVisible(amend);
      await tester.tap(amend);
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const Key('s04-amend-reason')),
        'Correction following review',
      );
      await tester.enterText(
        find.byKey(const Key('s04-amend-notes')),
        'Clinically corrected notes',
      );
      final submit = find.text('Submit amendment');
      await tester.ensureVisible(submit);
      await tester.tap(submit);
      await tester.pumpAndSettle();
      expect(transport.posts, 1);
      expect(
        find.textContaining('Amendment outcome unconfirmed'),
        findsOneWidget,
      );
      expect(find.byKey(const Key('s04-amend')), findsNothing);
      final refresh = find.byKey(const Key('s04-refresh'));
      await tester.ensureVisible(refresh);
      await tester.tap(refresh);
      await tester.pumpAndSettle();
      expect(transport.posts, 1);
      expect(find.textContaining('write attempt locked'), findsOneWidget);
      expect(find.byKey(const Key('s04-amend')), findsNothing);
    },
  );
}
