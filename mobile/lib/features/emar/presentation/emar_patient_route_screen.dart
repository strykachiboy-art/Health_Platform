// P23F.S03.C — authenticated patient-to-current-admission eMAR navigation.
// No route ID or stale row grants authorization to verify/administer a dose.
import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/emar/data/emar_barcode_verification_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/emar_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';
import 'package:clinic_system_pro/features/emar/presentation/emar_barcode_verification_screen.dart';
import 'package:clinic_system_pro/features/emar/presentation/emar_due_schedule_screen.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';

/// Resolves the current admission from the server; never trusts a route-supplied
/// admission/clinic ID. Only nurse and doctor sessions may read eMAR here.
class EmarPatientRouteScreen extends StatefulWidget {
  const EmarPatientRouteScreen({
    super.key,
    required this.admissions,
    required this.patientId,
  });

  final PatientAdmissionRemoteDataSource admissions;
  final int patientId;

  @override
  State<EmarPatientRouteScreen> createState() => _EmarPatientRouteScreenState();
}

class _EmarPatientRouteScreenState extends State<EmarPatientRouteScreen> {
  late EmarReadRemoteDataSource _emar;
  PatientAdmission? _currentAdmission;
  bool _nurse = false;
  bool _loading = true;
  bool _opening = false;
  bool _verificationOpened = false;
  bool _mustReopen = false;
  String? _message;
  int _generation = 0;
  int _scheduleEpoch = 0;

  SessionNavigationGuard get _guard => widget.admissions.sessionNavigationGuard;
  bool get _available => _guard.canAccessProtectedRoutes && !_mustReopen;

  bool _valid(int generation, int revision) =>
      mounted &&
      _available &&
      _generation == generation &&
      _guard.revision == revision;

  @override
  void initState() {
    super.initState();
    _emar = _makeReader();
    _guard.addListener(_sessionChanged);
    unawaited(_load());
  }

  EmarReadRemoteDataSource _makeReader() => EmarReadRemoteDataSource(
    apiClient: widget.admissions.apiClient,
    loadSession: widget.admissions.loadSession,
    sessionNavigationGuard: widget.admissions.sessionNavigationGuard,
  );

  @override
  void didUpdateWidget(covariant EmarPatientRouteScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.admissions, widget.admissions) ||
        oldWidget.patientId != widget.patientId) {
      oldWidget.admissions.sessionNavigationGuard.removeListener(
        _sessionChanged,
      );
      widget.admissions.sessionNavigationGuard.addListener(_sessionChanged);
      _emar = _makeReader();
      _generation++;
      _currentAdmission = null;
      _nurse = false;
      _opening = false;
      _verificationOpened = false;
      _message = null;
      _mustReopen = false;
      _scheduleEpoch++;
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    _guard.removeListener(_sessionChanged);
    super.dispose();
  }

  void _sessionChanged() {
    _generation++;
    _currentAdmission = null;
    _nurse = false;
    _loading = false;
    _opening = false;
    _verificationOpened = true;
    _message = null;
    _mustReopen = true;
    if (mounted) {
      setState(() {});
    }
  }

  Future<AuthTokenResponse> _authorized() async {
    final state = await widget.admissions.loadSession();
    final session = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.userId <= 0 ||
        session.clinicContextId == null ||
        session.clinicContextId! <= 0 ||
        session.accessToken.trim().isEmpty ||
        session.accessToken != session.accessToken.trim() ||
        !const <String>{
          'doctor',
          'nurse',
        }.contains(session.role.trim().toLowerCase())) {
      throw const EmarReadSessionException();
    }
    return session;
  }

  static bool _sameSession(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<void> _load() async {
    if (!_available) {
      return;
    }
    final revision = _guard.revision;
    final generation = ++_generation;
    if (!mounted) {
      return;
    }
    setState(() {
      _loading = true;
      _opening = false;
      _nurse = false;
      _currentAdmission = null;
      _message = null;
    });
    try {
      if (widget.patientId <= 0 || widget.patientId > 2147483647) {
        throw const FormatException('Invalid patient scope.');
      }
      final before = await _authorized();
      if (!_valid(generation, revision)) {
        return;
      }
      final admission = await widget.admissions.fetchCurrentAdmission(
        patientId: widget.patientId,
      );
      if (!_valid(generation, revision)) {
        return;
      }
      final after = await _authorized();
      if (!_sameSession(before, after)) {
        throw const EmarReadSessionException();
      }
      if (!_valid(generation, revision)) {
        return;
      }
      if (admission == null) {
        setState(() {
          _loading = false;
          _message = 'No active admission returned for this patient.';
        });
        return;
      }
      if (!admission.isActive || admission.patientId != widget.patientId) {
        throw const FormatException('Patient admission changed.');
      }
      setState(() {
        _loading = false;
        _nurse = before.role.trim().toLowerCase() == 'nurse';
        _currentAdmission = admission;
      });
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _loading = false;
        _nurse = false;
        _currentAdmission = null;
        _message =
            'Medication schedule access or admission could not be verified.';
      });
    }
  }

  static bool _sameClinical(
    EmarAdministration before,
    EmarAdministration now,
  ) =>
      before.id == now.id &&
      before.clinicId == now.clinicId &&
      before.patientId == now.patientId &&
      before.admissionId == now.admissionId &&
      before.prescriptionItemId == now.prescriptionItemId &&
      before.status == now.status &&
      before.recordVersion == now.recordVersion &&
      before.sourceOrderVersion == now.sourceOrderVersion &&
      before.doseValue == now.doseValue &&
      before.doseUnit == now.doseUnit &&
      before.routeCode == now.routeCode &&
      before.scheduledAt == now.scheduledAt;

  /// The selected row is only a navigation hint: admission, role and eMAR
  /// detail are re-read before constructing a fresh, one-shot verifier.
  Future<void> _openVerification(EmarAdministration selected) async {
    if (!_available ||
        !_nurse ||
        _opening ||
        _verificationOpened ||
        _loading ||
        _currentAdmission == null ||
        !selected.isPending) {
      return;
    }
    final admission = _currentAdmission!;
    final revision = _guard.revision;
    final generation = _generation;
    if (selected.patientId != widget.patientId ||
        selected.admissionId != admission.id) {
      return;
    }
    setState(() => _opening = true);
    bool entered = false;
    try {
      final before = await _authorized();
      if (!_valid(generation, revision) ||
          before.role.trim().toLowerCase() != 'nurse' ||
          selected.clinicId != before.clinicContextId) {
        throw const EmarReadSessionException();
      }
      final active = await widget.admissions.fetchCurrentAdmission(
        patientId: widget.patientId,
      );
      if (!_valid(generation, revision) ||
          active == null ||
          !active.isActive ||
          active.id != admission.id) {
        throw const FormatException('Current admission no longer matches.');
      }
      final fresh = await _emar.getAdministration(
        administrationId: selected.id,
        expectedPatientId: widget.patientId,
        expectedAdmissionId: admission.id,
      );
      if (!_valid(generation, revision) ||
          !fresh.isPending ||
          !_sameClinical(selected, fresh)) {
        throw const FormatException('Administration context changed.');
      }
      final after = await _authorized();
      if (!_valid(generation, revision) || !_sameSession(before, after)) {
        throw const EmarReadSessionException();
      }
      if (!mounted) {
        return;
      }
      // A second verification screen in this route lifetime is not allowed.
      // Leave and re-enter from a fresh patient/admission after reconciliation.
      _verificationOpened = true;
      entered = true;
      await Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => EmarBarcodeVerificationScreen(
            key: ValueKey('p23f-s03c-verifier-${fresh.id}-$revision'),
            adapter: EmarBarcodeVerificationRemoteDataSource(
              apiClient: widget.admissions.apiClient,
              readSource: _emar,
              loadSession: widget.admissions.loadSession,
              sessionNavigationGuard: _guard,
            ),
            reviewed: fresh,
          ),
        ),
      );
    } on Object {
      if (_valid(generation, revision)) {
        setState(() {
          _message =
              'Medication context changed or verification was unavailable. '
              'Return to the current clinical record.';
        });
      }
    } finally {
      if (_valid(generation, revision)) {
        if (entered) {
          // Always re-read the admission and schedule after a verification
          // screen closes; never retain a potentially changed clinical row.
          _scheduleEpoch++;
          unawaited(_load());
        } else {
          setState(() => _opening = false);
        }
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final admission = _currentAdmission;
    if (!_available) {
      return const Scaffold(
        body: Center(
          key: ValueKey('p23f-s03c-unavailable'),
          child: Text('eMAR session changed. Reopen from the current patient.'),
        ),
      );
    }
    if (_loading) {
      return const Scaffold(
        body: Center(
          child: CircularProgressIndicator(key: ValueKey('p23f-s03c-loading')),
        ),
      );
    }
    if (admission == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('Medication administration (eMAR)')),
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text(
              _message ?? 'Medication schedule unavailable.',
              key: const ValueKey('p23f-s03c-no-admission'),
            ),
          ),
        ),
      );
    }
    return Stack(
      children: [
        EmarDueScheduleScreen(
          key: ValueKey('p23f-s03c-schedule-${admission.id}-$_scheduleEpoch'),
          dataSource: _emar,
          patientId: widget.patientId,
          admissionId: admission.id,
          onVerifyRequested: _nurse && !_verificationOpened
              ? _openVerification
              : null,
        ),
        if (_message != null)
          Positioned(
            bottom: 0,
            left: 0,
            right: 0,
            child: Material(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Text(
                  _message!,
                  key: const ValueKey('p23f-s03c-message'),
                ),
              ),
            ),
          ),
        if (_opening)
          const Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: LinearProgressIndicator(
              key: ValueKey('p23f-s03c-opening-verification'),
            ),
          ),
      ],
    );
  }
}
