import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/diet_nursing_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/diet_nursing_order.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

/// Admission-verified, online-only diet and nursing orders.
/// The screen never retries a mutation; all writes lock until route re-entry.
class DietNursingOrdersScreen extends StatefulWidget {
  const DietNursingOrdersScreen({
    super.key,
    required this.unifiedSource,
    required this.admissionDataSource,
    required this.patientId,
    required this.kind,
  }) : assert(kind == 'diets' || kind == 'nursing-orders');

  final ClinicalOrdersRemoteDataSource unifiedSource;
  final PatientAdmissionRemoteDataSource admissionDataSource;
  final int patientId;
  final String kind;

  @override
  State<DietNursingOrdersScreen> createState() =>
      _DietNursingOrdersScreenState();
}

class _DietNursingOrdersScreenState extends State<DietNursingOrdersScreen> {
  late DietNursingRemoteDataSource _source;
  final _description = TextEditingController();
  final _instructions = TextEditingController();
  final _customFrequency = TextEditingController();
  final _assignedStaff = TextEditingController();
  final _actionNote = TextEditingController();

  PatientAdmission? _admission;
  List<DietNursingOrder> _items = const <DietNursingOrder>[];
  List<NursingOrderExecution>? _history;
  bool _loading = true;
  bool _doctor = false;
  bool _nurse = false;
  bool _writeLocked = false;
  String _category = 'regular';
  String _frequency = 'once';
  String _feedingRoute = 'oral';
  String? _message;
  int _generation = 0;

  bool get _diet => widget.kind == 'diets';
  bool get _canWrite =>
      !_loading &&
      !_writeLocked &&
      _admission != null &&
      (_doctor || (!_diet && _nurse)) &&
      widget.unifiedSource.sessionNavigationGuard.canAccessProtectedRoutes;

  @override
  void initState() {
    super.initState();
    _source = DietNursingRemoteDataSource(unifiedSource: widget.unifiedSource);
    _category = _diet ? 'regular' : 'monitoring';
    widget.unifiedSource.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant DietNursingOrdersScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.patientId != widget.patientId ||
        oldWidget.kind != widget.kind ||
        !identical(oldWidget.unifiedSource, widget.unifiedSource) ||
        !identical(oldWidget.admissionDataSource, widget.admissionDataSource)) {
      oldWidget.unifiedSource.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      widget.unifiedSource.sessionNavigationGuard.addListener(_sessionChanged);
      _source = DietNursingRemoteDataSource(
        unifiedSource: widget.unifiedSource,
      );
      _clear();
      _category = _diet ? 'regular' : 'monitoring';
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.unifiedSource.sessionNavigationGuard.removeListener(_sessionChanged);
    for (final c in <TextEditingController>[
      _description,
      _instructions,
      _customFrequency,
      _assignedStaff,
      _actionNote,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  void _clear() {
    _generation++;
    _description.clear();
    _instructions.clear();
    _customFrequency.clear();
    _assignedStaff.clear();
    _actionNote.clear();
    _admission = null;
    _items = const <DietNursingOrder>[];
    _history = null;
    _doctor = false;
    _nurse = false;
    _writeLocked = false;
    _message = null;
    _frequency = 'once';
    _feedingRoute = 'oral';
  }

  void _sessionChanged() {
    _clear();
    if (!mounted) {
      return;
    }
    setState(() => _loading = false);
    if (widget.unifiedSource.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  Future<void> _load() async {
    final guard = widget.unifiedSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_generation;
    if (!mounted) {
      return;
    }
    setState(() {
      _loading = true;
      _admission = null;
      _items = const <DietNursingOrder>[];
      _history = null;
      _doctor = false;
      _nurse = false;
      _message = null;
    });
    try {
      final before = await _source.authorizedSession();
      final admission = await widget.admissionDataSource.fetchCurrentAdmission(
        patientId: widget.patientId,
      );
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      final orders = admission == null
          ? const <DietNursingOrder>[]
          : (await _source.list(
              kind: widget.kind,
              patientId: widget.patientId,
              admissionId: admission.id,
            )).items;
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      final after = await _source.authorizedSession();
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes ||
          before.userId != after.userId ||
          before.clinicContextId != after.clinicContextId ||
          before.role != after.role ||
          before.accessToken != after.accessToken ||
          before.refreshToken != after.refreshToken) {
        return;
      }
      setState(() {
        _admission = admission;
        _items = orders;
        _doctor = before.role.trim().toLowerCase() == 'doctor';
        _nurse = before.role.trim().toLowerCase() == 'nurse';
        _loading = false;
      });
    } on Object {
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _loading = false;
        _admission = null;
        _items = const <DietNursingOrder>[];
        _doctor = false;
        _nurse = false;
        _message = 'Unable to verify active admission and clinical orders.';
      });
    }
  }

  Future<void> _write(Future<DietNursingOrder> Function() operation) async {
    if (!_canWrite) {
      return;
    }
    final guard = widget.unifiedSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;
    setState(() {
      _writeLocked = true;
      _message = 'Submitting once. No automatic retry will be sent.';
    });
    try {
      final record = await operation();
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(
        () => _message =
            'Server confirmed order #${record.id}: ${record.status}. '
            'Leave and reload before another write.',
      );
    } on Object {
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(
        () => _message =
            'Outcome not confirmed. No retry was sent. '
            'Leave and verify the server record before another write.',
      );
    }
  }

  void _create() {
    if (!_canWrite || !_doctor) {
      return;
    }
    final admission = _admission;
    if (admission == null) {
      return;
    }
    if (_diet && _items.any((order) => order.isOpen)) {
      setState(
        () => _message =
            'An active diet order exists. Complete or cancel it first.',
      );
      return;
    }
    if ((_diet && _category == 'custom' && _description.text.trim().isEmpty) ||
        (!_diet && _description.text.trim().isEmpty) ||
        (!_diet &&
            _frequency == 'custom' &&
            _customFrequency.text.trim().isEmpty)) {
      setState(() => _message = 'Complete the required order fields.');
      return;
    }
    final rawStaff = _assignedStaff.text.trim();
    final staffId = rawStaff.isEmpty ? null : int.tryParse(rawStaff);
    if (!_diet &&
        rawStaff.isNotEmpty &&
        (!RegExp(r'^[1-9][0-9]{0,9}$').hasMatch(rawStaff) ||
            staffId == null ||
            staffId > 2147483647)) {
      setState(() => _message = 'Enter a valid assigned nurse staff ID.');
      return;
    }
    try {
      if (_diet) {
        final diet = _category;
        final desc = _description.text.trim();
        final instructions = _instructions.text.trim();
        unawaited(
          _write(
            () => _source.createDiet(
              patientId: widget.patientId,
              admissionId: admission.id,
              dietType: diet,
              customDescription: diet == 'custom' ? desc : null,
              feedingRoute: diet == 'npo' ? null : _feedingRoute,
              instructions: instructions.isEmpty ? null : instructions,
            ),
          ),
        );
      } else {
        final category = _category;
        final freq = _frequency;
        final desc = _description.text.trim();
        final custom = _customFrequency.text.trim();
        final instructions = _instructions.text.trim();
        unawaited(
          _write(
            () => _source.createNursing(
              patientId: widget.patientId,
              admissionId: admission.id,
              category: category,
              orderText: desc,
              frequency: freq,
              customFrequency: freq == 'custom' ? custom : null,
              instructions: instructions.isEmpty ? null : instructions,
              assignedToStaffId: staffId,
            ),
          ),
        );
      }
    } on ArgumentError {
      setState(() => _message = 'Review the order fields before submission.');
    }
  }

  void _act(DietNursingOrder order, String action) {
    final admission = _admission;
    if (!_canWrite ||
        admission == null ||
        !order.isOpen ||
        order.admissionId != admission.id ||
        (action == 'execute' && (!_nurse || !order.canExecute)) ||
        (action != 'execute' && !_doctor) ||
        (action == 'complete' && !order.canDoctorComplete)) {
      return;
    }
    final note = _actionNote.text.trim();
    if (note.isEmpty) {
      setState(() => _message = 'Enter the execution note or action reason.');
      return;
    }
    if (action == 'execute') {
      unawaited(
        _write(
          () => _source.executeNursing(
            patientId: widget.patientId,
            admissionId: admission.id,
            order: order,
            resultNote: note,
          ),
        ),
      );
    } else {
      unawaited(
        _write(
          () => _source.act(
            kind: widget.kind,
            patientId: widget.patientId,
            admissionId: admission.id,
            order: order,
            action: action,
            note: note,
          ),
        ),
      );
    }
  }

  Future<void> _loadHistory(DietNursingOrder order) async {
    final admission = _admission;
    if (_diet ||
        admission == null ||
        _loading ||
        !widget.unifiedSource.sessionNavigationGuard.canAccessProtectedRoutes ||
        order.admissionId != admission.id) {
      return;
    }
    final guard = widget.unifiedSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;
    try {
      final results = await _source.executions(
        patientId: widget.patientId,
        admissionId: admission.id,
        orderId: order.id,
      );
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _history = results.items;
        _message = 'Execution history for nursing order #${order.id}';
      });
    } on Object {
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _history = null;
        _message = 'Nursing execution history unavailable.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final categories =
        (_diet
              ? DietNursingOrder.dietTypes.toList()
              : DietNursingOrder.nursingCategories.toList())
          ..sort();
    final frequencyOptions = DietNursingOrder.frequencies.toList()..sort();
    final feedingOptions = DietNursingOrder.feedingRoutes.toList()..sort();
    return Scaffold(
      appBar: AppBar(title: Text(_diet ? 'Diet orders' : 'Nursing orders')),
      body: _loading
          ? const Center(
              child: CircularProgressIndicator(key: ValueKey('p23d-c-loading')),
            )
          : _admission == null
          ? Center(
              child: Text(
                _message ?? 'No active admission for this patient.',
                key: const ValueKey('p23d-c-unavailable'),
              ),
            )
          : ListView(
              key: const ValueKey('p23d-c-ready'),
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Patient #${widget.patientId} · Verified admission #${_admission!.id}',
                ),
                const Text(
                  'Online only. Never queued or automatically retried.',
                ),
                if (_doctor) ...[
                  DropdownButton<String>(
                    key: const ValueKey('p23d-c-category'),
                    value: _category,
                    items: [
                      for (final item in categories)
                        DropdownMenuItem(value: item, child: Text(item)),
                    ],
                    onChanged: _canWrite
                        ? (value) {
                            if (value != null) {
                              setState(() {
                                _category = value;
                                if (_diet && value == 'enteral') {
                                  _feedingRoute = 'enteral';
                                }
                              });
                            }
                          }
                        : null,
                  ),
                  if (_diet && _category != 'npo')
                    DropdownButton<String>(
                      key: const ValueKey('p23d-c-feeding-route'),
                      value: _feedingRoute,
                      items: [
                        for (final route in feedingOptions)
                          DropdownMenuItem(value: route, child: Text(route)),
                      ],
                      onChanged: _canWrite
                          ? (value) {
                              if (value != null) {
                                setState(() => _feedingRoute = value);
                              }
                            }
                          : null,
                    ),
                  if (!_diet || _category == 'custom')
                    TextField(
                      key: const ValueKey('p23d-c-description'),
                      controller: _description,
                      enabled: _canWrite,
                      maxLines: 2,
                      decoration: InputDecoration(
                        labelText: _diet
                            ? 'Custom diet description'
                            : 'Nursing order text',
                      ),
                    ),
                  if (!_diet) ...[
                    DropdownButton<String>(
                      key: const ValueKey('p23d-c-frequency'),
                      value: _frequency,
                      items: [
                        for (final item in frequencyOptions)
                          DropdownMenuItem(value: item, child: Text(item)),
                      ],
                      onChanged: _canWrite
                          ? (value) {
                              if (value != null) {
                                setState(() => _frequency = value);
                              }
                            }
                          : null,
                    ),
                    if (_frequency == 'custom')
                      TextField(
                        key: const ValueKey('p23d-c-custom-frequency'),
                        controller: _customFrequency,
                        enabled: _canWrite,
                        decoration: const InputDecoration(
                          labelText: 'Custom frequency',
                        ),
                      ),
                    TextField(
                      key: const ValueKey('p23d-c-assigned-staff'),
                      controller: _assignedStaff,
                      enabled: _canWrite,
                      keyboardType: TextInputType.number,
                      decoration: const InputDecoration(
                        labelText: 'Assigned nurse staff ID (optional)',
                      ),
                    ),
                  ],
                  TextField(
                    key: const ValueKey('p23d-c-instructions'),
                    controller: _instructions,
                    enabled: _canWrite,
                    maxLines: 2,
                    decoration: const InputDecoration(
                      labelText: 'Instructions (optional)',
                    ),
                  ),
                  FilledButton(
                    key: const ValueKey('p23d-c-create'),
                    onPressed:
                        _canWrite &&
                            (!_diet || !_items.any((order) => order.isOpen))
                        ? _create
                        : null,
                    child: const Text('Create once'),
                  ),
                ] else if (!_nurse)
                  const Text(
                    'Read-only: only doctors can create or change orders.',
                  ),
                if (_doctor || (!_diet && _nurse))
                  TextField(
                    key: const ValueKey('p23d-c-action-note'),
                    controller: _actionNote,
                    enabled: _canWrite,
                    maxLines: 3,
                    decoration: InputDecoration(
                      labelText: _nurse
                          ? 'Nursing execution result note'
                          : 'Completion note or cancellation reason',
                    ),
                  ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('p23d-c-message')),
                OutlinedButton(
                  key: const ValueKey('p23d-c-refresh'),
                  onPressed: () => unawaited(_load()),
                  child: const Text('Refresh from server'),
                ),
                Text('Orders: ${_items.length}'),
                if (_items.isEmpty)
                  const Text('No orders for this active admission.'),
                for (final order in _items)
                  Card(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        ListTile(
                          key: ValueKey('p23d-c-order-${order.id}'),
                          title: Text(
                            _diet
                                ? 'Diet: ${order.category}'
                                : 'Nursing: ${order.description}',
                          ),
                          subtitle: Text(
                            'Order #${order.id} · ${order.status}',
                          ),
                        ),
                        if (_diet && order.category == 'npo')
                          const Text('Nothing by mouth (NPO)'),
                        if (!_diet) Text('Frequency: ${order.frequency}'),
                        if (!_diet && order.assignedToStaffId != null)
                          Text(
                            'Assigned nurse staff #${order.assignedToStaffId}',
                          ),
                        if (_doctor && order.isOpen) ...[
                          OutlinedButton(
                            key: ValueKey('p23d-c-cancel-${order.id}'),
                            onPressed: _canWrite
                                ? () => _act(order, 'cancel')
                                : null,
                            child: const Text('Cancel once'),
                          ),
                          if (order.canDoctorComplete)
                            OutlinedButton(
                              key: ValueKey('p23d-c-complete-${order.id}'),
                              onPressed: _canWrite
                                  ? () => _act(order, 'complete')
                                  : null,
                              child: const Text('Complete once'),
                            ),
                        ],
                        if (!_diet && _nurse && order.canExecute)
                          FilledButton(
                            key: ValueKey('p23d-c-execute-${order.id}'),
                            onPressed: _canWrite
                                ? () => _act(order, 'execute')
                                : null,
                            child: const Text('Record nursing execution once'),
                          ),
                        if (!_diet)
                          TextButton(
                            key: ValueKey('p23d-c-history-${order.id}'),
                            onPressed: () => unawaited(_loadHistory(order)),
                            child: const Text('Review execution history'),
                          ),
                      ],
                    ),
                  ),
                if (_history != null) ...[
                  Text('Executions: ${_history!.length}'),
                  for (final execution in _history!)
                    ListTile(
                      title: Text('Execution #${execution.id}'),
                      subtitle: Text(execution.resultNote),
                    ),
                ],
              ],
            ),
    );
  }
}
