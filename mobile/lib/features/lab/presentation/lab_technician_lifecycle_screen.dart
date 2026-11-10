import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/lab/data/lab_technician_lifecycle_remote_data_source.dart';

// S05A: standalone technician action, not router-wired.
// A new adapter is required for a different clinical action.
class LabTechnicianLifecycleScreen extends StatefulWidget {
  const LabTechnicianLifecycleScreen({
    super.key,
    required this.patientId,
    required this.orderId,
    required this.action,
    required this.dataSource,
  }) : assert(patientId > 0),
       assert(orderId > 0);

  final int patientId;
  final int orderId;
  final LabTechnicianAction action;
  final LabTechnicianLifecycleRemoteDataSource dataSource;

  @override
  State<LabTechnicianLifecycleScreen> createState() =>
      _LabTechnicianLifecycleScreenState();
}

class _LabTechnicianLifecycleScreenState
    extends State<LabTechnicianLifecycleScreen> {
  final _qr = TextEditingController();
  final _equipment = TextEditingController();
  LabTechnicianReview? _review;
  bool _confirmed = false;
  bool _busy = false;
  int _generation = 0;
  String? _error;
  String? _result;

  @override
  void initState() {
    super.initState();
    widget.dataSource.reader.sessionNavigationGuard.addListener(
      _sessionChanged,
    );
  }

  @override
  void didUpdateWidget(covariant LabTechnicianLifecycleScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.patientId != widget.patientId ||
        oldWidget.orderId != widget.orderId ||
        oldWidget.action != widget.action ||
        !identical(oldWidget.dataSource, widget.dataSource)) {
      oldWidget.dataSource.reader.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      widget.dataSource.reader.sessionNavigationGuard.addListener(
        _sessionChanged,
      );
      _clear('Order or clinical session changed. Start a new review.');
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.reader.sessionNavigationGuard.removeListener(
      _sessionChanged,
    );
    _qr.dispose();
    _equipment.dispose();
    super.dispose();
  }

  void _sessionChanged() {
    _clear('Session changed. Technician review cleared.');
  }

  void _clear(String reason) {
    _generation++;
    _qr.clear();
    _equipment.clear();
    if (!mounted) {
      return;
    }
    setState(() {
      _review = null;
      _confirmed = false;
      _busy = false;
      _result = null;
      _error = reason;
    });
  }

  bool _current(
    int generation,
    int revision,
    LabTechnicianLifecycleRemoteDataSource source,
    int patient,
    int order,
    LabTechnicianAction action,
  ) =>
      mounted &&
      _generation == generation &&
      identical(source, widget.dataSource) &&
      patient == widget.patientId &&
      order == widget.orderId &&
      action == widget.action &&
      source.reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      source.reader.sessionNavigationGuard.revision == revision;

  String _message(Object error) {
    if (error is LabTechnicianOutcomeUnconfirmed ||
        error is LabTechnicianAlreadyAttempted) {
      return 'Outcome unconfirmed or action already attempted. Reconcile with the server. Do not retry this clinical POST.';
    }
    if (error is LabTechnicianScopeUnavailable) {
      return 'Technician session is unavailable. No clinical write authorized.';
    }
    if (error is LabTechnicianEvidenceChanged ||
        error is FormatException ||
        error is ArgumentError) {
      return 'Order QR, state or evidence changed. No write was authorized.';
    }
    if (error is ApiException) {
      return 'Laboratory request failed. Check the server before any further action.';
    }
    return 'Laboratory action could not be verified. Do not retry automatically.';
  }

  Future<void> _startReview() async {
    if (_busy || _review != null || widget.dataSource.writeAttempted) {
      return;
    }
    final source = widget.dataSource;
    final patient = widget.patientId;
    final order = widget.orderId;
    final action = widget.action;
    final revision = source.reader.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _error = null;
      _result = null;
    });
    try {
      final review = await source.review(
        patientId: patient,
        orderId: order,
        action: action,
        scannedQrCode: _qr.text,
        equipmentReference: _equipment.text,
      );
      if (!_current(generation, revision, source, patient, order, action)) {
        return;
      }
      setState(() {
        _review = review;
        _confirmed = false;
        _busy = false;
      });
    } catch (error) {
      if (!_current(generation, revision, source, patient, order, action)) {
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
        !_confirmed ||
        _review == null ||
        widget.dataSource.writeAttempted) {
      return;
    }
    final source = widget.dataSource;
    final review = _review!;
    final patient = widget.patientId;
    final order = widget.orderId;
    final action = widget.action;
    final revision = source.reader.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _error = null;
      _result = null;
    });
    try {
      final evidence = await source.submitOnce(reviewed: review);
      if (!_current(generation, revision, source, patient, order, action)) {
        return;
      }
      setState(() {
        _review = null;
        _confirmed = false;
        _busy = false;
        _result =
            'Server-confirmed order #${evidence.order.id}: ${evidence.order.status.name}.';
      });
    } catch (error) {
      if (!_current(generation, revision, source, patient, order, action)) {
        return;
      }
      setState(() {
        _review = null;
        _confirmed = false;
        _busy = false;
        _error = _message(error);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final actionName = switch (widget.action) {
      LabTechnicianAction.collect => 'Collect sample',
      LabTechnicianAction.linkEquipment => 'Link equipment',
      LabTechnicianAction.process => 'Process sample',
    };
    final locked = _busy || _review != null || widget.dataSource.writeAttempted;
    return Scaffold(
      appBar: AppBar(title: Text('Lab technician: $actionName')),
      body: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Patient ${widget.patientId} • Order ${widget.orderId}'),
              const Text(
                'Online-only, one clinical POST. No automatic retries.',
              ),
              if (widget.action == LabTechnicianAction.collect)
                TextField(
                  key: const ValueKey('lab-tech-qr'),
                  controller: _qr,
                  enabled: !locked,
                  decoration: const InputDecoration(
                    labelText: 'Order QR (from scanner)',
                  ),
                ),
              if (widget.action != LabTechnicianAction.collect)
                TextField(
                  key: const ValueKey('lab-tech-equipment'),
                  controller: _equipment,
                  enabled: !locked,
                  decoration: InputDecoration(
                    labelText:
                        widget.action == LabTechnicianAction.linkEquipment
                        ? 'Equipment reference (required)'
                        : 'Equipment reference (optional)',
                  ),
                ),
              const SizedBox(height: 16),
              OutlinedButton(
                key: const ValueKey('lab-tech-review'),
                onPressed: locked ? null : () => unawaited(_startReview()),
                child: Text('Review $actionName'),
              ),
              if (_busy) const CircularProgressIndicator(),
              if (_review != null) ...[
                Text(
                  'Verified server order #${_review!.evidence.order.id} '
                  '(${_review!.evidence.order.status.name})',
                  key: const ValueKey('lab-tech-review-evidence'),
                ),
                CheckboxListTile(
                  key: const ValueKey('lab-tech-ack'),
                  value: _confirmed,
                  onChanged: _busy
                      ? null
                      : (value) => setState(() {
                          _confirmed = value == true;
                        }),
                  title: const Text(
                    'I verified the patient, order and specimen evidence',
                  ),
                ),
                ElevatedButton(
                  key: const ValueKey('lab-tech-submit'),
                  onPressed:
                      _busy || !_confirmed || widget.dataSource.writeAttempted
                      ? null
                      : () => unawaited(_submit()),
                  child: Text('Confirm $actionName'),
                ),
              ],
              if (_result != null) ...[
                Text(_result!, key: const ValueKey('lab-tech-confirmed')),
              ],
              if (_error != null) ...[
                Text(_error!, key: const ValueKey('lab-tech-error')),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
