// P23F.S03.B — capture patient and medication barcode, review, then verify ONCE.
// A passed verification is NOT an administration record or permission to dose.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:uuid/uuid.dart';

import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/emar/data/emar_barcode_verification_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/data/models/emar_barcode_verification.dart';
import 'package:clinic_system_pro/features/emar/data/models/medication_administration.dart';
import 'package:clinic_system_pro/features/emar/presentation/emar_camera_barcode_capture.dart';
import 'package:clinic_system_pro/features/emar/data/emar_medication_administration_remote_data_source.dart';
import 'package:clinic_system_pro/features/emar/presentation/emar_administration_confirmation_panel.dart';

class EmarBarcodeVerificationScreen extends StatefulWidget {
  const EmarBarcodeVerificationScreen({
    super.key,
    required this.adapter,
    required this.reviewed,
    this.capture = captureEmarBarcode,
    this.administrationAdapter,
  });

  final EmarBarcodeVerificationRemoteDataSource adapter;
  final EmarAdministration reviewed;
  final EmarBarcodeCapture capture;
  final EmarMedicationAdministrationRemoteDataSource? administrationAdapter;

  @override
  State<EmarBarcodeVerificationScreen> createState() =>
      _EmarBarcodeVerificationScreenState();
}

class _EmarBarcodeVerificationScreenState
    extends State<EmarBarcodeVerificationScreen> {
  int _generation = 0;
  bool _loading = true;
  bool _busy = false;
  bool _submitted = false;
  bool _needsReopen = false;
  EmarAdministration? _fresh;
  EmarCapturedBarcode? _patient;
  EmarCapturedBarcode? _medication;
  EmarBarcodeVerification? _result;
  String? _message;

  bool get _guardAvailable =>
      widget.adapter.sessionNavigationGuard.canAccessProtectedRoutes;
  bool get _available => _guardAvailable && !_needsReopen;
  bool _current(int generation, int revision) =>
      mounted &&
      _available &&
      generation == _generation &&
      widget.adapter.sessionNavigationGuard.revision == revision;

  @override
  void initState() {
    super.initState();
    widget.adapter.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_review());
  }

  @override
  void didUpdateWidget(covariant EmarBarcodeVerificationScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.adapter, widget.adapter) ||
        !identical(oldWidget.reviewed, widget.reviewed)) {
      oldWidget.adapter.sessionNavigationGuard.removeListener(_sessionChanged);
      widget.adapter.sessionNavigationGuard.addListener(_sessionChanged);
      _sessionChanged();
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.adapter.sessionNavigationGuard.removeListener(_sessionChanged);
    _patient = null;
    _medication = null;
    super.dispose();
  }

  void _sessionChanged() {
    _generation++;
    _needsReopen = true;
    _loading = false;
    _busy = false;
    _patient = null;
    _medication = null;
    _fresh = null;
    _result = null;
    _message = null;
    if (mounted) setState(() {});
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> _nurse() async {
    final state = await widget.adapter.loadSession();
    final session = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.userId <= 0 ||
        session.clinicContextId != widget.reviewed.clinicId ||
        session.role.trim().toLowerCase() != 'nurse' ||
        session.accessToken.trim().isEmpty ||
        session.accessToken != session.accessToken.trim()) {
      throw const EmarVerificationSessionUnavailable();
    }
    return session;
  }

  static bool _sameClinical(EmarAdministration a, EmarAdministration b) =>
      a.id == b.id &&
      a.clinicId == b.clinicId &&
      a.patientId == b.patientId &&
      a.admissionId == b.admissionId &&
      a.prescriptionItemId == b.prescriptionItemId &&
      a.status == b.status &&
      a.recordVersion == b.recordVersion &&
      a.sourceOrderVersion == b.sourceOrderVersion &&
      a.doseValue == b.doseValue &&
      a.doseUnit == b.doseUnit &&
      a.routeCode == b.routeCode &&
      a.scheduledAt == b.scheduledAt;

  Future<void> _review() async {
    final revision = widget.adapter.sessionNavigationGuard.revision;
    final generation = ++_generation;
    if (!mounted) return;
    setState(() {
      _loading = true;
      _fresh = null;
      _message = null;
    });
    try {
      if (!_available ||
          !identical(
            widget.adapter.readSource.apiClient,
            widget.adapter.apiClient,
          ) ||
          !identical(
            widget.adapter.readSource.sessionNavigationGuard,
            widget.adapter.sessionNavigationGuard,
          )) {
        throw const EmarVerificationSessionUnavailable();
      }
      final before = await _nurse();
      if (!_current(generation, revision)) return;
      final latest = await widget.adapter.readSource.getAdministration(
        administrationId: widget.reviewed.id,
        expectedPatientId: widget.reviewed.patientId,
        expectedAdmissionId: widget.reviewed.admissionId,
      );
      if (!_current(generation, revision)) return;
      final after = await _nurse();
      if (!_same(before, after) ||
          !_sameClinical(widget.reviewed, latest) ||
          !latest.isPending) {
        throw const EmarVerificationPreflightChanged();
      }
      if (!_current(generation, revision)) return;
      setState(() {
        _fresh = latest;
        _loading = false;
      });
    } on Object {
      if (!_current(generation, revision)) return;
      setState(() {
        _loading = false;
        _message =
            'Nurse authorization or medication schedule changed. '
            'Reopen from a fresh patient and administration record.';
      });
    }
  }

  Future<void> _scan(EmarScanKind kind) async {
    if (!_available ||
        _loading ||
        _busy ||
        _submitted ||
        _fresh == null ||
        (kind == EmarScanKind.medication && _patient == null)) {
      return;
    }
    final revision = widget.adapter.sessionNavigationGuard.revision;
    final generation = _generation;
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      final result = await widget.capture(
        context,
        kind,
        widget.adapter.sessionNavigationGuard,
      );
      if (!_current(generation, revision)) return;
      if (result == null) {
        setState(() => _busy = false);
        return;
      }
      if (result.value.isEmpty ||
          result.value.length > 255 ||
          result.value.trim() != result.value ||
          !result.capturedAtUtc.isUtc) {
        throw const FormatException('Invalid camera barcode.');
      }
      setState(() {
        _busy = false;
        if (kind == EmarScanKind.patient) {
          _patient = result;
          _medication = null; // Patient rescanning invalidates medication scan.
        } else {
          _medication = result;
        }
      });
    } on Object {
      if (!_current(generation, revision)) return;
      setState(() {
        _busy = false;
        _message = 'Barcode capture failed. No server action was sent.';
      });
    }
  }

  Future<void> _verifyOnce() async {
    if (!_available ||
        _loading ||
        _busy ||
        _submitted ||
        _fresh == null ||
        _patient == null ||
        _medication == null) {
      return;
    }
    final revision = widget.adapter.sessionNavigationGuard.revision;
    final generation = _generation;
    final patient = _patient!;
    final medication = _medication!;
    // Terminal UI lock is acquired before any network action. NEVER auto retry.
    setState(() {
      _submitted = true;
      _busy = true;
      _message = null;
    });
    try {
      final event = await widget.adapter.verifyOnce(
        reviewed: _fresh!,
        patientBarcode: patient.value,
        medicationBarcode: medication.value,
        scanDevice: 'flutter_mobile_scanner_camera',
        scanTimestamp: medication.capturedAtUtc,
        operationKey: const Uuid().v4(),
      );
      if (!_current(generation, revision)) return;
      setState(() {
        _busy = false;
        _patient = null;
        _medication = null;
        _result = event;
        _message = event.verified
            ? 'Server barcode verification confirmed. Medication NOT '
                  'administered. Administration requires a separate authorized action.'
            : 'Barcode verification FAILED (${event.failureCode?.name ?? 'unknown'}). '
                  'Do not administer. Reopen after clinical review.';
      });
    } on Object {
      if (!_current(generation, revision)) return;
      setState(() {
        _busy = false;
        _patient = null;
        _medication = null;
        _result = null;
        _message =
            'Verification outcome unconfirmed or access changed. '
            'Do not repeat the POST. Reconcile using the server record.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final record = _fresh;
    final canScan =
        _available && !_loading && !_busy && !_submitted && record != null;
    final canVerify = canScan && _patient != null && _medication != null;
    return Scaffold(
      appBar: AppBar(title: const Text('Medication barcode verification')),
      body: !_available
          ? const Center(
              key: ValueKey('p23f-s03b-unavailable'),
              child: Padding(
                padding: EdgeInsets.all(20),
                child: Text(
                  'Session changed. Close and reopen from the current '
                  'authenticated patient and admission.',
                ),
              ),
            )
          : ListView(
              key: const ValueKey('p23f-s03b-workspace'),
              padding: const EdgeInsets.all(16),
              children: [
                const Text(
                  'Camera scan and barcode verification. '
                  'Recording a dose requires separate nurse confirmation.',
                ),
                if (_loading)
                  const LinearProgressIndicator(
                    key: ValueKey('p23f-s03b-loading'),
                  ),
                if (record != null) ...[
                  Text(
                    'Patient #${record.patientId} · Admission #${record.admissionId}',
                    key: const ValueKey('p23f-s03b-context'),
                  ),
                  Text(
                    'Administration #${record.id} · Server status: ${record.status.name}',
                  ),
                  Text(
                    'Prescription item #${record.prescriptionItemId} · Source order version ${record.sourceOrderVersion ?? 'not available'}',
                  ),
                  Text(
                    'Dose: ${record.doseValue ?? 'not provided'} '
                    '${record.doseUnit ?? ''} · Route: ${record.routeCode ?? 'not provided'}',
                  ),
                  const SizedBox(height: 12),
                  ElevatedButton.icon(
                    key: const ValueKey('p23f-s03b-scan-patient'),
                    onPressed: canScan
                        ? () => unawaited(_scan(EmarScanKind.patient))
                        : null,
                    icon: const Icon(Icons.qr_code_scanner),
                    label: const Text('Capture patient barcode'),
                  ),
                  Text(
                    _patient == null
                        ? 'Patient barcode not captured.'
                        : 'Patient barcode captured (value hidden).',
                    key: const ValueKey('p23f-s03b-patient-state'),
                  ),
                  ElevatedButton.icon(
                    key: const ValueKey('p23f-s03b-scan-medication'),
                    onPressed: canScan && _patient != null
                        ? () => unawaited(_scan(EmarScanKind.medication))
                        : null,
                    icon: const Icon(Icons.medication_outlined),
                    label: const Text('Capture medication barcode'),
                  ),
                  Text(
                    _medication == null
                        ? 'Medication barcode not captured.'
                        : 'Medication barcode captured (value hidden).',
                    key: const ValueKey('p23f-s03b-medication-state'),
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'Review the patient, medication order, dose and route '
                    'against the bedside record. Verification is sent once; '
                    'it does not administer medication.',
                  ),
                  FilledButton(
                    key: const ValueKey('p23f-s03b-verify-once'),
                    onPressed: canVerify
                        ? () => unawaited(_verifyOnce())
                        : null,
                    child: const Text('Verify scanned barcodes once'),
                  ),
                ],
                if (_result != null)
                  Text(
                    'Verification event #${_result!.id}',
                    key: const ValueKey('p23f-s03b-event'),
                  ),
                if (_result != null && _result!.verified &&
                    _fresh != null && widget.administrationAdapter != null)
                  EmarAdministrationConfirmationPanel(
                    key: ValueKey('p23f-s04-panel-${_result!.id}'),
                    adapter: widget.administrationAdapter!,
                    reviewed: _fresh!,
                    verification: _result!,
                    onConfirmed: (administration) {
                      if (mounted && _available) {
                        setState(() {
                          _message = 'Server confirmed medication administration '
                              '#${administration.id}. Reopen for another dose.';
                        });
                      }
                    },
                  ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('p23f-s03b-message')),
                if (_submitted)
                  const Text(
                    'This verification attempt is closed. '
                    'Reopen after authoritative reconciliation.',
                  ),
              ],
            ),
    );
  }
}
