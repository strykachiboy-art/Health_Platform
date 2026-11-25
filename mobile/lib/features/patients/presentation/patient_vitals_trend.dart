import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_vitals.dart';

enum VitalTrendMetric {
  systolic('Systolic blood pressure', 'mmHg'),
  diastolic('Diastolic blood pressure', 'mmHg'),
  heartRate('Heart rate', 'bpm'),
  temperature('Temperature', '°C'),
  oxygen('Oxygen saturation', '%'),
  respiratoryRate('Respiratory rate', 'breaths/min'),
  weight('Weight', 'kg'),
  height('Height', 'cm');

  const VitalTrendMetric(this.label, this.unit);
  final String label;
  final String unit;

  String? recordedValue(PatientVitals row) => switch (this) {
    VitalTrendMetric.systolic => row.bloodPressureSystolic?.toString(),
    VitalTrendMetric.diastolic => row.bloodPressureDiastolic?.toString(),
    VitalTrendMetric.heartRate => row.heartRate?.toString(),
    VitalTrendMetric.temperature => row.temperature,
    VitalTrendMetric.oxygen => row.oxygenSaturation,
    VitalTrendMetric.respiratoryRate => row.respiratoryRate?.toString(),
    VitalTrendMetric.weight => row.weight,
    VitalTrendMetric.height => row.height,
  };
}

class VitalTrendPoint {
  const VitalTrendPoint(this.recordedAt, this.raw, this.value);
  final DateTime recordedAt;
  final String raw;
  final double value;
}

/// Use at most the newest 50 loaded observations. Missing measurements are
/// omitted, not treated as zero. The original precision stays in the raw string.
List<VitalTrendPoint> buildVitalTrend(
  List<PatientVitals> observations,
  VitalTrendMetric metric,
) {
  final rows = [...observations]
    ..sort((a, b) {
      final time = a.recordedAt.compareTo(b.recordedAt);
      return time == 0 ? a.id.compareTo(b.id) : time;
    });
  final points = <VitalTrendPoint>[];
  for (final row in rows.skip(math.max(0, rows.length - 50))) {
    final raw = metric.recordedValue(row);
    final value = raw == null ? null : double.tryParse(raw);
    if (raw != null && value != null && value.isFinite) {
      points.add(VitalTrendPoint(row.recordedAt.toUtc(), raw, value));
    }
  }
  return List.unmodifiable(points);
}

/// Visualization only. No extra reads, medical classification or interpolation.
class PatientVitalsTrend extends StatefulWidget {
  const PatientVitalsTrend({super.key, required this.observations});
  final List<PatientVitals> observations;

  @override
  State<PatientVitalsTrend> createState() => _PatientVitalsTrendState();
}

class _PatientVitalsTrendState extends State<PatientVitalsTrend> {
  VitalTrendMetric _metric = VitalTrendMetric.systolic;

  @override
  Widget build(BuildContext context) {
    final points = buildVitalTrend(widget.observations, _metric);
    return ExpansionTile(
      key: const ValueKey('clinical-vitals-trend'),
      title: const Text('Vital-sign trends'),
      children: [
        DropdownButton<VitalTrendMetric>(
          key: const ValueKey('clinical-vitals-metric'),
          isExpanded: true,
          value: _metric,
          items: [
            for (final metric in VitalTrendMetric.values)
              DropdownMenuItem(value: metric, child: Text(metric.label)),
          ],
          onChanged: (metric) {
            if (metric != null) {
              setState(() => _metric = metric);
            }
          },
        ),
        Text(
          'Recorded ${_metric.label} (${_metric.unit}). UTC time axis. '
          'Newest 50 loaded observations at most; not the complete record. '
          'Missing measurements are omitted. No clinical interpretation.',
        ),
        if (points.isEmpty)
          const Text('No plottable measurements in the loaded observations.')
        else ...[
          const SizedBox(height: 8),
          Semantics(
            label:
                '${_metric.label} recorded measurements. ${points.length} points. '
                'Exact values and timestamps are listed below.',
            child: SizedBox(
              height: 150,
              width: double.infinity,
              child: CustomPaint(
                key: const ValueKey('clinical-vitals-plot'),
                painter: _TrendPainter(
                  points,
                  Theme.of(context).colorScheme.primary,
                ),
              ),
            ),
          ),
          Text(
            'From ${points.first.recordedAt.toIso8601String()} '
            'to ${points.last.recordedAt.toIso8601String()}',
          ),
          Text(
            'Recorded range: ${_rangePoint(points, true).raw} to '
            '${_rangePoint(points, false).raw} ${_metric.unit}',
          ),
          // Accessible exact values, including measurements sharing a timestamp.
          for (final point in points)
            Text(
              '${point.recordedAt.toIso8601String()}: ${point.raw} ${_metric.unit}',
            ),
        ],
      ],
    );
  }
}

VitalTrendPoint _rangePoint(List<VitalTrendPoint> points, bool minimum) =>
    points.reduce(
      (a, b) => (minimum ? a.value <= b.value : a.value >= b.value) ? a : b,
    );

class _TrendPainter extends CustomPainter {
  const _TrendPainter(this.points, this.color);
  final List<VitalTrendPoint> points;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    if (size.width < 16 || size.height < 16 || points.isEmpty) {
      return;
    }
    final bounds = Rect.fromLTWH(8, 8, size.width - 16, size.height - 16);
    final axes = Paint()
      ..color = color.withAlpha(100)
      ..style = PaintingStyle.stroke;
    canvas.drawRect(bounds, axes);
    final dots = Paint()..color = color;
    final low = _rangePoint(points, true).value;
    final high = _rangePoint(points, false).value;
    final duration = points.last.recordedAt
        .difference(points.first.recordedAt)
        .inMicroseconds;
    // Scatter plot (no interpolated values or line through missing observations).
    for (final point in points) {
      final elapsed = point.recordedAt
          .difference(points.first.recordedAt)
          .inMicroseconds;
      final x = duration == 0 ? 0.5 : elapsed / duration;
      final y = high == low ? 0.5 : (point.value - low) / (high - low);
      if (x.isFinite && y.isFinite) {
        canvas.drawCircle(
          Offset(
            bounds.left + bounds.width * x,
            bounds.bottom - bounds.height * y,
          ),
          3.5,
          dots,
        );
      }
    }
  }

  @override
  bool shouldRepaint(covariant _TrendPainter oldDelegate) =>
      oldDelegate.points != points || oldDelegate.color != color;
}
