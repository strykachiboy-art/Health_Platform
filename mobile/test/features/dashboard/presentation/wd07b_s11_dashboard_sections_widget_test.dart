import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/features/dashboard/data/models/dashboard_extended_sections.dart';
import 'package:clinic_system_pro/features/dashboard/presentation/dashboard_extended_section_panel.dart';

void main() {
  testWidgets('S11 Windows card renders verified decimal and breakdown', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ListView(
            children: const <Widget>[
              DashboardExtendedSectionPanel(
                section: DashboardExtendedSection(
                  'AI usage and safety',
                  <DashboardExtendedMetric>[
                    DashboardExtendedMetric(
                      'Estimated AI cost',
                      '9007199254740993.12345678',
                    ),
                  ],
                  <DashboardExtendedBreakdown>[
                    DashboardExtendedBreakdown('AI usage by feature', <String>[
                      'clinical summary — Requests: 3',
                    ]),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('AI usage and safety'), findsOneWidget);
    expect(find.text('9007199254740993.12345678'), findsOneWidget);
    expect(find.textContaining('Requests: 3'), findsOneWidget);
  });

  testWidgets('S11 empty breakdown never displays fabricated values', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: DashboardExtendedSectionPanel(
            section: DashboardExtendedSection(
              'AI usage',
              <DashboardExtendedMetric>[],
              <DashboardExtendedBreakdown>[
                DashboardExtendedBreakdown('Feature usage', <String>[]),
              ],
            ),
          ),
        ),
      ),
    );
    expect(find.text('No entries returned for this period.'), findsOneWidget);
    expect(find.text('0'), findsNothing);
  });
}
