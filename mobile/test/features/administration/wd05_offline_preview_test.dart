import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/main_wd05_preview.dart' as preview;

void main() {
  testWidgets('WD05 standalone preview offers synthetic links only', (
    tester,
  ) async {
    await tester.pumpWidget(const preview.Wd05OfflinePreviewApp());
    expect(find.byKey(const ValueKey('wd05-preview-warning')), findsOneWidget);
    expect(find.textContaining('No login, backend calls'), findsOneWidget);
    expect(find.byKey(const ValueKey('wd05-preview-billing')), findsOneWidget);
    expect(find.byKey(const ValueKey('wd05-preview-reports')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('wd05-preview-billing')));
    await tester.pump();
    expect(find.textContaining('Preview only'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('wd05-preview-role')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('patient').last);
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('wd05-preview-billing')), findsNothing);
    expect(find.byKey(const ValueKey('wd05-preview-reports')), findsNothing);
  });
}
