import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:image_picker/image_picker.dart';
import 'package:clinic_system_pro/features/administration/data/wd05_administration_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/doctor_observation_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_consultation.dart';

class DoctorDirectoryScreen extends StatefulWidget {
  const DoctorDirectoryScreen({super.key, required this.source, this.profileOnly = false});
  final DoctorObservationSource source;
  final bool profileOnly;

  @override
  State<DoctorDirectoryScreen> createState() => _DoctorDirectoryScreenState();
}

class _DoctorDirectoryScreenState extends State<DoctorDirectoryScreen> {
  DoctorDirectorySession? _session;
  DoctorDirectoryPage<Patient>? _patients;
  Wd05AdministrationReadPage? _staff;
  DoctorDirectoryPage<PatientConsultation>? _consultations;
  DoctorOwnProfile? _profile;
  int? _staffId;
  bool _busy = false;
  bool _locked = false;
  bool _uncertain = false;
  int _generation = 0;
  String? _message;
  final _first = TextEditingController();
  final _last = TextEditingController();
  final _phone = TextEditingController();
  final _specialty = TextEditingController();

  @override
  void initState() {
    super.initState();
    widget.source.reader.sessionNavigationGuard.addListener(_invalidate);
    unawaited(_start());
  }

  void _clearProfile() {
    _profile = null;
    for (final controller in [_first, _last, _phone, _specialty]) {
      controller.clear();
    }
  }

  void _invalidate() {
    if (!mounted) {
      return;
    }
    ++_generation;
    setState(() { _locked = true; _session = null; _patients = null; _staff = null;
      _staffId = null; _consultations = null; _clearProfile(); _message = null; _busy = false; });
  }

  @override
  void dispose() {
    ++_generation;
    widget.source.reader.sessionNavigationGuard.removeListener(_invalidate);
    for (final controller in [_first, _last, _phone, _specialty]) {
      controller.dispose();
    }
    super.dispose();
  }

  bool _current(int generation) => mounted && generation == _generation && !_locked &&
      _session != null && widget.source.current(_session!.revision);

  Future<void> _start() async {
    final generation = ++_generation;
    setState(() => _busy = true);
    try {
      final session = await widget.source.directorySession();
      if (mounted && generation == _generation && !_locked && widget.source.current(session.revision)) {
        setState(() { _session = session; _busy = false; });
        if (widget.profileOnly) {
          await _loadProfile();
        } else {
          await _loadPatients();
        }
      }
    } on Object {
      if (mounted && generation == _generation) {
        setState(() { _locked = true; _busy = false; });
      }
    }
  }

  Future<void> _run<T>(Future<T> Function(DoctorDirectorySession) request,
      void Function(T) apply, {VoidCallback? clear, bool mutation = false}) async {
    final session = _session;
    if (_busy || _locked || session == null || (mutation && _uncertain)) {
      return;
    }
    final generation = ++_generation;
    setState(() { _busy = true; _message = null; clear?.call(); });
    try {
      final data = await request(session);
      if (_current(generation)) {
        setState(() => apply(data));
      }
    } on DoctorObservationConflict {
      if (_current(generation)) {
        setState(() { _clearProfile(); _message = 'Server record changed. Reload and review before submitting changes.'; });
      }
    } on DoctorObservationUnconfirmed {
      if (_current(generation)) {
        setState(() { _uncertain = true; _clearProfile(); _message = 'Outcome unconfirmed. Reload the server record; do not retry.'; });
      }
    } on Object {
      if (_current(generation)) {
        setState(() => _message = 'Server record or operation could not be verified.');
      }
    } finally {
      if (_current(generation)) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _loadPatients({int page = 1}) => _run(
    (session) => widget.source.myPatients(session, page: page), (data) => _patients = data,
    clear: () { _patients = null; _staff = null; _staffId = null; _consultations = null; });

  Future<void> _loadStaff({int page = 1}) => _run(
    (session) => widget.source.staffDirectory(session, page: page), (data) => _staff = data,
    clear: () { _patients = null; _staff = null; _staffId = null; _consultations = null; });

  Future<void> _loadConsultations(int id, {int page = 1}) => _run(
    (session) => widget.source.staffHistory(session, id, page: page), (data) { _consultations = data; _staffId = id; },
    clear: () { _consultations = null; _staffId = null; });

  void _showProfile(DoctorOwnProfile profile) {
    _profile = profile;
    _first.text = profile.firstName;
    _last.text = profile.lastName;
    _phone.text = profile.phone ?? '';
    _specialty.text = profile.specialty ?? '';
  }

  Future<void> _loadProfile() => _run(widget.source.ownProfile, _showProfile, clear: _clearProfile);

  Future<void> _confirm(String title, Future<DoctorOwnProfile> Function() request) async {
    if (_busy || _locked || _uncertain) {
      return;
    }
    final generation = ++_generation;
    setState(() => _busy = true);
    final accepted = await showDialog<bool>(context: context, builder: (ctx) => AlertDialog(
      title: Text(title), content: const Text('Only your authenticated profile will be changed. Confirm the reviewed action.'),
      actions: [TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Confirm'))],
    ));
    if (!_current(generation)) {
      return;
    }
    setState(() => _busy = false);
    if (accepted == true) {
      await _run((_) => request(), (result) { _showProfile(result); _message = 'Server confirmed profile change.'; }, mutation: true);
    }
  }

  Future<void> _edit(DoctorOwnProfile profile) async {
    final first = _first.text;
    final last = _last.text;
    final phone = _phone.text;
    final specialty = _specialty.text;
    await _confirm('Save own profile?', () => widget.source.updateOwnProfile(profile,
      firstName: first, lastName: last, phone: phone, specialty: specialty));
  }

  Future<void> _image(DoctorOwnProfile profile) async {
    if (_busy || _locked || _uncertain) {
      return;
    }
    final generation = ++_generation;
    setState(() => _busy = true);
    try {
      final selection = await ImagePicker().pickImage(source: ImageSource.gallery);
      if (!_current(generation)) {
        return;
      }
      if (selection == null) {
        return;
      }
      if (await selection.length() > 5 * 1024 * 1024) {
        throw const FormatException('Profile image too large.');
      }
      final bytes = await selection.readAsBytes();
      DoctorDirectoryProfile.imageMime(bytes);
      if (!_current(generation)) {
        return;
      }
      setState(() => _busy = false);
      await _confirm('Upload selected profile image?', () => widget.source.uploadOwnImage(profile, bytes));
    } on Object {
      if (_current(generation)) {
        setState(() => _message = 'Choose a valid PNG or JPEG image of at most 5 MB.');
      }
    } finally {
      if (_current(generation)) {
        setState(() => _busy = false);
      }
    }
  }

  Widget _field(TextEditingController controller, String label, int limit) => TextField(
    controller: controller, enabled: !_busy && !_uncertain, maxLength: limit,
    decoration: InputDecoration(labelText: label));

  List<Widget> _paging(int page, int perPage, int total, Future<void> Function(int) request) => [
    if (page > 1) TextButton(onPressed: _busy ? null : () => request(page - 1), child: const Text('Previous')),
    if (page * perPage < total) TextButton(onPressed: _busy ? null : () => request(page + 1), child: const Text('Next')),
  ];

  @override
  Widget build(BuildContext context) {
    if (_locked || (_session != null && !widget.source.current(_session!.revision))) {
      return const Scaffold(body: Center(child: Text('Doctor workspace unavailable.')));
    }
    final patients = _patients;
    final staff = _staff;
    final consultations = _consultations;
    final profile = _profile;
    return Scaffold(appBar: AppBar(title: Text(widget.profileOnly ? 'Manage own profile' : 'Consultation history')),
      body: SafeArea(child: ListView(padding: const EdgeInsets.all(16), children: [
        if (_busy) const LinearProgressIndicator(),
        if (_message != null) Text(_message!),
        if (_session != null && widget.profileOnly) ...[
          OutlinedButton(onPressed: _busy ? null : _loadProfile, child: const Text('Reload own profile')),
          if (profile != null) ...[
            Text('Staff #${profile.staffId} · Clinic #${_session!.clinicId}'),
            _field(_first, 'First name', 150), _field(_last, 'Last name', 150),
            _field(_phone, 'Phone (optional)', 50), _field(_specialty, 'Specialty (optional)', 255),
            const Text('Login email is not edited here. Role and clinic are server-controlled.'),
            FilledButton(onPressed: _busy || _uncertain ? null : () => _edit(profile), child: const Text('Review profile changes')),
            Text(profile.hasImage ? 'Profile image registered by server' : 'No registered profile image'),
            OutlinedButton(onPressed: _busy || _uncertain ? null : () => _image(profile), child: const Text('Select profile image')),
            if (profile.hasImage) OutlinedButton(onPressed: _busy || _uncertain ? null : () => _confirm('Remove current profile image?', () => widget.source.removeOwnImage(profile)), child: const Text('Remove profile image')),
          ],
        ] else if (_session != null) ...[
          OutlinedButton(onPressed: _busy ? null : () => _loadPatients(), child: const Text('My previously seen patients')),
          OutlinedButton(onPressed: _busy ? null : () => _loadStaff(), child: const Text('Consultations by clinic staff')),
          if (patients != null) ...[
            if (patients.items.isEmpty) const Text('No patients returned.'),
            for (final patient in patients.items) ListTile(title: Text(patient.fullName),
              subtitle: Text(patient.patientNumber), onTap: _busy ? null : () => context.push<void>('/patients/${patient.id}')),
            ..._paging(patients.page, 20, patients.total, (page) => _loadPatients(page: page)),
          ],
          if (staff != null) ...[
            for (final item in staff.items) ListTile(title: Text(item.title), subtitle: Text(item.description),
              onTap: _busy ? null : () => _loadConsultations(item.id)),
            ..._paging(staff.page, staff.perPage, staff.total, (page) => _loadStaff(page: page)),
          ],
          if (consultations != null && _staffId != null) ...[
            const Divider(), Text('Consultations for staff #$_staffId'),
            if (consultations.items.isEmpty) const Text('No consultations returned.'),
            for (final record in consultations.items) ListTile(title: Text('Consultation #${record.id} · ${record.status.name}'),
              subtitle: Text('Patient #${record.patientId} · ${record.startedAt ?? 'Date unavailable'}'),
              onTap: _busy ? null : () => context.push<void>('/consultations/patient/${record.patientId}/${record.id}')),
            ..._paging(consultations.page, 20, consultations.total, (page) => _loadConsultations(_staffId!, page: page)),
          ],
        ],
      ])));
  }
}
