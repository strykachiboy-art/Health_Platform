// P23I.S07 — standalone, no route registration, no background writes or retry.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_lifecycle_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_lifecycle.dart';

class AmbulanceLifecycleScreen extends StatefulWidget {
  const AmbulanceLifecycleScreen({super.key, required this.source});
  final AmbulanceLifecycleRemoteDataSource source;
  @override
  State<AmbulanceLifecycleScreen> createState() =>
      _AmbulanceLifecycleScreenState();
}

class _AmbulanceLifecycleScreenState extends State<AmbulanceLifecycleScreen> {
  final _tripId = TextEditingController();
  final _vehicleId = TextEditingController();
  final _patientId = TextEditingController();
  final _status = TextEditingController();
  final _reason = TextEditingController();
  final _plate = TextEditingController();
  final _capacity = TextEditingController(text: '1');
  String _equipment = 'bls';
  AmbulanceLifecycleKind _kind = AmbulanceLifecycleKind.tripProgress;
  AmbulanceLifecycleInspection? _inspection;
  AmbulanceLifecycleConfirmation? _confirmation;
  AmbulanceLifecycleCommand? _command;
  bool _reviewed = false;
  bool _busy = false;
  bool _locked = false;
  String? _message;
  int _generation = 0;
  late int _revision;

  @override
  void initState() {
    super.initState();
    _revision = widget.source.reader.sessionNavigationGuard.revision;
    widget.source.reader.sessionNavigationGuard.addListener(_sessionChanged);
  }

  @override
  void didUpdateWidget(covariant AmbulanceLifecycleScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(widget.source, oldWidget.source)) {
      oldWidget.source.reader.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      widget.source.reader.sessionNavigationGuard.addListener(_sessionChanged);
      _generation++;
      _locked = true;
      _reviewed = false;
      _command = null;
      _inspection = null;
      _confirmation = null;
      _message = 'Workflow identity changed. Reopen the action.';
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.reader.sessionNavigationGuard.removeListener(_sessionChanged);
    for (final c in <TextEditingController>[
      _tripId,
      _vehicleId,
      _patientId,
      _status,
      _reason,
      _plate,
      _capacity,
    ]) {
      c.dispose();
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
      _reviewed = false;
      _command = null;
      _inspection = null;
      _confirmation = null;
      _message = 'Session or clinic changed. Reopen this ambulance workflow.';
    });
  }

  void _edited() {
    if (!mounted) {
      return;
    }
    setState(() {
      _reviewed = false;
      _command = null;
      _inspection = null;
      _confirmation = null;
      _message = null;
    });
  }

  static int? _id(String value) => int.tryParse(value.trim());

  AmbulanceLifecycleCommand _build() => AmbulanceLifecycleCommand(
    kind: _kind,
    tripId: switch (_kind) {
      AmbulanceLifecycleKind.vehicleStatus ||
      AmbulanceLifecycleKind.registerVehicle => null,
      _ => _id(_tripId.text),
    },
    vehicleId: _kind == AmbulanceLifecycleKind.vehicleStatus
        ? _id(_vehicleId.text)
        : null,
    patientId: _kind == AmbulanceLifecycleKind.patientLink
        ? _id(_patientId.text)
        : null,
    nextStatus:
        _kind == AmbulanceLifecycleKind.tripProgress ||
            _kind == AmbulanceLifecycleKind.vehicleStatus
        ? _status.text.trim()
        : null,
    reason: _kind == AmbulanceLifecycleKind.cancelTrip
        ? _reason.text.trim()
        : null,
    plateNumber: _kind == AmbulanceLifecycleKind.registerVehicle
        ? _plate.text.trim().toUpperCase()
        : null,
    equipmentLevel: _kind == AmbulanceLifecycleKind.registerVehicle
        ? _equipment
        : null,
    capacity: _kind == AmbulanceLifecycleKind.registerVehicle
        ? _id(_capacity.text)
        : null,
  );

  Future<void> _inspect() async {
    if (!_current || _busy || widget.source.writeAttempted) {
      return;
    }
    late final AmbulanceLifecycleCommand command;
    try {
      command = _build();
      command.validate();
    } on Object {
      setState(() => _message = 'Invalid action details. No request sent.');
      return;
    }
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _reviewed = false;
      _command = null;
      _inspection = null;
      _confirmation = null;
      _message = null;
    });
    try {
      final inspection = await widget.source.inspect(command);
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _command = command;
        _inspection = inspection;
        _message = command.kind == AmbulanceLifecycleKind.registerVehicle
            ? 'Authorized registration review. Backend will validate unique fleet identity.'
            : 'Read-only evidence loaded. Verify current status and action.';
        _busy = false;
      });
    } on Object {
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        _message = 'Authorization or fresh backend evidence unavailable. No write sent.';
      });
    }
  }

  Future<void> _apply() async {
    final command = _command;
    final inspection = _inspection;
    if (!_current ||
        _busy ||
        !_reviewed ||
        command == null ||
        inspection == null ||
        widget.source.writeAttempted) {
      return;
    }
    final generation = ++_generation;
    final approved = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('Confirm one ambulance change'),
        content: Text(
          'Apply ${command.kind.name} to '
          '${command.tripId == null ? 'vehicle ${command.vehicleId ?? command.plateNumber}' : 'trip ${command.tripId}'}? '
          'The backend is authoritative. If the outcome is uncertain, do not retry.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: const Text('Back'),
          ),
          FilledButton(
            key: const ValueKey('p23i-s07-confirm-once'),
            onPressed: () => Navigator.pop(dialog, true),
            child: const Text('Confirm once'),
          ),
        ],
      ),
    );
    if (!_accept(generation) || approved != true) {
      return;
    }
    setState(() {
      _busy = true;
      _reviewed = false;
      _confirmation = null;
      _message = null;
    });
    try {
      final confirmed = await widget.source.applyOnce(
        command: command,
        inspection: inspection,
      );
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        _confirmation = confirmed;
        _message =
            'Backend confirmed ${confirmed.kind.name}. No other action sent.';
      });
    } on Object {
      if (!_accept(generation)) {
        return;
      }
      setState(() {
        _busy = false;
        _message = widget.source.writeAttempted
            ? 'Outcome unconfirmed. Do not resubmit. Verify through authorized operations.'
            : 'Operation rejected before write. Refresh evidence and review.';
      });
    }
  }

  Widget _field(String key, String label, TextEditingController controller) =>
      TextField(
        key: ValueKey(key),
        controller: controller,
        onChanged: (_) => _edited(),
        decoration: InputDecoration(labelText: label),
      );

  @override
  Widget build(BuildContext context) {
    final isTrip =
        _kind != AmbulanceLifecycleKind.vehicleStatus &&
        _kind != AmbulanceLifecycleKind.registerVehicle;
    return Scaffold(
      appBar: AppBar(title: const Text('Ambulance operations')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            const Text(
              'Online-only, server-owned ambulance operations. No automatic retries.',
            ),
            DropdownButton<AmbulanceLifecycleKind>(
              key: const ValueKey('p23i-s07-action'),
              value: _kind,
              isExpanded: true,
              items: AmbulanceLifecycleKind.values
                  .map(
                    (kind) =>
                        DropdownMenuItem(value: kind, child: Text(kind.name)),
                  )
                  .toList(growable: false),
              onChanged: _current && !_busy && !widget.source.writeAttempted
                  ? (kind) {
                      if (kind == null) {
                        return;
                      }
                      setState(() => _kind = kind);
                      _edited();
                    }
                  : null,
            ),
            if (isTrip) _field('p23i-s07-trip-id', 'Trip ID', _tripId),
            if (_kind == AmbulanceLifecycleKind.vehicleStatus)
              _field('p23i-s07-vehicle-id', 'Vehicle ID', _vehicleId),
            if (_kind == AmbulanceLifecycleKind.tripProgress ||
                _kind == AmbulanceLifecycleKind.vehicleStatus)
              _field(
                'p23i-s07-status',
                'Next status (exact backend enum)',
                _status,
              ),
            if (_kind == AmbulanceLifecycleKind.patientLink)
              _field('p23i-s07-patient-id', 'Patient ID', _patientId),
            if (_kind == AmbulanceLifecycleKind.cancelTrip)
              _field('p23i-s07-reason', 'Cancellation reason', _reason),
            if (_kind == AmbulanceLifecycleKind.registerVehicle) ...<Widget>[
              _field('p23i-s07-plate', 'New vehicle plate', _plate),
              DropdownButton<String>(
                value: _equipment,
                isExpanded: true,
                items: const <String>['bls', 'als', 'cct']
                    .map(
                      (value) =>
                          DropdownMenuItem(value: value, child: Text(value)),
                    )
                    .toList(growable: false),
                onChanged: _current && !_busy
                    ? (next) {
                        if (next == null) {
                          return;
                        }
                        setState(() => _equipment = next);
                        _edited();
                      }
                    : null,
              ),
              _field('p23i-s07-capacity', 'Vehicle capacity', _capacity),
            ],
            const SizedBox(height: 16),
            OutlinedButton(
              key: const ValueKey('p23i-s07-inspect'),
              onPressed: _current && !_busy && !widget.source.writeAttempted
                  ? () => unawaited(_inspect())
                  : null,
              child: const Text('Inspect server evidence'),
            ),
            if (_inspection != null) ...<Widget>[
              Text(
                'Current status: ${_inspection!.trip?.status ?? _inspection!.vehicle?.status ?? 'new vehicle'}',
              ),
              CheckboxListTile(
                key: const ValueKey('p23i-s07-reviewed'),
                value: _reviewed,
                onChanged: _current && !_busy && !widget.source.writeAttempted
                    ? (value) => setState(() => _reviewed = value == true)
                    : null,
                title: const Text(
                  'I reviewed the current server evidence and one proposed action',
                ),
              ),
            ],
            FilledButton(
              key: const ValueKey('p23i-s07-submit'),
              onPressed:
                  _current &&
                      !_busy &&
                      _reviewed &&
                      _inspection != null &&
                      !widget.source.writeAttempted
                  ? () => unawaited(_apply())
                  : null,
              child: const Text('Submit one controlled change'),
            ),
            if (_message != null)
              Text(_message!, key: const ValueKey('p23i-s07-message')),
            if (_confirmation != null)
              Text(
                'Server-confirmed ${_confirmation!.kind.name}: '
                '#${_confirmation!.trip?.id ?? _confirmation!.vehicle?.id}',
                key: const ValueKey('p23i-s07-confirmed'),
              ),
          ],
        ),
      ),
    );
  }
}
