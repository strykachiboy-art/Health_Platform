import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/main_wd04_preview.dart' as preview;

void main() {
  testWidgets(
    'WD04 preview labels all data as synthetic and cannot launch actions',
    (tester) async {
      await tester.pumpWidget(const preview.Wd04OfflinePreviewApp());
      expect(
        find.byKey(const ValueKey('wd04-preview-warning')),
        findsOneWidget,
      );
      expect(
        find.textContaining('No login, backend connection'),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('wd04-preview-emar')), findsOneWidget);
      expect(
        find.byKey(const ValueKey('wd04-preview-ambulance')),
        findsNothing,
      );
      await tester.tap(find.byKey(const ValueKey('wd04-preview-emar')));
      await tester.pump();
      expect(find.textContaining('Offline preview only'), findsOneWidget);
    },
  );
}
