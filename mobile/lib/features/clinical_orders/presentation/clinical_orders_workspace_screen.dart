import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/clinical_order_summary.dart';

/// Authenticated patient-scoped unified clinical orders. GET only.
class ClinicalOrdersWorkspaceScreen extends StatefulWidget {
  const ClinicalOrdersWorkspaceScreen({
    super.key,
    required this.dataSource,
    required this.onPatientSelected,
    this.patientId,
    this.onConsultationSelected,
    this.onImagingRequested,
    this.onProcedureRequested,
    this.onDietRequested,
    this.onNursingRequested,
    this.onBloodRequested,
  });

  final ClinicalOrdersRemoteDataSource dataSource;
  final int? patientId;
  final ValueChanged<int> onPatientSelected;
  final void Function(int patientId, int consultationId)?
  onConsultationSelected;
  final ValueChanged<int>? onImagingRequested;
  final ValueChanged<int>? onProcedureRequested;
  final ValueChanged<int>? onDietRequested;
  final ValueChanged<int>? onNursingRequested;
  final ValueChanged<int>? onBloodRequested;

  @override
  State<ClinicalOrdersWorkspaceScreen> createState() =>
      _ClinicalOrdersWorkspaceScreenState();
}

class _ClinicalOrdersWorkspaceScreenState
    extends State<ClinicalOrdersWorkspaceScreen> {
  final _patientInput = TextEditingController();
  ClinicalOrderTimeline? _timeline;
  String? _type;
  String? _status;
  String? _message;
  bool _loading = true;
  bool _authorized = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant ClinicalOrdersWorkspaceScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.patientId != widget.patientId ||
        !identical(oldWidget.dataSource, widget.dataSource)) {
      oldWidget.dataSource.sessionNavigationGuard.removeListener(
        _onSessionChanged,
      );
      widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
      _patientInput.clear();
      _type = null;
      _status = null;
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onSessionChanged);
    _patientInput.dispose();
    super.dispose();
  }

  void _onSessionChanged() {
    _generation++;
    _patientInput.clear();
    _timeline = null;
    _authorized = false;
    _message = null;
    _type = null;
    _status = null;
    _loading = false;
    if (!mounted) {
      return;
    }
    setState(() {});
    if (widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  Future<void> _load() async {
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_generation;
    if (!mounted) {
      return;
    }
    setState(() {
      _loading = true;
      _timeline = null;
      _authorized = false;
      _message = null;
    });
    if (!guard.canAccessProtectedRoutes) {
      if (mounted && _generation == generation) {
        setState(() => _loading = false);
      }
      return;
    }
    try {
      await widget.dataSource.authorizedSession();
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      if (widget.patientId == null) {
        setState(() {
          _loading = false;
          _authorized = true;
        });
        return;
      }
      final result = await widget.dataSource.fetchPatientOrders(
        patientId: widget.patientId!,
        type: _type,
        status: _status,
      );
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _timeline = result;
        _authorized = true;
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
        _authorized = false;
        _timeline = null;
        _message =
            'Clinical orders unavailable. Verify the patient and session.';
      });
    }
  }

  void _openPatient() {
    if (_loading ||
        !_authorized ||
        !widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes) {
      return;
    }
    final raw = _patientInput.text;
    final patient = int.tryParse(raw);
    if (!RegExp(r'^[1-9][0-9]{0,9}$').hasMatch(raw) ||
        patient == null ||
        patient > 2147483647) {
      setState(() => _message = 'Enter a valid positive patient ID.');
      return;
    }
    widget.onPatientSelected(patient);
  }

  void _filter({String? type, String? status}) {
    if (_loading ||
        !_authorized ||
        !widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes) {
      return;
    }
    setState(() {
      _type = type;
      _status = status;
    });
    unawaited(_load());
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Clinical orders')),
      body: _loading
          ? const Center(
              child: CircularProgressIndicator(
                key: ValueKey('p23d-orders-loading'),
              ),
            )
          : !_authorized
          ? Center(
              child: Text(
                _message ?? 'Clinical orders access unavailable.',
                key: const ValueKey('p23d-orders-unavailable'),
              ),
            )
          : widget.patientId == null
          ? ListView(
              key: const ValueKey('p23d-orders-entry'),
              padding: const EdgeInsets.all(16),
              children: [
                const Text(
                  'Select a patient to view server-owned clinical orders.',
                ),
                const Text(
                  'No clinic-wide list or offline clinical-order cache is used.',
                ),
                TextField(
                  key: const ValueKey('p23d-orders-patient-id'),
                  controller: _patientInput,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Patient ID'),
                ),
                FilledButton(
                  key: const ValueKey('p23d-orders-open-patient'),
                  onPressed: _openPatient,
                  child: const Text('View patient orders'),
                ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('p23d-orders-message')),
              ],
            )
          : ListView(
              key: const ValueKey('p23d-orders-loaded'),
              padding: const EdgeInsets.all(16),
              children: [
                Text('Patient #${widget.patientId} — unified clinical orders'),
                const Text(
                  'Read-only online orders. No clinical action is submitted here.',
                ),
                if (widget.onBloodRequested != null)
                  OutlinedButton(
                    key: const ValueKey('p23d-d-open-blood-products'),
                    onPressed: () =>
                        widget.onBloodRequested?.call(widget.patientId!),
                    child: const Text('Manage blood-product orders'),
                  ),
                if (widget.onDietRequested != null)
                  OutlinedButton(
                    key: const ValueKey('p23d-c-open-diets'),
                    onPressed: () =>
                        widget.onDietRequested?.call(widget.patientId!),
                    child: const Text('Manage diet orders'),
                  ),
                if (widget.onNursingRequested != null)
                  OutlinedButton(
                    key: const ValueKey('p23d-c-open-nursing'),
                    onPressed: () =>
                        widget.onNursingRequested?.call(widget.patientId!),
                    child: const Text('Manage nursing orders'),
                  ),
                if (widget.onImagingRequested != null)
                  OutlinedButton(
                    key: const ValueKey('p23d-b-open-imaging'),
                    onPressed: () =>
                        widget.onImagingRequested?.call(widget.patientId!),
                    child: const Text('Manage imaging orders'),
                  ),
                if (widget.onProcedureRequested != null)
                  OutlinedButton(
                    key: const ValueKey('p23d-b-open-procedures'),
                    onPressed: () =>
                        widget.onProcedureRequested?.call(widget.patientId!),
                    child: const Text('Manage procedure orders'),
                  ),
                DropdownButton<String>(
                  key: const ValueKey('p23d-orders-type'),
                  value: _type ?? 'all',
                  items: const [
                    DropdownMenuItem(
                      value: 'all',
                      child: Text('All order types'),
                    ),
                    DropdownMenuItem(
                      value: 'medication',
                      child: Text('Medication'),
                    ),
                    DropdownMenuItem(
                      value: 'laboratory',
                      child: Text('Laboratory'),
                    ),
                    DropdownMenuItem(value: 'imaging', child: Text('Imaging')),
                    DropdownMenuItem(
                      value: 'procedure',
                      child: Text('Procedures'),
                    ),
                    DropdownMenuItem(
                      value: 'blood_product',
                      child: Text('Blood products'),
                    ),
                    DropdownMenuItem(value: 'diet', child: Text('Diet')),
                    DropdownMenuItem(value: 'nursing', child: Text('Nursing')),
                  ],
                  onChanged: (value) => _filter(
                    type: value == 'all' ? null : value,
                    status: _status,
                  ),
                ),
                DropdownButton<String>(
                  key: const ValueKey('p23d-orders-status'),
                  value: _status ?? 'all',
                  items: const [
                    DropdownMenuItem(value: 'all', child: Text('All statuses')),
                    DropdownMenuItem(value: 'pending', child: Text('Pending')),
                    DropdownMenuItem(value: 'active', child: Text('Active')),
                    DropdownMenuItem(
                      value: 'in_progress',
                      child: Text('In progress'),
                    ),
                    DropdownMenuItem(value: 'on_hold', child: Text('On hold')),
                    DropdownMenuItem(
                      value: 'completed',
                      child: Text('Completed'),
                    ),
                    DropdownMenuItem(
                      value: 'cancelled',
                      child: Text('Cancelled'),
                    ),
                  ],
                  onChanged: (value) => _filter(
                    type: _type,
                    status: value == 'all' ? null : value,
                  ),
                ),
                OutlinedButton(
                  key: const ValueKey('p23d-orders-refresh'),
                  onPressed: () => unawaited(_load()),
                  child: const Text('Refresh from server'),
                ),
                Text('Orders: ${_timeline?.total ?? 0}'),
                if (_timeline?.items.isEmpty ?? true)
                  const Text('No clinical orders found for this patient.'),
                for (final item
                    in _timeline?.items ?? const <ClinicalOrderSummary>[])
                  Card(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        ListTile(
                          key: ValueKey(
                            'p23d-order-${item.orderType}-${item.sourceEntityId}',
                          ),
                          title: Text(item.displayName),
                          subtitle: Text(
                            '${item.orderType} · ${item.status} · ${item.priority}',
                          ),
                        ),
                        if (item.admissionId != null)
                          Text('Admission #${item.admissionId}'),
                        if (item.consultationId != null &&
                            widget.onConsultationSelected != null)
                          TextButton(
                            key: ValueKey(
                              'p23d-linked-consultation-${item.sourceEntityId}',
                            ),
                            onPressed: () {
                              if (widget
                                  .dataSource
                                  .sessionNavigationGuard
                                  .canAccessProtectedRoutes) {
                                widget.onConsultationSelected!(
                                  item.patientId,
                                  item.consultationId!,
                                );
                              }
                            },
                            child: const Text('View linked consultation'),
                          ),
                      ],
                    ),
                  ),
              ],
            ),
    );
  }
}
