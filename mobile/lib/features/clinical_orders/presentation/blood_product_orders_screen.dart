import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/blood_product_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/blood_product_order.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

/// Clinic-scoped, online-only blood product workflow. Doctor create/cancel
/// uses current active admission; lab verification/issue uses an exact
/// server-fetched order ID. No compatibility calculation is performed here.
class BloodProductOrdersScreen extends StatefulWidget {
  const BloodProductOrdersScreen({
    super.key,
    required this.unifiedSource,
    this.admissionDataSource,
    this.patientId,
  });

  final ClinicalOrdersRemoteDataSource unifiedSource;
  final PatientAdmissionRemoteDataSource? admissionDataSource;
  final int? patientId;

  @override
  State<BloodProductOrdersScreen> createState() =>
      _BloodProductOrdersScreenState();
}

class _BloodProductOrdersScreenState extends State<BloodProductOrdersScreen> {
  late BloodProductRemoteDataSource _source;
  final _lookupId = TextEditingController();
  final _units = TextEditingController(text: '1');
  final _indication = TextEditingController();
  final _crossmatch = TextEditingController();
  final _issueUnits = TextEditingController(text: '1');
  final _actionNote = TextEditingController();
  String _product = 'packed_red_blood_cells';
  bool _loading = true;
  bool _reading = false;
  bool _writeLocked = false;
  bool _doctor = false;
  bool _lab = false;
  bool _attestedCompatibility = false;
  bool _attestedIssuance = false;
  PatientAdmission? _admission;
  BloodProductOrder? _detail;
  List<BloodProductOrder> _items = const <BloodProductOrder>[];
  String? _message;
  int _generation = 0;

  bool get _current =>
      widget.unifiedSource.sessionNavigationGuard.canAccessProtectedRoutes;
  bool get _canWrite => !_loading && !_reading && !_writeLocked && _current;

  @override
  void initState() {
    super.initState();
    _source = BloodProductRemoteDataSource(unifiedSource: widget.unifiedSource);
    widget.unifiedSource.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant BloodProductOrdersScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.patientId != widget.patientId ||
        !identical(oldWidget.unifiedSource, widget.unifiedSource) ||
        !identical(oldWidget.admissionDataSource, widget.admissionDataSource)) {
      oldWidget.unifiedSource.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      widget.unifiedSource.sessionNavigationGuard.addListener(_sessionChanged);
      _source = BloodProductRemoteDataSource(
        unifiedSource: widget.unifiedSource,
      );
      _clear();
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.unifiedSource.sessionNavigationGuard.removeListener(_sessionChanged);
    for (final controller in <TextEditingController>[
      _lookupId,
      _units,
      _indication,
      _crossmatch,
      _issueUnits,
      _actionNote,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  void _clear() {
    _generation++;
    for (final controller in <TextEditingController>[
      _lookupId,
      _indication,
      _crossmatch,
      _actionNote,
    ]) {
      controller.clear();
    }
    _units.text = '1';
    _issueUnits.text = '1';
    _admission = null;
    _detail = null;
    _items = const <BloodProductOrder>[];
    _doctor = false;
    _lab = false;
    _reading = false;
    _writeLocked = false;
    _attestedCompatibility = false;
    _attestedIssuance = false;
    _message = null;
  }

  void _sessionChanged() {
    _clear();
    if (!mounted) return;
    setState(() => _loading = false);
    if (_current) unawaited(_load());
  }

  static bool _sameSession(AuthTokenResponse before, AuthTokenResponse after) =>
      before.userId == after.userId &&
      before.clinicContextId == after.clinicContextId &&
      before.role == after.role &&
      before.accessToken == after.accessToken &&
      before.refreshToken == after.refreshToken;

  bool _active(int revision, int generation) {
    return mounted &&
        generation == _generation &&
        widget.unifiedSource.sessionNavigationGuard.revision == revision &&
        _current;
  }

  Future<void> _load() async {
    final guard = widget.unifiedSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_generation;
    if (!mounted) return;
    setState(() {
      _loading = true;
      _admission = null;
      _items = const <BloodProductOrder>[];
      _detail = null;
      _doctor = false;
      _lab = false;
      _message = null;
    });
    if (!_current) {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
      return;
    }
    try {
      final before = await _source.authorizedSession();
      if (!_active(revision, generation)) return;
      final role = before.role.trim().toLowerCase();
      final doctor = role == 'doctor';
      final lab = role == 'lab_technician';
      PatientAdmission? admission;
      List<BloodProductOrder> items = const <BloodProductOrder>[];
      if (widget.patientId != null && !lab) {
        final admissions = widget.admissionDataSource;
        if (admissions == null ||
            !identical(admissions.sessionNavigationGuard, guard)) {
          throw const BloodProductSessionException();
        }
        admission = await admissions.fetchCurrentAdmission(
          patientId: widget.patientId!,
        );
        if (!_active(revision, generation)) return;
        if (admission != null) {
          items = (await _source.list(
            patientId: widget.patientId!,
            admissionId: admission.id,
          )).items;
          if (!_active(revision, generation)) return;
        }
      }
      final after = await _source.authorizedSession();
      if (!_active(revision, generation) || !_sameSession(before, after)) {
        return;
      }
      setState(() {
        _loading = false;
        _doctor = doctor;
        _lab = lab;
        _admission = admission;
        _items = items;
      });
    } on Object {
      if (!_active(revision, generation)) return;
      setState(() {
        _loading = false;
        _doctor = false;
        _lab = false;
        _admission = null;
        _items = const <BloodProductOrder>[];
        _detail = null;
        _message = 'Blood-product access or admission could not be verified.';
      });
    }
  }

  Future<void> _lookup() async {
    if (_loading || _reading || !_current) return;
    final text = _lookupId.text;
    final parsed = int.tryParse(text);
    if (!RegExp(r'^[1-9][0-9]{0,9}$').hasMatch(text) ||
        parsed == null ||
        parsed > 2147483647) {
      setState(() => _message = 'Enter a valid blood-product order ID.');
      return;
    }
    final guard = widget.unifiedSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;
    setState(() {
      _reading = true;
      _detail = null;
      _message = null;
      _attestedCompatibility = false;
      _attestedIssuance = false;
    });
    try {
      final order = await _source.get(
        orderId: parsed,
        patientId: widget.patientId,
        admissionId: _lab ? null : _admission?.id,
      );
      if (!_active(revision, generation)) return;
      setState(() {
        _reading = false;
        _detail = order;
      });
    } on Object {
      if (!_active(revision, generation)) return;
      setState(() {
        _reading = false;
        _detail = null;
        _message = 'Order not found or not authorized for this session.';
      });
    }
  }

  Future<void> _write(Future<BloodProductOrder> Function() operation) async {
    if (!_canWrite) return;
    final guard = widget.unifiedSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;
    setState(() {
      _writeLocked = true;
      _message = 'Submitting once. No automatic retry will be sent.';
    });
    try {
      final order = await operation();
      if (!_active(revision, generation)) return;
      setState(
        () => _message =
            'Server confirmed order #${order.id}: ${order.status}. '
            'Leave and reopen before another write.',
      );
    } on Object {
      if (!_active(revision, generation)) return;
      setState(
        () => _message =
            'Outcome not confirmed. Do not repeat this action. '
            'Verify the server record and coordinate with the clinical team.',
      );
    }
  }

  void _create() {
    if (!_doctor || !_canWrite || _admission == null) return;
    final units = int.tryParse(_units.text);
    if (units == null ||
        units < 1 ||
        units > 100 ||
        _indication.text.trim().isEmpty) {
      setState(() => _message = 'Provide valid units and clinical indication.');
      return;
    }
    final admission = _admission!;
    final indication = _indication.text.trim();
    final product = _product;
    unawaited(
      _write(
        () => _source.create(
          patientId: widget.patientId!,
          admissionId: admission.id,
          productType: product,
          unitsRequested: units,
          indication: indication,
        ),
      ),
    );
  }

  void _verify(BloodProductOrder order) {
    if (!_lab ||
        !_canWrite ||
        !order.canVerify ||
        !_attestedCompatibility ||
        _crossmatch.text.trim().isEmpty) {
      setState(
        () => _message = 'A validated laboratory crossmatch and acknowledgment are required.',
      );
      return;
    }
    final reference = _crossmatch.text.trim();
    unawaited(
      _write(
        () => _source.verifyStandard(
          order: order,
          crossmatchReference: reference,
          laboratoryAttested: true,
        ),
      ),
    );
  }

  void _fulfill(BloodProductOrder order) {
    final units = int.tryParse(_issueUnits.text);
    if (!_lab ||
        !_canWrite ||
        !order.canFulfill ||
        !_attestedIssuance ||
        units == null ||
        units < 1 ||
        units > order.unitsRequested ||
        _actionNote.text.trim().isEmpty) {
      setState(
        () => _message =
            'Verified order, acknowledgment, quantity and issue note required.',
      );
      return;
    }
    final note = _actionNote.text.trim();
    unawaited(
      _write(
        () => _source.fulfill(
          order: order,
          unitsIssued: units,
          note: note,
          issuanceAttested: true,
        ),
      ),
    );
  }

  void _cancel(BloodProductOrder order) {
    if (!_doctor ||
        !_canWrite ||
        !order.isOpen ||
        _actionNote.text.trim().isEmpty ||
        _admission?.id != order.admissionId) {
      setState(() => _message = 'Reason and verified admission required.');
      return;
    }
    final reason = _actionNote.text.trim();
    unawaited(_write(() => _source.cancel(order: order, reason: reason)));
  }

  Widget _orderActions(BloodProductOrder order) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Order #${order.id} · ${order.productType} · ${order.status}'),
        Text('Patient #${order.patientId} / admission #${order.admissionId}'),
        Text(
          'Requested ${order.unitsRequested} units · '
          'Issued ${order.unitsIssued} · ${order.compatibilityStatus}',
        ),
        if (order.compatibilityStatus == 'emergency_release')
          const Text(
            'Emergency release recorded by backend. '
            'This screen does not initiate emergency release.',
          ),
        if (_lab && order.canVerify) ...[
          TextField(
            key: ValueKey('p23d-d-crossmatch-${order.id}'),
            controller: _crossmatch,
            enabled: _canWrite,
            decoration: const InputDecoration(
              labelText: 'Verified laboratory crossmatch reference',
            ),
          ),
          CheckboxListTile(
            key: ValueKey('p23d-d-verify-attestation-${order.id}'),
            value: _attestedCompatibility,
            onChanged: _canWrite
                ? (value) =>
                      setState(() => _attestedCompatibility = value == true)
                : null,
            title: const Text(
              'I have independently verified this crossmatch '
              'under the laboratory protocol.',
            ),
          ),
          FilledButton(
            key: ValueKey('p23d-d-verify-${order.id}'),
            onPressed: _canWrite && _attestedCompatibility
                ? () => _verify(order)
                : null,
            child: const Text('Record verified compatibility once'),
          ),
        ],
        if (_lab &&
            order.canFulfill &&
            order.compatibilityStatus == 'compatible') ...[
          TextField(
            key: ValueKey('p23d-d-issue-units-${order.id}'),
            controller: _issueUnits,
            enabled: _canWrite,
            decoration: const InputDecoration(labelText: 'Units issued'),
            keyboardType: TextInputType.number,
          ),
          TextField(
            key: ValueKey('p23d-d-issue-note-${order.id}'),
            controller: _actionNote,
            enabled: _canWrite,
            decoration: const InputDecoration(labelText: 'Issue note'),
          ),
          CheckboxListTile(
            key: ValueKey('p23d-d-issue-attestation-${order.id}'),
            value: _attestedIssuance,
            onChanged: _canWrite
                ? (value) => setState(() => _attestedIssuance = value == true)
                : null,
            title: const Text(
              'I verified the laboratory release and '
              'patient/order identity before issue.',
            ),
          ),
          FilledButton(
            key: ValueKey('p23d-d-fulfill-${order.id}'),
            onPressed: _canWrite && _attestedIssuance
                ? () => _fulfill(order)
                : null,
            child: const Text('Record issue once'),
          ),
        ],
        if (_doctor && _admission?.id == order.admissionId && order.isOpen) ...[
          TextField(
            key: ValueKey('p23d-d-cancel-note-${order.id}'),
            controller: _actionNote,
            enabled: _canWrite,
            decoration: const InputDecoration(labelText: 'Cancellation reason'),
          ),
          FilledButton(
            key: ValueKey('p23d-d-cancel-${order.id}'),
            onPressed: _canWrite ? () => _cancel(order) : null,
            child: const Text('Cancel order once'),
          ),
        ],
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final choices = BloodProductOrder.productTypes.toList()..sort();
    return Scaffold(
      appBar: AppBar(title: const Text('Blood-product orders')),
      body: _loading
          ? const Center(
              child: CircularProgressIndicator(key: ValueKey('p23d-d-loading')),
            )
          : ListView(
              key: const ValueKey('p23d-d-ready'),
              padding: const EdgeInsets.all(16),
              children: [
                const Text(
                  'Online only. This screen does not determine blood '
                  'compatibility or support emergency release.',
                ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('p23d-d-message')),
                if (_doctor && widget.patientId != null && _admission == null)
                  const Text(
                    'No verified active admission. Ordering disabled.',
                  ),
                if (_doctor && _admission != null) ...[
                  Text(
                    'Verified admission #${_admission!.id} for '
                    'patient #${widget.patientId}',
                  ),
                  DropdownButton<String>(
                    key: const ValueKey('p23d-d-product'),
                    value: _product,
                    items: choices
                        .map(
                          (choice) => DropdownMenuItem(
                            value: choice,
                            child: Text(choice),
                          ),
                        )
                        .toList(),
                    onChanged: _canWrite
                        ? (value) {
                            if (value != null) setState(() => _product = value);
                          }
                        : null,
                  ),
                  TextField(
                    key: const ValueKey('p23d-d-request-units'),
                    controller: _units,
                    enabled: _canWrite,
                    decoration: const InputDecoration(
                      labelText: 'Units requested',
                    ),
                    keyboardType: TextInputType.number,
                  ),
                  TextField(
                    key: const ValueKey('p23d-d-indication'),
                    controller: _indication,
                    enabled: _canWrite,
                    decoration: const InputDecoration(
                      labelText: 'Clinical indication',
                    ),
                  ),
                  FilledButton(
                    key: const ValueKey('p23d-d-create'),
                    onPressed: _canWrite ? _create : null,
                    child: const Text('Create blood-product order once'),
                  ),
                ],
                if (_lab || _doctor || widget.patientId == null) ...[
                  const Text(
                    'Lookup an exact server order ID. No admission '
                    'read privilege is assumed for laboratory staff.',
                  ),
                  TextField(
                    key: const ValueKey('p23d-d-order-id'),
                    controller: _lookupId,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(labelText: 'Order ID'),
                  ),
                  OutlinedButton(
                    key: const ValueKey('p23d-d-lookup'),
                    onPressed: _reading || !_current ? null : _lookup,
                    child: const Text('Fetch order from server'),
                  ),
                ],
                if (_detail != null) ...[
                  const Divider(),
                  _orderActions(_detail!),
                ],
                for (final order in _items) ...[
                  const Divider(),
                  Text(
                    'Order #${order.id} · ${order.productType} · ${order.status}',
                  ),
                  OutlinedButton(
                    key: ValueKey('p23d-d-view-${order.id}'),
                    onPressed: _reading || !_current
                        ? null
                        : () {
                            _lookupId.text = order.id.toString();
                            unawaited(_lookup());
                          },
                    child: const Text('Load verified order details'),
                  ),
                ],
              ],
            ),
    );
  }
}
