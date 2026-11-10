import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/models/lab_order_read.dart';

// P23H.S04B: standalone doctor/nurse clinical confirmation, not router-wired.
class LabOrderMutationScreen extends StatefulWidget {
  const LabOrderMutationScreen({
    super.key,
    required this.patientId,
    required this.dataSource,
  }) : assert(patientId > 0);

  final int patientId;
  final LabOrderMutationRemoteDataSource dataSource;

  @override
  State<LabOrderMutationScreen> createState() => _LabOrderMutationScreenState();
}

class _LabOrderMutationScreenState extends State<LabOrderMutationScreen> {
  final _tests = TextEditingController();
  final _consultation = TextEditingController();
  final _cancelOrder = TextEditingController();
  final _cancelReason = TextEditingController();
  Object? _review;
  LabPatientOrder? _confirmed;
  bool _acknowledged = false;
  bool _busy = false;
  String? _error;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.reader.sessionNavigationGuard.addListener(
      _sessionChanged,
    );
  }

  @override
  void didUpdateWidget(covariant LabOrderMutationScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.patientId != widget.patientId ||
        !identical(oldWidget.dataSource, widget.dataSource)) {
      oldWidget.dataSource.reader.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      widget.dataSource.reader.sessionNavigationGuard.addListener(
        _sessionChanged,
      );
      _invalidate('Patient or clinical session changed. Review again.');
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.reader.sessionNavigationGuard.removeListener(
      _sessionChanged,
    );
    _tests.dispose();
    _consultation.dispose();
    _cancelOrder.dispose();
    _cancelReason.dispose();
    super.dispose();
  }

  void _invalidate(String message) {
    _generation++;
    if (!mounted) {
      return;
    }
    setState(() {
      _review = null;
      _confirmed = null;
      _acknowledged = false;
      _busy = false;
      _error = message;
    });
  }

  void _sessionChanged() {
    _invalidate('Session changed. Laboratory review cleared.');
  }

  bool _current(
    int generation,
    int revision,
    int patientId,
    LabOrderMutationRemoteDataSource source,
  ) =>
      mounted &&
      _generation == generation &&
      widget.patientId == patientId &&
      identical(widget.dataSource, source) &&
      source.reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      source.reader.sessionNavigationGuard.revision == revision;

  String _message(Object error) {
    if (error is LabMutationAlreadyAttempted ||
        error is LabMutationOutcomeUnconfirmed) {
      return 'Clinical write was attempted or cannot be confirmed. Reconcile with the server before any new action. Do not retry.';
    }
    if (error is LabMutationScopeUnavailable ||
        error is LabReadSessionUnavailable) {
      return 'Session unavailable. No laboratory action is authorized.';
    }
    if (error is LabMutationEvidenceChanged ||
        error is FormatException ||
        error is ArgumentError) {
      return 'Review invalid or server evidence changed. Nothing was submitted.';
    }
    if (error is ApiException &&
        const <int>{400, 401, 403, 404, 409, 422}.contains(error.statusCode)) {
      return 'Laboratory server rejected the review. No automatic retry.';
    }
    return 'Laboratory evidence could not be confirmed. No automatic retry.';
  }

  List<int> _selectedIds(String raw) {
    final pieces = raw.split(',');
    if (pieces.isEmpty ||
        pieces.length > 30 ||
        pieces.any((part) => part.trim().isEmpty)) {
      throw const FormatException('Invalid test IDs.');
    }
    final ids = <int>[];
    for (final part in pieces) {
      final trimmed = part.trim();
      if (!RegExp(r'^\d{1,10}$').hasMatch(trimmed)) {
        throw const FormatException('Invalid test ID.');
      }
      final value = int.tryParse(trimmed);
      if (value == null || value <= 0 || value > 2147483647) {
        throw const FormatException('Invalid test ID.');
      }
      ids.add(value);
    }
    if (ids.toSet().length != ids.length) {
      throw const FormatException('Duplicate test ID.');
    }
    return ids;
  }

  int? _optionalId(String raw) {
    if (raw.trim().isEmpty) {
      return null;
    }
    final value = int.tryParse(raw.trim());
    if (value == null || value <= 0 || value > 2147483647) {
      throw const FormatException('Invalid consultation ID.');
    }
    return value;
  }

  int _requiredId(String raw) {
    final value = _optionalId(raw);
    if (value == null) {
      throw const FormatException('Order ID is required.');
    }
    return value;
  }

  Future<void> _reviewCreate() async {
    if (_busy || _review != null || widget.dataSource.writeAttempted) {
      return;
    }
    final source = widget.dataSource;
    final revision = source.reader.sessionNavigationGuard.revision;
    final patientId = widget.patientId;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _confirmed = null;
      _error = null;
    });
    try {
      final testIds = _selectedIds(_tests.text);
      final consultationId = _optionalId(_consultation.text);
      final review = await source.reviewCreate(
        patientId: patientId,
        testIds: testIds,
        consultationId: consultationId,
      );
      if (!_current(generation, revision, patientId, source)) {
        return;
      }
      setState(() {
        _busy = false;
        _review = review;
        _acknowledged = false;
      });
    } catch (error) {
      if (!_current(generation, revision, patientId, source)) {
        return;
      }
      setState(() {
        _busy = false;
        _error = _message(error);
      });
    }
  }

  Future<void> _reviewCancel() async {
    if (_busy || _review != null || widget.dataSource.writeAttempted) {
      return;
    }
    final source = widget.dataSource;
    final revision = source.reader.sessionNavigationGuard.revision;
    final patientId = widget.patientId;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _confirmed = null;
      _error = null;
    });
    try {
      final orderId = _requiredId(_cancelOrder.text);
      final review = await source.reviewCancel(
        patientId: patientId,
        orderId: orderId,
      );
      if (!_current(generation, revision, patientId, source)) {
        return;
      }
      setState(() {
        _busy = false;
        _review = review;
        _acknowledged = false;
      });
    } catch (error) {
      if (!_current(generation, revision, patientId, source)) {
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
    final review = _review!;
    final revision = source.reader.sessionNavigationGuard.revision;
    final patientId = widget.patientId;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _error = null;
      _confirmed = null;
    });
    try {
      final LabPatientOrder result;
      if (review is LabOrderCreateReview) {
        result = await source.createOnce(reviewed: review);
      } else if (review is LabOrderCancelReview) {
        result = await source.cancelOnce(
          reviewed: review,
          reason: _cancelReason.text.trim(),
        );
      } else {
        throw const FormatException('Unknown laboratory action.');
      }
      if (!_current(generation, revision, patientId, source)) {
        return;
      }
      setState(() {
        _busy = false;
        _review = null;
        _acknowledged = false;
        _confirmed = result;
      });
    } catch (error) {
      if (!_current(generation, revision, patientId, source)) {
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
    final locked = _busy || _review != null || widget.dataSource.writeAttempted;
    return Scaffold(
      appBar: AppBar(title: const Text('Laboratory orders — clinical actions')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('Patient ID: ${widget.patientId}'),
          const Text(
            'Doctor or nurse only. Online confirmation required. No automatic retry.',
          ),
          const SizedBox(height: 16),
          TextField(
            key: const ValueKey('lab-mutation-test-ids'),
            controller: _tests,
            enabled: !locked,
            decoration: const InputDecoration(
              labelText: 'Test IDs (comma-separated)',
            ),
          ),
          TextField(
            key: const ValueKey('lab-mutation-consultation'),
            controller: _consultation,
            enabled: !locked,
            decoration: const InputDecoration(
              labelText: 'Consultation ID (optional)',
            ),
          ),
          ElevatedButton(
            key: const ValueKey('lab-mutation-review-create'),
            onPressed: locked ? null : () => unawaited(_reviewCreate()),
            child: const Text('Review new lab order'),
          ),
          const Divider(),
          TextField(
            key: const ValueKey('lab-mutation-cancel-id'),
            controller: _cancelOrder,
            enabled: !locked,
            decoration: const InputDecoration(
              labelText: 'Existing lab order ID',
            ),
          ),
          TextField(
            key: const ValueKey('lab-mutation-cancel-reason'),
            controller: _cancelReason,
            enabled: !locked,
            decoration: const InputDecoration(
              labelText: 'Cancellation reason (required)',
            ),
            maxLength: 255,
          ),
          ElevatedButton(
            key: const ValueKey('lab-mutation-review-cancel'),
            onPressed: locked ? null : () => unawaited(_reviewCancel()),
            child: const Text('Review lab cancellation'),
          ),
          if (_review is LabOrderCreateReview) ...[
            const Divider(),
            const Text('Creation review from server:'),
            Text(
              'Tests: ${(_review! as LabOrderCreateReview).testNames.join(', ')}',
            ),
            Text(
              'Consultation: ${(_review! as LabOrderCreateReview).consultationId ?? 'none'}',
            ),
          ],
          if (_review is LabOrderCancelReview) ...[
            const Divider(),
            Text(
              'Cancel order #${(_review! as LabOrderCancelReview).order.id}',
            ),
            Text(
              'Current status: ${(_review! as LabOrderCancelReview).order.status.name}',
            ),
          ],
          if (_review != null) ...[
            CheckboxListTile(
              key: const ValueKey('lab-mutation-confirm-checkbox'),
              title: const Text('I confirm this clinical laboratory action'),
              value: _acknowledged,
              onChanged: _busy
                  ? null
                  : (value) {
                      setState(() {
                        _acknowledged = value == true;
                      });
                    },
            ),
            ElevatedButton(
              key: const ValueKey('lab-mutation-submit'),
              onPressed: !_busy && _acknowledged
                  ? () => unawaited(_submit())
                  : null,
              child: const Text('Submit once and verify from server'),
            ),
          ],
          if (_busy) const CircularProgressIndicator(),
          if (_error != null)
            Text(_error!, key: const ValueKey('lab-mutation-error')),
          if (_confirmed != null) ...[
            Text(
              'Server-confirmed order #${_confirmed!.id}: ${_confirmed!.status.name}',
              key: const ValueKey('lab-mutation-confirmed'),
            ),
            const Text(
              'Clinical action verified by a fresh server read. No further writes from this screen.',
            ),
          ],
        ],
      ),
    );
  }
}
