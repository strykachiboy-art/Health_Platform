import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/imaging_procedure_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/imaging_procedure_order.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';

/// Admission-verified online imaging or procedure workflow.
/// Any write attempt locks further writes until this screen is left/reopened.
class ImagingProcedureOrdersScreen extends StatefulWidget {
  const ImagingProcedureOrdersScreen({
    super.key,
    required this.unifiedSource,
    required this.admissionDataSource,
    required this.patientId,
    required this.kind,
  });

  final ClinicalOrdersRemoteDataSource unifiedSource;
  final PatientAdmissionRemoteDataSource admissionDataSource;
  final int patientId;
  final String kind;

  @override
  State<ImagingProcedureOrdersScreen> createState() =>
      _ImagingProcedureOrdersScreenState();
}

class _ImagingProcedureOrdersScreenState
    extends State<ImagingProcedureOrdersScreen> {
  late final ImagingProcedureRemoteDataSource _orders;
  final _name = TextEditingController();
  final _bodySite = TextEditingController();
  final _indication = TextEditingController();
  final _actionNote = TextEditingController();
  bool _loading = true;
  bool _writeLocked = false;
  bool _doctor = false;
  bool _consentRequired = false;
  final Set<int> _attestedConsentIds = <int>{};
  String _category = 'xray';
  String? _message;
  int _generation = 0;
  PatientAdmission? _admission;
  List<ImagingProcedureOrder> _items = const <ImagingProcedureOrder>[];

  bool get _isImaging => widget.kind == 'imaging';
  bool get _canWrite =>
      _doctor &&
      !_loading &&
      !_writeLocked &&
      _admission != null &&
      widget.unifiedSource.sessionNavigationGuard.canAccessProtectedRoutes;

  @override
  void initState() {
    super.initState();
    _orders = ImagingProcedureRemoteDataSource(
      unifiedSource: widget.unifiedSource,
    );
    _category = _isImaging ? 'xray' : 'diagnostic';
    widget.unifiedSource.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant ImagingProcedureOrdersScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.patientId != widget.patientId ||
        oldWidget.kind != widget.kind ||
        !identical(oldWidget.unifiedSource, widget.unifiedSource) ||
        !identical(oldWidget.admissionDataSource, widget.admissionDataSource)) {
      oldWidget.unifiedSource.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      widget.unifiedSource.sessionNavigationGuard.addListener(_sessionChanged);
      _orders = ImagingProcedureRemoteDataSource(
        unifiedSource: widget.unifiedSource,
      );
      _clear();
      _category = _isImaging ? 'xray' : 'diagnostic';
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.unifiedSource.sessionNavigationGuard.removeListener(_sessionChanged);
    _name.dispose();
    _bodySite.dispose();
    _indication.dispose();
    _actionNote.dispose();
    super.dispose();
  }

  void _clear() {
    _generation++;
    _name.clear();
    _bodySite.clear();
    _indication.clear();
    _actionNote.clear();
    _admission = null;
    _items = const <ImagingProcedureOrder>[];
    _doctor = false;
    _writeLocked = false;
    _consentRequired = false;
    _attestedConsentIds.clear();
    _message = null;
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
      _items = const <ImagingProcedureOrder>[];
      _admission = null;
      _doctor = false;
      _message = null;
    });
    try {
      final session = await _orders.authorizedSession();
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
          ? const <ImagingProcedureOrder>[]
          : (await _orders.list(
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
      // Re-authenticate after all reads, including the admission lookup.
      final after = await _orders.authorizedSession();
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes ||
          session.userId != after.userId ||
          session.clinicContextId != after.clinicContextId ||
          session.role != after.role ||
          session.accessToken != after.accessToken ||
          session.refreshToken != after.refreshToken) {
        return;
      }
      setState(() {
        _admission = admission;
        _items = orders;
        _doctor = session.role.trim().toLowerCase() == 'doctor';
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
        _doctor = false;
        _admission = null;
        _items = const <ImagingProcedureOrder>[];
        _message = 'Unable to verify active admission and clinical orders.';
      });
    }
  }

  Future<void> _write(
    Future<ImagingProcedureOrder> Function() operation,
  ) async {
    if (!_canWrite) {
      return;
    }
    final guard = widget.unifiedSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;
    setState(() {
      _writeLocked = true;
      _message = 'Submitting once. No automatic retry will be made.';
    });
    try {
      final result = await operation();
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(
        () => _message =
            'Server confirmed order #${result.id}: ${result.status}. '
            'Leave and reload to verify before another write.',
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
    final admission = _admission;
    if (!_canWrite || admission == null) {
      return;
    }
    if (_name.text.trim().isEmpty ||
        _indication.text.trim().isEmpty ||
        (_isImaging && _bodySite.text.trim().isEmpty)) {
      setState(() => _message = 'Complete the required order fields.');
      return;
    }
    unawaited(
      _write(
        () => _orders.create(
          kind: widget.kind,
          patientId: widget.patientId,
          admissionId: admission.id,
          name: _name.text.trim(),
          category: _category,
          indication: _indication.text.trim(),
          bodySite: _isImaging ? _bodySite.text.trim() : null,
          consentRequired: _consentRequired,
        ),
      ),
    );
  }

  void _act(ImagingProcedureOrder order, String action) {
    final admission = _admission;
    if (!_canWrite ||
        admission == null ||
        !order.isOpen ||
        order.admissionId != admission.id) {
      return;
    }
    if (action == 'verify-consent' && !_attestedConsentIds.contains(order.id)) {
      setState(
        () => _message = 'Confirm consent verification before recording it.',
      );
      return;
    }
    if (action != 'verify-consent' && _actionNote.text.trim().isEmpty) {
      setState(() => _message = 'Enter a note or cancellation reason.');
      return;
    }
    unawaited(
      _write(
        () => _orders.act(
          kind: widget.kind,
          patientId: widget.patientId,
          admissionId: admission.id,
          orderId: order.id,
          action: action,
          note: action == 'verify-consent' ? null : _actionNote.text.trim(),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final options =
        (_isImaging
              ? ImagingProcedureOrder.modalities.toList()
              : ImagingProcedureOrder.procedureTypes.toList())
          ..sort();
    return Scaffold(
      appBar: AppBar(
        title: Text(_isImaging ? 'Imaging orders' : 'Procedure orders'),
      ),
      body: _loading
          ? const Center(
              child: CircularProgressIndicator(key: ValueKey('p23d-b-loading')),
            )
          : _admission == null
          ? Center(
              child: Text(
                _message ?? 'No active admission for this patient.',
                key: const ValueKey('p23d-b-unavailable'),
              ),
            )
          : ListView(
              key: const ValueKey('p23d-b-ready'),
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Patient #${widget.patientId} · Verified admission #${_admission!.id}',
                ),
                const Text(
                  'Online only. Writes are never queued or automatically retried.',
                ),
                if (_doctor) ...[
                  TextField(
                    key: const ValueKey('p23d-b-name'),
                    controller: _name,
                    enabled: _canWrite,
                    decoration: InputDecoration(
                      labelText: _isImaging ? 'Study name' : 'Procedure name',
                    ),
                  ),
                  DropdownButton<String>(
                    key: const ValueKey('p23d-b-category'),
                    value: _category,
                    items: [
                      for (final value in options)
                        DropdownMenuItem(value: value, child: Text(value)),
                    ],
                    onChanged: _canWrite
                        ? (value) {
                            if (value != null) {
                              setState(() => _category = value);
                            }
                          }
                        : null,
                  ),
                  if (_isImaging)
                    TextField(
                      key: const ValueKey('p23d-b-body-site'),
                      controller: _bodySite,
                      enabled: _canWrite,
                      decoration: const InputDecoration(labelText: 'Body site'),
                    ),
                  TextField(
                    key: const ValueKey('p23d-b-indication'),
                    controller: _indication,
                    enabled: _canWrite,
                    maxLines: 2,
                    decoration: const InputDecoration(
                      labelText: 'Clinical indication',
                    ),
                  ),
                  if (!_isImaging)
                    CheckboxListTile(
                      key: const ValueKey('p23d-b-consent'),
                      value: _consentRequired,
                      onChanged: _canWrite
                          ? (value) => setState(
                              () => _consentRequired = value ?? false,
                            )
                          : null,
                      title: const Text('Consent required'),
                    ),
                  FilledButton(
                    key: const ValueKey('p23d-b-create'),
                    onPressed: _canWrite ? _create : null,
                    child: const Text('Create once'),
                  ),
                  TextField(
                    key: const ValueKey('p23d-b-action-note'),
                    controller: _actionNote,
                    enabled: _canWrite,
                    maxLines: 3,
                    decoration: const InputDecoration(
                      labelText: 'Completion report / procedure note / cancellation reason',
                    ),
                  ),
                ] else
                  const Text(
                    'Read-only: only doctors may change these orders.',
                  ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('p23d-b-message')),
                OutlinedButton(
                  key: const ValueKey('p23d-b-refresh'),
                  onPressed: () => unawaited(_load()),
                  child: const Text('Refresh server view'),
                ),
                Text('Orders in active admission: ${_items.length}'),
                for (final order in _items)
                  Card(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        ListTile(
                          key: ValueKey('p23d-b-order-${order.id}'),
                          title: Text(order.name),
                          subtitle: Text(
                            '${order.category} · ${order.status} · v${order.version}',
                          ),
                        ),
                        if (!_isImaging && order.consentRequired)
                          Text(
                            order.consentVerifiedAt == null
                                ? 'Consent pending verification'
                                : 'Consent verified',
                          ),
                        if (_doctor && order.isOpen) ...[
                          if (!_isImaging &&
                              order.consentRequired &&
                              order.consentVerifiedAt == null) ...[
                            CheckboxListTile(
                              key: ValueKey('p23d-b-attest-${order.id}'),
                              value: _attestedConsentIds.contains(order.id),
                              onChanged: _canWrite
                                  ? (value) {
                                      setState(() {
                                        if (value == true) {
                                          _attestedConsentIds.add(order.id);
                                        } else {
                                          _attestedConsentIds.remove(order.id);
                                        }
                                      });
                                    }
                                  : null,
                              title: const Text(
                                'I verified clinical consent in the patient record',
                              ),
                            ),
                            OutlinedButton(
                              key: ValueKey('p23d-b-verify-${order.id}'),
                              onPressed:
                                  _canWrite &&
                                      _attestedConsentIds.contains(order.id)
                                  ? () => _act(order, 'verify-consent')
                                  : null,
                              child: const Text('Record consent verification'),
                            ),
                          ],
                          OutlinedButton(
                            key: ValueKey('p23d-b-complete-${order.id}'),
                            onPressed:
                                _canWrite &&
                                    (_isImaging ||
                                        !order.consentRequired ||
                                        order.consentVerifiedAt != null)
                                ? () => _act(order, 'complete')
                                : null,
                            child: const Text('Complete with note'),
                          ),
                          TextButton(
                            key: ValueKey('p23d-b-cancel-${order.id}'),
                            onPressed: _canWrite
                                ? () => _act(order, 'cancel')
                                : null,
                            child: const Text('Cancel with reason'),
                          ),
                        ],
                      ],
                    ),
                  ),
              ],
            ),
    );
  }
}
