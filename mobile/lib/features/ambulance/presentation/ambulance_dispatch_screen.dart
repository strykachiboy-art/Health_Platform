// P23I.S06: standalone management-only ambulance request and dispatch screen.
// Explicit review + confirmation, no route registration or automatic POST replay.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_dispatch_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_dispatch.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_read.dart';

enum AmbulanceDispatchMode { create, dispatch }

class AmbulanceDispatchScreen extends StatefulWidget {
  const AmbulanceDispatchScreen({
    super.key,
    required this.source,
    this.initialMode = AmbulanceDispatchMode.create,
  });

  final AmbulanceDispatchRemoteDataSource source;
  final AmbulanceDispatchMode initialMode;

  @override
  State<AmbulanceDispatchScreen> createState() =>
      _AmbulanceDispatchScreenState();
}

class _AmbulanceDispatchScreenState extends State<AmbulanceDispatchScreen> {
  final _patientId = TextEditingController();
  final _admissionId = TextEditingController();
  final _pickup = TextEditingController();
  final _destination = TextEditingController();
  final _notes = TextEditingController();
  final _tripId = TextEditingController();
  final _vehicleId = TextEditingController();
  final _driverId = TextEditingController();
  final _paramedicId = TextEditingController();

  late AmbulanceDispatchMode _mode;
  String _tripType = 'emergency_pickup';
  AmbulanceDispatchInspection? _inspection;
  AmbulanceDispatchConfirmation? _confirmed;
  bool _reviewed = false;
  bool _busy = false;
  bool _locked = false;
  String? _message;
  int _generation = 0;
  late int _revision;

  @override
  void initState() {
    super.initState();
    _mode = widget.initialMode;
    _revision = widget.source.reader.sessionNavigationGuard.revision;
    widget.source.reader.sessionNavigationGuard.addListener(_sessionChanged);
  }

  @override
  void didUpdateWidget(covariant AmbulanceDispatchScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source) ||
        oldWidget.initialMode != widget.initialMode) {
      oldWidget.source.reader.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      widget.source.reader.sessionNavigationGuard.addListener(_sessionChanged);
      _generation++;
      _mode = widget.initialMode;
      _revision = widget.source.reader.sessionNavigationGuard.revision;
      _inspection = null;
      _confirmed = null;
      _reviewed = false;
      _busy = false;
      // Do not recycle the same screen State into a second write workflow.
      _locked = true;
      _message = 'Workflow changed. Reopen to authorize a new operation.';
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.reader.sessionNavigationGuard.removeListener(_sessionChanged);
    for (final controller in <TextEditingController>[
      _patientId,
      _admissionId,
      _pickup,
      _destination,
      _notes,
      _tripId,
      _vehicleId,
      _driverId,
      _paramedicId,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  bool get _current =>
      !_locked &&
      widget.source.reader.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.source.reader.sessionNavigationGuard.revision == _revision;

  bool _accept(int generation) =>
      mounted && generation == _generation && _current;

  void _sessionChanged() {
    if (!mounted) {
      return;
    }
    _generation++;
    setState(() {
      _locked = true;
      _busy = false;
      _inspection = null;
      _confirmed = null;
      _reviewed = false;
      _message = 'Session or clinic changed. Reopen ambulance workflow.';
    });
  }

  void _edited() {
    setState(() {
      _reviewed = false;
      _inspection = null;
      _confirmed = null;
      _message = null;
    });
  }

  void _switch(AmbulanceDispatchMode mode) {
    if (_busy || _mode == mode) {
      return;
    }
    setState(() {
      _mode = mode;
      _reviewed = false;
      _inspection = null;
      _confirmed = null;
      _message = null;
    });
  }

  static int? _optionalId(String value) {
    final text = value.trim();
    if (text.isEmpty) {
      return null;
    }
    final parsed = int.tryParse(text);
    AmbulanceTripDraft.validateId(parsed, 'optional_id');
    return parsed;
  }

  static int _requiredId(String value) {
    final parsed = int.tryParse(value.trim());
    AmbulanceTripDraft.validateId(parsed, 'required_id');
    return parsed!;
  }

  AmbulanceTripDraft _draft() {
    final draft = AmbulanceTripDraft(
      tripType: _tripType,
      patientId: _optionalId(_patientId.text),
      admissionId: _optionalId(_admissionId.text),
      pickupAddress: _pickup.text.trim().isEmpty ? null : _pickup.text.trim(),
      destinationAddress: _destination.text.trim().isEmpty
          ? null
          : _destination.text.trim(),
      notes: _notes.text.trim().isEmpty ? null : _notes.text.trim(),
    );
    draft.toRequestBody();
    return draft;
  }

  Future<bool> _confirm(String text) async {
    final result = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('Confirm ambulance operation'),
        content: Text(text),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: const Text('Back'),
          ),
          FilledButton(
            key: const ValueKey('p23i-s06-confirm-operation'),
            onPressed: () => Navigator.pop(dialog, true),
            child: const Text('Confirm once'),
          ),
        ],
      ),
    );
    return result == true;
  }

  Future<void> _create() async {
    if (!_current ||
        _busy ||
        !_reviewed ||
        widget.source.createWriteAttempted) {
      return;
    }
    late final AmbulanceTripDraft draft;
    try {
      draft = _draft();
    } on Object {
      setState(
        () => _message = 'Invalid trip type or patient, admission, address or notes. No request sent.',
      );
      return;
    }
    final generation = ++_generation;
    final accepted = await _confirm(
      'Request one ${draft.tripType} ambulance trip. '
      'Patient: ${draft.patientId ?? 'not yet assigned'}; '
      'admission: ${draft.admissionId ?? 'none'}. '
      'This creates a REQUESTED trip; it does not dispatch a vehicle.',
    );
    if (!_accept(generation) || !accepted) {
      return;
    }
    setState(() {
      _busy = true;
      _reviewed = false;
      _inspection = null;
      _confirmed = null;
      _message = null;
    });
    try {
      final result = await widget.source.createTripOnce(draft);
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _confirmed = result;
        _message =
            'Backend confirmed trip #${result.trip.id} as REQUESTED. No vehicle dispatched.';
        _busy = false;
      });
    } on Object {
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _confirmed = null;
        _message = widget.source.createWriteAttempted
            ? 'Trip outcome is unconfirmed. Do not resubmit; verify with authorized operations.'
            : 'Trip request was not authorized. No write was sent.';
        _busy = false;
      });
    }
  }

  Future<void> _inspect() async {
    if (!_current || _busy || widget.source.dispatchWriteAttempted) {
      return;
    }
    late final int tripId;
    late final int vehicleId;
    try {
      tripId = _requiredId(_tripId.text);
      vehicleId = _requiredId(_vehicleId.text);
    } on Object {
      setState(() => _message = 'Enter valid trip and vehicle identifiers.');
      return;
    }
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _inspection = null;
      _reviewed = false;
      _confirmed = null;
      _message = null;
    });
    try {
      final evidence = await widget.source.inspectDispatch(
        tripId: tripId,
        vehicleId: vehicleId,
      );
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _inspection = evidence;
        _busy = false;
      });
    } on Object {
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        _message = 'Requested trip or available vehicle could not be verified. No dispatch sent.';
      });
    }
  }

  Future<void> _dispatch() async {
    if (!_current ||
        _busy ||
        !_reviewed ||
        widget.source.dispatchWriteAttempted ||
        _inspection == null) {
      return;
    }
    late final int driverId;
    int? paramedicId;
    try {
      driverId = _requiredId(_driverId.text);
      paramedicId = _optionalId(_paramedicId.text);
      if (driverId == paramedicId) {
        throw const FormatException('Driver and paramedic must differ.');
      }
    } on Object {
      setState(
        () => _message =
            'Check driver and paramedic identifiers. No dispatch sent.',
      );
      return;
    }
    final evidence = _inspection!;
    final generation = ++_generation;
    final accepted = await _confirm(
      'Dispatch available vehicle ${evidence.vehicle.plateNumber} '
      '(#${evidence.vehicle.id}) to requested trip #${evidence.trip.id} '
      'with driver #$driverId? This reserves the vehicle for the trip.',
    );
    if (!_accept(generation) || !accepted) {
      return;
    }
    setState(() {
      _busy = true;
      _reviewed = false;
      _inspection = null;
      _confirmed = null;
      _message = null;
    });
    try {
      final result = await widget.source.dispatchOnce(
        inspection: evidence,
        driverId: driverId,
        paramedicId: paramedicId,
      );
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        _confirmed = result;
        _message = 'Backend confirmed trip #${result.trip.id} as DISPATCHED.';
      });
    } on Object {
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        _message = widget.source.dispatchWriteAttempted
            ? 'Dispatch outcome is unconfirmed. Do not resend; inspect the trip with authorized staff.'
            : 'Dispatch evidence changed or access denied. No dispatch sent.';
      });
    }
  }

  Widget _field(
    String key,
    String label,
    TextEditingController controller, {
    TextInputType? keyboardType,
  }) => Padding(
    padding: const EdgeInsets.only(bottom: 10),
    child: TextField(
      key: ValueKey(key),
      controller: controller,
      keyboardType: keyboardType,
      onChanged: (_) => _edited(),
      decoration: InputDecoration(
        labelText: label,
        border: const OutlineInputBorder(),
      ),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final canCreate =
        _current && !_busy && _reviewed && !widget.source.createWriteAttempted;
    final canDispatch =
        _current &&
        !_busy &&
        _reviewed &&
        _inspection != null &&
        !widget.source.dispatchWriteAttempted;
    return Scaffold(
      appBar: AppBar(title: const Text('Ambulance request and dispatch')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: <Widget>[
          const Text(
            'Authorized dispatch management only. One-shot writes; backend owns all assignments and statuses.',
          ),
          Row(
            children: <Widget>[
              OutlinedButton(
                key: const ValueKey('p23i-s06-create-tab'),
                onPressed: _busy
                    ? null
                    : () => _switch(AmbulanceDispatchMode.create),
                child: const Text('Request trip'),
              ),
              const SizedBox(width: 8),
              OutlinedButton(
                key: const ValueKey('p23i-s06-dispatch-tab'),
                onPressed: _busy
                    ? null
                    : () => _switch(AmbulanceDispatchMode.dispatch),
                child: const Text('Dispatch trip'),
              ),
            ],
          ),
          if (_mode == AmbulanceDispatchMode.create) ...<Widget>[
            DropdownButton<String>(
              key: const ValueKey('p23i-s06-trip-type'),
              value: _tripType,
              isExpanded: true,
              onChanged: _busy
                  ? null
                  : (value) {
                      if (value != null) {
                        setState(() => _tripType = value);
                        _edited();
                      }
                    },
              items: ambulanceTripTypes
                  .map(
                    (type) => DropdownMenuItem<String>(
                      value: type,
                      child: Text(type),
                    ),
                  )
                  .toList(growable: false),
            ),
            _field(
              'p23i-s06-patient-id',
              'Patient ID (optional for pickup)',
              _patientId,
              keyboardType: TextInputType.number,
            ),
            _field(
              'p23i-s06-admission-id',
              'Admission ID (required for transfer/discharge)',
              _admissionId,
              keyboardType: TextInputType.number,
            ),
            _field('p23i-s06-pickup', 'Pickup address', _pickup),
            _field('p23i-s06-destination', 'Destination address', _destination),
            _field('p23i-s06-notes', 'Transport notes', _notes),
          ] else ...<Widget>[
            _field(
              'p23i-s06-trip-id',
              'Requested trip ID',
              _tripId,
              keyboardType: TextInputType.number,
            ),
            _field(
              'p23i-s06-vehicle-id',
              'Available vehicle ID',
              _vehicleId,
              keyboardType: TextInputType.number,
            ),
            _field(
              'p23i-s06-driver-id',
              'Verified driver staff ID',
              _driverId,
              keyboardType: TextInputType.number,
            ),
            _field(
              'p23i-s06-paramedic-id',
              'Paramedic/EMT staff ID (optional)',
              _paramedicId,
              keyboardType: TextInputType.number,
            ),
            OutlinedButton(
              key: const ValueKey('p23i-s06-inspect'),
              onPressed:
                  _current && !_busy && !widget.source.dispatchWriteAttempted
                  ? () => unawaited(_inspect())
                  : null,
              child: const Text('Verify trip and vehicle from server'),
            ),
            if (_inspection != null)
              Text(
                'Trip #${_inspection!.trip.id}: REQUESTED; '
                'Vehicle ${_inspection!.vehicle.plateNumber}: AVAILABLE; '
                'patient ${_inspection!.trip.patientId ?? 'unassigned'}',
                key: const ValueKey('p23i-s06-dispatch-evidence'),
              ),
          ],
          CheckboxListTile(
            key: const ValueKey('p23i-s06-reviewed'),
            title: const Text('I reviewed the intended ambulance operation.'),
            value: _reviewed,
            onChanged: !_current || _busy
                ? null
                : (value) => setState(() => _reviewed = value == true),
          ),
          FilledButton(
            key: const ValueKey('p23i-s06-submit'),
            onPressed: _mode == AmbulanceDispatchMode.create
                ? (canCreate ? () => unawaited(_create()) : null)
                : (canDispatch ? () => unawaited(_dispatch()) : null),
            child: Text(
              _mode == AmbulanceDispatchMode.create
                  ? 'Request trip once'
                  : 'Dispatch verified trip once',
            ),
          ),
          if (_busy) const Center(child: CircularProgressIndicator()),
          if (_message != null)
            Text(_message!, key: const ValueKey('p23i-s06-message')),
          if (_confirmed != null)
            Text(
              'Server-confirmed ${_confirmed!.action}: trip #${_confirmed!.trip.id}, '
              'status ${_confirmed!.trip.status}.',
              key: const ValueKey('p23i-s06-confirmed'),
            ),
        ],
      ),
    );
  }
}
