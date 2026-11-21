import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/dashboard/data/models/dashboard_extended_sections.dart';

/// Read-only visualization of server-returned dashboard details. No writes,
/// cache, role grant, rounding or inferred count is performed in this widget.
class DashboardExtendedSectionPanel extends StatelessWidget {
  const DashboardExtendedSectionPanel({super.key, required this.section});

  final DashboardExtendedSection section;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 16),
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                section.title,
                style: Theme.of(context).textTheme.titleMedium,
              ),
              if (section.metrics.isNotEmpty) ...[
                const SizedBox(height: 12),
                LayoutBuilder(
                  builder: (context, constraints) {
                    final tileWidth = constraints.maxWidth < 480
                        ? constraints.maxWidth
                        : 215.0;
                    return Wrap(
                      spacing: 10,
                      runSpacing: 10,
                      children: [
                        for (final metric in section.metrics)
                          SizedBox(
                            width: tileWidth,
                            child: Card(
                              child: Padding(
                                padding: const EdgeInsets.all(12),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(metric.label),
                                    const SizedBox(height: 4),
                                    Text(
                                      metric.value,
                                      style: Theme.of(context)
                                          .textTheme
                                          .titleMedium,
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ),
                      ],
                    );
                  },
                ),
              ],
              for (final breakdown in section.breakdowns) ...[
                const SizedBox(height: 12),
                Text(
                  breakdown.title,
                  style: Theme.of(context).textTheme.titleSmall,
                ),
                if (breakdown.entries.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 6),
                    child: Text('No entries returned for this period.'),
                  )
                else
                  for (final entry in breakdown.entries)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      child: Text(entry),
                    ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
