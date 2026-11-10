import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/lab/data/lab_result_finalization_remote_data_source.dart';

// S05B: standalone clinical technician workflow, not router-wired.
// Each clinical action requires its own fresh one-shot adapter.
class LabResultFinalizationScreen extends StatefulWidget {
  const LabResultFinalizationScreen({
    super.key,
    required this.patientId,
    required this.orderId,
    required this.action,
    required this.dataSource,
    this.orderItemId,
  }) : assert(patientId > 0),
       assert(orderId > 0),
       assert(action != LabResultAction.enterResult || orderItemId != null);

  final int patientId;
  final int orderId;
  final int? orderItemId;
  final LabResultAction action;
  final LabResultFinalizationRemoteDataSource dataSource;

  @override
  State<LabResultFinalizationScreen> createState() =>
      _LabResultFinalizationScreenState();
}

class _LabResultFinalizationScreenState
    extends State<LabResultFinalizationScreen> {
  final _value = TextEditingController();
  final _notes = TextEditingController();
  LabResultReview? _review;
  bool _acknowledged = false;
  bool _busy = false;
  String? _error;
  String? _success;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.reader.sessionNavigationGuard.addListener(
      _sessionChanged,
    );
  }

  @override
  void didUpdateWidget(covariant LabResultFinalizationScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.patientId != widget.patientId ||
        oldWidget.orderId != widget.orderId ||
        oldWidget.orderItemId != widget.orderItemId ||
        oldWidget.action != widget.action ||
        !identical(oldWidget.dataSource, widget.dataSource)) {
      oldWidget.dataSource.reader.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      widget.dataSource.reader.sessionNavigationGuard.addListener(
        _sessionChanged,
      );
      _clear('Clinical context changed. Review the order again.');
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.reader.sessionNavigationGuard.removeListener(
      _sessionChanged,
    );
    _value.dispose();
    _notes.dispose();
    super.dispose();
  }

  void _sessionChanged() {
    _clear('Session changed. Clinical review cleared.');
  }

  void _clear(String message) {
    _generation++;
    _value.clear();
    _notes.clear();
    if (!mounted) {
      return;
    }
    setState(() {
      _review = null;
      _acknowledged = false;
      _busy = false;
      _error = message;
      _success = null;
    });
  }

  bool _current(
    int generation,
    int revision,
    LabResultFinalizationRemoteDataSource source,
    int patient,
    int order,
    int? item,
    LabResultAction action,
  ) =>
      mounted &&
      _generation == generation &&
      identical(source, widget.dataSource) &&
      patient == widget.patientId &&
      order == widget.orderId &&
      item == widget.orderItemId &&
      action == widget.action &&
      source.reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      source.reader.sessionNavigationGuard.revision == revision;

  String _message(Object error) {
    if (error is LabResultAlreadyAttempted ||
        error is LabResultOutcomeUnconfirmed) {
      return 'Outcome unconfirmed or action already attempted. Check the server; do not retry this POST.';
    }
    if (error is LabResultScopeUnavailable) {
      return 'Lab technician session unavailable. No clinical write authorized.';
    }
    if (error is LabResultEvidenceChanged ||
        error is ArgumentError ||
        error is FormatException) {
      return 'Order or result evidence is invalid or changed. No clinical write authorized.';
    }
    if (error is ApiException) {
      return 'Server request failed. Recheck the order before any further action.';
    }
    return 'Clinical outcome could not be verified. Never retry automatically.';
  }

  Future<void> _startReview() async {
    if (_busy || _review != null || widget.dataSource.writeAttempted) {
      return;
    }
    final source = widget.dataSource;
    final patient = widget.patientId;
    final order = widget.orderId;
    final item = widget.orderItemId;
    final action = widget.action;
    final revision = source.reader.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _error = null;
      _success = null;
    });
    try {
      final reviewed = await source.review(
        patientId: patient,
        orderId: order,
        orderItemId: item,
        action: action,
        resultValue: _value.text,
        resultNotes: _notes.text,
      );
      if (!_current(
        generation,
        revision,
        source,
        patient,
        order,
        item,
        action,
      )) {
        return;
      }
      setState(() {
        _review = reviewed;
        _acknowledged = false;
        _busy = false;
      });
    } catch (error) {
      if (!_current(
        generation,
        revision,
        source,
        patient,
        order,
        item,
        action,
      )) {
        return;
      }
      setState(() {
        _busy = false;
        _error = _message(error);
      });
    }
  }

  Future<void> _submit() async {
    if (_busy ||
        !_acknowledged ||
        _review == null ||
        widget.dataSource.writeAttempted) {
      return;
    }
    final source = widget.dataSource;
    final reviewed = _review!;
    final patient = widget.patientId;
    final order = widget.orderId;
    final item = widget.orderItemId;
    final action = widget.action;
    final revision = source.reader.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _error = null;
      _success = null;
    });
    try {
      final confirmed = await source.submitOnce(reviewed: reviewed);
      if (!_current(
        generation,
        revision,
        source,
        patient,
        order,
        item,
        action,
      )) {
        return;
      }
      final resultFlag = item == null ? null : confirmed.items[item]?['flag'];
      setState(() {
        _busy = false;
        _review = null;
        _acknowledged = false;
        _success =
            'Server-confirmed order #$order: ${confirmed.order.status.name}. '
            '${resultFlag == null ? '' : 'Server result flag: $resultFlag.'}';
      });
    } catch (error) {
      if (!_current(
        generation,
        revision,
        source,
        patient,
        order,
        item,
        action,
      )) {
        return;
      }
      setState(() {
        _busy = false;
        _review = null;
        _acknowledged = false;
        _error = _message(error);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final actionTitle = switch (widget.action) {
      LabResultAction.enterResult => 'Enter result',
      LabResultAction.verify => 'Verify all results',
      LabResultAction.complete => 'Complete lab order',
    };
    final locked = _busy || _review != null || widget.dataSource.writeAttempted;
    return Scaffold(
      appBar: AppBar(title: Text('Laboratory: $actionTitle')),
      body: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Patient ${widget.patientId} • Order ${widget.orderId}'),
              const Text(
                'One clinical POST only. No automatic replay or offline queue.',
              ),
              if (widget.action == LabResultAction.enterResult) ...[
                Text('Order item ${widget.orderItemId}'),
                TextField(
                  key: const ValueKey('lab-result-value'),
                  controller: _value,
                  enabled: !locked,
                  maxLength: 150,
                  decoration: const InputDecoration(labelText: 'Result value'),
                ),
                TextField(
                  key: const ValueKey('lab-result-notes'),
                  controller: _notes,
                  enabled: !locked,
                  maxLength: 1000,
                  decoration: const InputDecoration(
                    labelText: 'Result notes (optional)',
                  ),
                ),
                const Text(
                  'Result flags, including critical, are determined by the server.',
                ),
              ],
              const SizedBox(height: 12),
              OutlinedButton(
                key: const ValueKey('lab-result-review'),
                onPressed: locked ? null : () => unawaited(_startReview()),
                child: Text('Review $actionTitle'),
              ),
              if (_busy) const CircularProgressIndicator(),
              if (_review != null) ...[
                Text(
                  'Server evidence: ${_review!.evidence.order.status.name}; '
                  '${_review!.evidence.items.length} test item(s).',
                  key: const ValueKey('lab-result-evidence'),
                ),
                CheckboxListTile(
                  key: const ValueKey('lab-result-ack'),
                  value: _acknowledged,
                  onChanged: _busy
                      ? null
                      : (value) => setState(() {
                          _acknowledged = value == true;
                        }),
                  title: const Text(
                    'I verified the patient, order, results and clinical action',
                  ),
                ),
                ElevatedButton(
                  key: const ValueKey('lab-result-submit'),
                  onPressed:
                      _busy ||
                          !_acknowledged ||
                          widget.dataSource.writeAttempted
                      ? null
                      : () => unawaited(_submit()),
                  child: Text('Confirm $actionTitle'),
                ),
              ],
              if (_success != null)
                Text(_success!, key: const ValueKey('lab-result-confirmed')),
              if (_error != null)
                Text(_error!, key: const ValueKey('lab-result-error')),
            ],
          ),
        ),
      ),
    );
  }
}
