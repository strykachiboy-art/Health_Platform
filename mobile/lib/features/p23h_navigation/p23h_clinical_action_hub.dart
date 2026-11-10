import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/models/lab_order_read.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_prescription.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';

// The hub proposes actions, not authorization. Every clinical screen re-reads
// its server evidence, checks staff role/clinic/session, and requires consent.
enum P23HClinicalActionKind {
  prescribe,
  cancelPrescription,
  completePrescription,
  dispense,
  labCreateOrCancel,
  collectSample,
  linkEquipment,
  processSample,
  enterResult,
  verifyResults,
  completeLabOrder,
}

class P23HClinicalActionIntent {
  const P23HClinicalActionIntent({
    required this.kind,
    required this.patientId,
    this.prescriptionId,
    this.orderId,
    this.orderItemId,
  });

  final P23HClinicalActionKind kind;
  final int patientId;
  final int? prescriptionId;
  final int? orderId;
  final int? orderItemId;

  bool get isValid {
    bool id(int? value) => value != null && value > 0 && value <= 2147483647;
    if (!id(patientId)) {
      return false;
    }
    return switch (kind) {
      P23HClinicalActionKind.prescribe ||
      P23HClinicalActionKind.labCreateOrCancel =>
        prescriptionId == null && orderId == null && orderItemId == null,
      P23HClinicalActionKind.cancelPrescription ||
      P23HClinicalActionKind.completePrescription ||
      P23HClinicalActionKind.dispense =>
        id(prescriptionId) && orderId == null && orderItemId == null,
      P23HClinicalActionKind.collectSample ||
      P23HClinicalActionKind.linkEquipment ||
      P23HClinicalActionKind.processSample ||
      P23HClinicalActionKind.verifyResults ||
      P23HClinicalActionKind.completeLabOrder =>
        id(orderId) && prescriptionId == null && orderItemId == null,
      P23HClinicalActionKind.enterResult =>
        id(orderId) && id(orderItemId) && prescriptionId == null,
    };
  }
}

class P23HClinicalActionHub extends StatefulWidget {
  const P23HClinicalActionHub({
    super.key,
    required this.patientId,
    required this.prescriptionReader,
    required this.labReader,
    required this.onActionRequested,
  }) : assert(patientId > 0);

  final int patientId;
  final PatientPrescriptionRemoteDataSource prescriptionReader;
  final LabOrderReadRemoteDataSource labReader;
  final void Function(P23HClinicalActionIntent) onActionRequested;

  @override
  State<P23HClinicalActionHub> createState() => _P23HClinicalActionHubState();
}

class _P23HClinicalActionHubState extends State<P23HClinicalActionHub> {
  bool _loading = true;
  String? _error;
  String? _role;
  List<PatientPrescription> _prescriptions = const [];
  List<LabPatientOrder> _orders = const [];
  int _rxTotal = 0;
  int _labTotal = 0;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.prescriptionReader.sessionNavigationGuard.addListener(_invalidate);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        unawaited(_refresh());
      }
    });
  }

  @override
  void didUpdateWidget(covariant P23HClinicalActionHub oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.patientId != widget.patientId ||
        !identical(oldWidget.prescriptionReader, widget.prescriptionReader) ||
        !identical(oldWidget.labReader, widget.labReader)) {
      oldWidget.prescriptionReader.sessionNavigationGuard.removeListener(
        _invalidate,
      );
      widget.prescriptionReader.sessionNavigationGuard.addListener(_invalidate);
      _clear();
      unawaited(_refresh());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.prescriptionReader.sessionNavigationGuard.removeListener(
      _invalidate,
    );
    super.dispose();
  }

  void _clear() {
    _generation++;
    _role = null;
    _prescriptions = const [];
    _orders = const [];
    _rxTotal = 0;
    _labTotal = 0;
  }

  void _invalidate() {
    if (!mounted) {
      return;
    }
    setState(() {
      _clear();
      _loading = false;
      _error = 'Session changed. Clinical actions cleared.';
    });
    if (widget
        .prescriptionReader
        .sessionNavigationGuard
        .canAccessProtectedRoutes) {
      unawaited(_refresh());
    }
  }

  Future<void> _refresh() async {
    final guard = widget.prescriptionReader.sessionNavigationGuard;
    if (!identical(guard, widget.labReader.sessionNavigationGuard) ||
        !guard.canAccessProtectedRoutes) {
      if (mounted) {
        setState(() {
          _clear();
          _loading = false;
          _error = 'Clinical session unavailable.';
        });
      }
      return;
    }
    final revision = guard.revision;
    final generation = ++_generation;
    final patientId = widget.patientId;
    setState(() {
      _clear();
      // _clear increments generation. Use the resulting generation below.
      _loading = true;
      _error = null;
    });
    final activeGeneration = _generation;
    bool current() =>
        mounted &&
        _generation == activeGeneration &&
        guard.revision == revision &&
        guard.canAccessProtectedRoutes &&
        patientId == widget.patientId &&
        identical(guard, widget.labReader.sessionNavigationGuard);
    assert(activeGeneration > generation);

    try {
      final before = await widget.prescriptionReader.loadSession();
      final session = before.session;
      if (!current() ||
          before.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0) {
        throw const FormatException('Clinical session unavailable.');
      }
      final role = session.role.trim().toLowerCase();
      if (!const <String>{
        'doctor',
        'nurse',
        'pharmacist',
        'lab_technician',
      }.contains(role)) {
        throw const FormatException('Clinical role unavailable.');
      }
      List<PatientPrescription> prescriptions = const [];
      List<LabPatientOrder> orders = const [];
      var rxTotal = 0;
      var labTotal = 0;
      if (const <String>{'doctor', 'pharmacist'}.contains(role)) {
        final page = await widget.prescriptionReader.fetchPrescriptions(
          patientId: patientId,
          perPage: 50,
        );
        if (!current()) {
          return;
        }
        prescriptions = page.items;
        rxTotal = page.total;
      }
      if (const <String>{'doctor', 'nurse', 'lab_technician'}.contains(role)) {
        final page = await widget.labReader.fetchPatientOrders(
          patientId: patientId,
          perPage: 50,
        );
        if (!current()) {
          return;
        }
        orders = page.items;
        labTotal = page.total;
      }
      final after = await widget.prescriptionReader.loadSession();
      final confirmed = after.session;
      if (!current() ||
          after.disposition != AuthSessionDisposition.ready ||
          confirmed == null ||
          confirmed.userId != session.userId ||
          confirmed.clinicContextId != session.clinicContextId ||
          confirmed.role.trim().toLowerCase() != role ||
          confirmed.accessToken != session.accessToken ||
          confirmed.refreshToken != session.refreshToken) {
        throw const FormatException('Clinical session changed.');
      }
      setState(() {
        _role = role;
        _prescriptions = List.unmodifiable(prescriptions);
        _orders = List.unmodifiable(orders);
        _rxTotal = rxTotal;
        _labTotal = labTotal;
        _loading = false;
      });
    } on Object {
      if (!current()) {
        return;
      }
      setState(() {
        _clear();
        _error = 'Clinical records could not be verified. Retry online.';
        _loading = false;
      });
    }
  }

  void _go(
    P23HClinicalActionKind kind, {
    int? prescriptionId,
    int? orderId,
    int? itemId,
  }) {
    if (_loading ||
        _error != null ||
        _role == null ||
        !widget
            .prescriptionReader
            .sessionNavigationGuard
            .canAccessProtectedRoutes) {
      return;
    }
    final intent = P23HClinicalActionIntent(
      kind: kind,
      patientId: widget.patientId,
      prescriptionId: prescriptionId,
      orderId: orderId,
      orderItemId: itemId,
    );
    if (intent.isValid) {
      widget.onActionRequested(intent);
    }
  }

  Widget _action(
    String label,
    String key,
    P23HClinicalActionKind kind, {
    int? prescriptionId,
    int? orderId,
    int? itemId,
  }) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 3),
    child: OutlinedButton(
      key: ValueKey(key),
      onPressed: () => _go(
        kind,
        prescriptionId: prescriptionId,
        orderId: orderId,
        itemId: itemId,
      ),
      child: Text(label),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final role = _role;
    return Scaffold(
      appBar: AppBar(title: const Text('Clinical action review')),
      body: ListView(
        key: const ValueKey('p23h-clinical-hub-list'),
        padding: const EdgeInsets.all(16),
        children: [
          Text('Patient ${widget.patientId}'),
          const Text(
            'Online server records only. Every mutation requires a fresh review and explicit confirmation.',
          ),
          const SizedBox(height: 12),
          if (_loading)
            const LinearProgressIndicator(
              key: ValueKey('p23h-clinical-hub-loading'),
            ),
          if (_error != null) ...[
            Text(_error!, key: const ValueKey('p23h-clinical-hub-error')),
            OutlinedButton(
              onPressed: () => unawaited(_refresh()),
              child: const Text('Retry online'),
            ),
          ],
          if (!_loading && _error == null && role != null) ...[
            Text('Role: $role'),
            if (role == 'doctor')
              _action(
                'Create prescription',
                'p23h-hub-prescribe',
                P23HClinicalActionKind.prescribe,
              ),
            if (role == 'doctor' || role == 'nurse')
              _action(
                'Create or cancel laboratory order',
                'p23h-hub-lab-mutate',
                P23HClinicalActionKind.labCreateOrCancel,
              ),
            if (role == 'doctor' || role == 'pharmacist') ...[
              Text(
                'Prescriptions: ${_prescriptions.length} of $_rxTotal (server page)',
              ),
              for (final record in _prescriptions)
                Card(
                  key: ValueKey('p23h-hub-rx-${record.id}'),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Prescription ${record.id}: ${record.status.name}'),
                      if (record.status == PatientPrescriptionStatus.active &&
                          role == 'doctor')
                        _action(
                          'Cancel prescription',
                          'p23h-hub-rx-cancel-${record.id}',
                          P23HClinicalActionKind.cancelPrescription,
                          prescriptionId: record.id,
                        ),
                      if (record.status == PatientPrescriptionStatus.active)
                        _action(
                          'Complete prescription',
                          'p23h-hub-rx-complete-${record.id}',
                          P23HClinicalActionKind.completePrescription,
                          prescriptionId: record.id,
                        ),
                      if (record.status == PatientPrescriptionStatus.active &&
                          role == 'pharmacist')
                        _action(
                          'Dispense prescription',
                          'p23h-hub-dispense-${record.id}',
                          P23HClinicalActionKind.dispense,
                          prescriptionId: record.id,
                        ),
                    ],
                  ),
                ),
            ],
            if (role == 'doctor' ||
                role == 'nurse' ||
                role == 'lab_technician') ...[
              Text(
                'Laboratory orders: ${_orders.length} of $_labTotal (server page)',
              ),
              for (final order in _orders)
                Card(
                  key: ValueKey('p23h-hub-lab-${order.id}'),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Lab order ${order.id}: ${order.status.name}'),
                      for (final item in order.items)
                        Text(
                          'Test ${item.test?.name ?? item.testId}: '
                          '${item.flag?.name ?? 'no result flag'}',
                        ),
                      if (role == 'lab_technician' &&
                          order.status == LabOrderStatus.ordered)
                        _action(
                          'Collect sample',
                          'p23h-hub-collect-${order.id}',
                          P23HClinicalActionKind.collectSample,
                          orderId: order.id,
                        ),
                      if (role == 'lab_technician' &&
                          order.status == LabOrderStatus.sampleCollected) ...[
                        _action(
                          'Link equipment',
                          'p23h-hub-equip-${order.id}',
                          P23HClinicalActionKind.linkEquipment,
                          orderId: order.id,
                        ),
                        _action(
                          'Process sample',
                          'p23h-hub-process-${order.id}',
                          P23HClinicalActionKind.processSample,
                          orderId: order.id,
                        ),
                      ],
                      if (role == 'lab_technician' &&
                          order.status == LabOrderStatus.inProgress) ...[
                        _action(
                          'Process sample if not yet processed',
                          'p23h-hub-process-${order.id}',
                          P23HClinicalActionKind.processSample,
                          orderId: order.id,
                        ),
                        for (final item in order.items)
                          _action(
                            'Enter or revise result for test ${item.testId}',
                            'p23h-hub-result-${item.id}',
                            P23HClinicalActionKind.enterResult,
                            orderId: order.id,
                            itemId: item.id,
                          ),
                        _action(
                          'Verify results',
                          'p23h-hub-verify-${order.id}',
                          P23HClinicalActionKind.verifyResults,
                          orderId: order.id,
                        ),
                        _action(
                          'Complete verified order',
                          'p23h-hub-complete-${order.id}',
                          P23HClinicalActionKind.completeLabOrder,
                          orderId: order.id,
                        ),
                      ],
                    ],
                  ),
                ),
            ],
            const Text(
              'Missing or outdated records require online refresh. No action shown here confirms server authorization.',
            ),
            TextButton(
              onPressed: () => unawaited(_refresh()),
              child: const Text('Refresh clinical records'),
            ),
          ],
        ],
      ),
    );
  }
}
