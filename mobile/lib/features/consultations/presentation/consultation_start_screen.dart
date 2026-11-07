import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';

/// Online-only consultation creation. Uncertain writes remain locked;
/// this screen never submits an automatic second attempt.
class ConsultationStartScreen extends StatefulWidget {
  const ConsultationStartScreen({
    super.key,
    required this.dataSource,
    required this.onCreated,
    this.initialPatientId,
  });

  final ConsultationRemoteDataSource dataSource;
  final void Function(int patientId, int consultationId) onCreated;
  final int? initialPatientId;

  @override
  State<ConsultationStartScreen> createState() =>
      _ConsultationStartScreenState();
}

class _ConsultationStartScreenState extends State<ConsultationStartScreen> {
  late final TextEditingController _patient;
  final _staff = TextEditingController();
  final _appointment = TextEditingController();
  final _complaint = TextEditingController();
  final _symptoms = TextEditingController();
  String _type = 'general';
  bool _authorized = false;
  bool _loading = true;
  bool _submissionLocked = false;
  String? _message;
  int _generation = 0;

  SessionNavigationGuard get _guard => widget.dataSource.sessionNavigationGuard;

  @override
  void initState() {
    super.initState();
    _patient = TextEditingController(
      text: widget.initialPatientId?.toString() ?? '',
    );
    _guard.addListener(_sessionChanged);
    unawaited(_authorize());
  }

  @override
  void dispose() {
    _generation++;
    _guard.removeListener(_sessionChanged);
    _patient.dispose();
    _staff.dispose();
    _appointment.dispose();
    _complaint.dispose();
    _symptoms.dispose();
    super.dispose();
  }

  void _sessionChanged() {
    _generation++;
    _patient.clear();
    _staff.clear();
    _appointment.clear();
    _complaint.clear();
    _symptoms.clear();
    _authorized = false;
    _submissionLocked = false;
    _message = null;
    _loading = false;
    if (!mounted) {
      return;
    }
    setState(() {});
    if (_guard.canAccessProtectedRoutes) {
      unawaited(_authorize());
    }
  }

  Future<void> _authorize() async {
    final generation = ++_generation;
    final revision = _guard.revision;
    if (!mounted) {
      return;
    }
    setState(() {
      _loading = true;
      _authorized = false;
    });
    if (!_guard.canAccessProtectedRoutes) {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
      return;
    }
    try {
      final state = await widget.dataSource.loadSession();
      if (!mounted ||
          generation != _generation ||
          _guard.revision != revision ||
          !_guard.canAccessProtectedRoutes) {
        return;
      }
      final session = state.session;
      setState(() {
        _authorized =
            state.disposition == AuthSessionDisposition.ready &&
            session != null &&
            session.userId > 0 &&
            session.clinicContextId != null &&
            session.clinicContextId! > 0 &&
            const <String>{
              'admin',
              'doctor',
              'nurse',
            }.contains(session.role.trim().toLowerCase());
        _loading = false;
      });
    } on Object {
      if (mounted && generation == _generation && _guard.revision == revision) {
        setState(() {
          _loading = false;
          _authorized = false;
        });
      }
    }
  }

  static int? _id(String text) {
    if (!RegExp(r'^[1-9][0-9]{0,9}$').hasMatch(text)) {
      return null;
    }
    final result = int.tryParse(text);
    return result != null && result <= 2147483647 ? result : null;
  }

  static String _newKey() {
    final random = Random.secure();
    final bytes = List<int>.generate(24, (_) => random.nextInt(256));
    final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
    return 'p23c-$hex';
  }

  Future<void> _submit() async {
    if (!_authorized ||
        _loading ||
        _submissionLocked ||
        !_guard.canAccessProtectedRoutes) {
      return;
    }
    final patientId = _id(_patient.text.trim());
    final staffId = _id(_staff.text.trim());
    final appointmentRaw = _appointment.text.trim();
    final appointmentId = appointmentRaw.isEmpty ? null : _id(appointmentRaw);
    if (patientId == null ||
        staffId == null ||
        (appointmentRaw.isNotEmpty && appointmentId == null)) {
      setState(
        () => _message =
            'Enter valid patient, staff and optional appointment IDs.',
      );
      return;
    }
    final revision = _guard.revision;
    final generation = _generation;
    final command = ConsultationStartCommand(
      patientId: patientId,
      staffId: staffId,
      appointmentId: appointmentId,
      consultationType: _type,
      chiefComplaint: _complaint.text.trim().isEmpty
          ? null
          : _complaint.text.trim(),
      symptoms: _symptoms.text.trim().isEmpty ? null : _symptoms.text.trim(),
      idempotencyKey: _newKey(),
    );
    try {
      command.validate();
    } on ArgumentError {
      setState(
        () => _message = 'Review the consultation fields and try again.',
      );
      return;
    }
    setState(() {
      _submissionLocked = true;
      _message = 'Submitting once...';
    });
    try {
      final record = await widget.dataSource.start(command);
      if (!mounted ||
          generation != _generation ||
          _guard.revision != revision ||
          !_guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() => _message = 'Consultation created. Opening record...');
      widget.onCreated(record.patientId, record.id);
    } on Object {
      if (!mounted ||
          generation != _generation ||
          _guard.revision != revision ||
          !_guard.canAccessProtectedRoutes) {
        return;
      }
      setState(
        () => _message = 'Result not confirmed. Check the patient consultation history before starting a new consultation. No automatic retry was sent.',
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Start consultation')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : !_authorized
          ? const Center(child: Text('Consultation access unavailable.'))
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Text(
                  'Start an online clinical encounter. Verify the patient and staff IDs before submission.',
                ),
                TextField(
                  key: const ValueKey('p23c-patient-id'),
                  controller: _patient,
                  decoration: const InputDecoration(labelText: 'Patient ID'),
                  keyboardType: TextInputType.number,
                  enabled: !_submissionLocked,
                ),
                TextField(
                  key: const ValueKey('p23c-staff-id'),
                  controller: _staff,
                  decoration: const InputDecoration(labelText: 'Staff ID'),
                  keyboardType: TextInputType.number,
                  enabled: !_submissionLocked,
                ),
                TextField(
                  key: const ValueKey('p23c-appointment-id'),
                  controller: _appointment,
                  decoration: const InputDecoration(
                    labelText: 'Appointment ID (optional)',
                  ),
                  keyboardType: TextInputType.number,
                  enabled: !_submissionLocked,
                ),
                DropdownButton<String>(
                  key: const ValueKey('p23c-type'),
                  value: _type,
                  items: const [
                    DropdownMenuItem(value: 'general', child: Text('General')),
                    DropdownMenuItem(
                      value: 'follow_up',
                      child: Text('Follow-up'),
                    ),
                    DropdownMenuItem(
                      value: 'specialist',
                      child: Text('Specialist'),
                    ),
                    DropdownMenuItem(
                      value: 'emergency',
                      child: Text('Emergency'),
                    ),
                  ],
                  onChanged: _submissionLocked
                      ? null
                      : (value) {
                          if (value != null) {
                            setState(() => _type = value);
                          }
                        },
                ),
                TextField(
                  key: const ValueKey('p23c-complaint'),
                  controller: _complaint,
                  decoration: const InputDecoration(
                    labelText: 'Chief complaint (optional)',
                  ),
                  maxLines: 2,
                  enabled: !_submissionLocked,
                ),
                TextField(
                  key: const ValueKey('p23c-symptoms'),
                  controller: _symptoms,
                  decoration: const InputDecoration(
                    labelText: 'Symptoms (optional)',
                  ),
                  maxLines: 2,
                  enabled: !_submissionLocked,
                ),
                const SizedBox(height: 16),
                ElevatedButton(
                  key: const ValueKey('p23c-submit'),
                  onPressed: _submissionLocked ? null : _submit,
                  child: const Text('Start once'),
                ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('p23c-message')),
                const Text(
                  'Clinical note editing, signing and amendments are separate verified workflows.',
                ),
              ],
            ),
    );
  }
}
