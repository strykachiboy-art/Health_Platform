import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/main_wd06_preview.dart' as preview;

void main() {
  testWidgets(
    'WD06 synthetic preview cannot trigger a real coordination action',
    (tester) async {
      await tester.pumpWidget(const preview.Wd06OfflinePreviewApp());
      expect(
        find.byKey(const ValueKey('wd06-preview-warning')),
        findsOneWidget,
      );
      expect(find.textContaining('No login, backend checks'), findsOneWidget);
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('wd06-preview-chat')),
        180,
        scrollable: find.byType(Scrollable).last,
      );
      expect(find.byKey(const ValueKey('wd06-preview-chat')), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('wd06-preview-chat')));
      await tester.pump();
      expect(
        find.textContaining('Offline layout preview only'),
        findsOneWidget,
      );
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('wd06-preview-role')),
        -180,
        scrollable: find.byType(Scrollable).last,
      );
      await tester.tap(find.byKey(const ValueKey('wd06-preview-role')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('patient').last);
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('wd06-preview-chat')), findsNothing);
      expect(
        find.byKey(const ValueKey('wd06-preview-clinical_ai')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('wd06-preview-pending_work')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('wd06-preview-notifications')),
        findsOneWidget,
      );
    },
  );
}
