import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/problems/data/problem_source.dart';

/// A separate resource-bound projection, not a signed reconciliation or care plan.
/// It does not modify any clinical decision. No patient-ID-only fallback.
class ProblemContextCard extends StatefulWidget {
  const ProblemContextCard({
    super.key,
    required this.source,
    required this.patientId,
    required this.resourceId,
    required this.medication,
  });
  final ProblemSource source;
  final int patientId, resourceId;
  final bool medication;
  @override
  State<ProblemContextCard> createState() => _ProblemContextCardState();
}

class _ProblemContextCardState extends State<ProblemContextCard> {
  ProblemSession? _session;
  ProblemReview? _review;
  bool _busy = false, _error = false;
  int _generation = 0;
  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_clear);
    unawaited(_authorize());
  }

  @override
  void didUpdateWidget(covariant ProblemContextCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source) ||
        oldWidget.patientId != widget.patientId ||
        oldWidget.resourceId != widget.resourceId ||
        oldWidget.medication != widget.medication) {
      oldWidget.source.guard.removeListener(_clear);
      widget.source.guard.addListener(_clear);
      _generation++;
      _session = null;
      _review = null;
      _busy = false;
      _error = false;
      unawaited(_authorize());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.guard.removeListener(_clear);
    super.dispose();
  }

  void _clear() {
    _generation++;
    if (mounted) {
      setState(() {
        _session = null;
        _review = null;
        _busy = false;
        _error = false;
      });
    }
  }

  Future<void> _authorize() async {
    final g = ++_generation;
    try {
      final session = await widget.source.open(
        medicationReview: widget.medication,
      );
      if (mounted && g == _generation && session.current) {
        setState(() => _session = session);
      }
    } on Object {
      /* Deny by default, including unsupported roles. */
    }
  }

  Future<void> _load() async {
    final session = _session;
    if (session == null || !session.current || _busy) {
      return;
    }
    final g = ++_generation;
    setState(() {
      _review = null;
      _error = false;
      _busy = true;
    });
    try {
      final result = await session.workflow(
        widget.patientId,
        widget.resourceId,
        medication: widget.medication,
      );
      if (mounted && g == _generation && session.current) {
        setState(() {
          _review = result;
          _busy = false;
        });
      }
    } on Object {
      if (mounted && g == _generation && session.current) {
        setState(() {
          _review = null;
          _error = true;
          _busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_session?.current != true) {
      return const SizedBox.shrink();
    }
    final review = _review;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              widget.medication
                  ? 'Medication reconciliation: problem review'
                  : 'Nursing and discharge planning: problem review',
              style: Theme.of(context).textTheme.titleSmall,
            ),
            const Text(
              'Current confirmed conditions from the backend. Read only; not a historical snapshot, signed record, treatment recommendation or discharge clearance.',
            ),
            OutlinedButton(
              key: const ValueKey('problem-context-load'),
              onPressed: _busy ? null : () => unawaited(_load()),
              child: Text(
                review == null
                    ? 'Review confirmed current problems'
                    : 'Refresh current problem review',
              ),
            ),
            if (_busy) const LinearProgressIndicator(),
            if (_error)
              const Text(
                'Problem review unavailable. No clinical conclusion can be drawn.',
              ),
            if (review != null) ...[
              Text('Evaluated: ${review.evaluatedAt}'),
              if (review.conditions.isEmpty)
                const Text(
                  'No confirmed current problems returned. This does not exclude other clinical risks.',
                ),
              for (final p in review.conditions)
                ListTile(
                  title: Text(p.name),
                  subtitle: Text(
                    '${p.clinicalStatus} · confirmed${p.code == null ? '' : ' · ${p.code} (${p.codingSystem})'} · v${p.version}',
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}
