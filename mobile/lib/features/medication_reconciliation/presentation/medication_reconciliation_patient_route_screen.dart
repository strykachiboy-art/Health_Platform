// P23G post-S06 closure: server-scoped patient entry and review/clinical actions.
// The ward API permits doctors and nurses to discover active admission context.
// Pharmacist discovery needs a separate supported admission-lookup contract.
import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/patient_admission_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_admission.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/models/medication_reconciliation.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/data/medication_reconciliation_finalization_remote_data_source.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/presentation/medication_reconciliation_review_screen.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/presentation/medication_reconciliation_decision_screen.dart';
import 'package:clinic_system_pro/features/medication_reconciliation/presentation/medication_reconciliation_finalization_screen.dart';

class MedicationReconciliationPatientRouteScreen extends StatefulWidget {
  const MedicationReconciliationPatientRouteScreen({
    super.key,
    required this.admissions,
    required this.patientId,
  }) : assert(patientId > 0);

  final PatientAdmissionRemoteDataSource admissions;
  final int patientId;

  @override
  State<MedicationReconciliationPatientRouteScreen> createState() =>
      _MedicationReconciliationPatientRouteScreenState();
}

class _MedicationReconciliationPatientRouteScreenState
    extends State<MedicationReconciliationPatientRouteScreen> {
  late MedicationReconciliationReadRemoteDataSource _read;
  PatientAdmission? _admission;
  MedicationReconciliationPage? _page;
  AuthTokenResponse? _session;
  bool _loading = false;
  String? _error;
  int _generation = 0;

  bool get _canWrite => _session?.role.trim().toLowerCase() == 'doctor';

  bool _current(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.admissions.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.admissions.sessionNavigationGuard.revision == revision;

  @override
  void initState() {
    super.initState();
    _bind();
    widget.admissions.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  void _bind() {
    _read = MedicationReconciliationReadRemoteDataSource(
      apiClient: widget.admissions.apiClient,
      loadSession: widget.admissions.loadSession,
      sessionNavigationGuard: widget.admissions.sessionNavigationGuard,
    );
  }

  @override
  void didUpdateWidget(
    covariant MedicationReconciliationPatientRouteScreen oldWidget,
  ) {
    super.didUpdateWidget(oldWidget);
    if (widget.patientId == oldWidget.patientId &&
        identical(widget.admissions, oldWidget.admissions)) {
      return;
    }
    oldWidget.admissions.sessionNavigationGuard.removeListener(_sessionChanged);
    widget.admissions.sessionNavigationGuard.addListener(_sessionChanged);
    _generation++;
    _clear();
    _bind();
    unawaited(_load());
  }

  @override
  void dispose() {
    _generation++;
    widget.admissions.sessionNavigationGuard.removeListener(_sessionChanged);
    super.dispose();
  }

  void _clear() {
    _admission = null;
    _page = null;
    _session = null;
    _error = null;
    _loading = false;
  }

  void _sessionChanged() {
    if (!mounted) return;
    _generation++;
    setState(_clear);
    if (widget.admissions.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  static bool _sameSession(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> _sessionFor(int revision) async {
    final guard = widget.admissions.sessionNavigationGuard;
    if (!guard.canAccessProtectedRoutes || guard.revision != revision) {
      throw StateError('Session changed.');
    }
    final state = await widget.admissions.loadSession();
    final session = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.userId <= 0 ||
        session.clinicContextId == null ||
        session.clinicContextId! <= 0 ||
        session.accessToken.isEmpty ||
        session.accessToken.trim() != session.accessToken ||
        !const <String>{
          'doctor',
          'nurse',
        }.contains(session.role.trim().toLowerCase()) ||
        !guard.canAccessProtectedRoutes ||
        guard.revision != revision) {
      throw StateError('Medication review session unavailable.');
    }
    return session;
  }

  Future<void> _load() async {
    final revision = widget.admissions.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _admission = null;
      _page = null;
      _session = null;
      _error = null;
      _loading = true;
    });
    try {
      final before = await _sessionFor(revision);
      final admission = await widget.admissions.fetchCurrentAdmission(
        patientId: widget.patientId,
      );
      if (!_current(generation, revision)) return;
      if (admission == null) {
        throw StateError('No active admission.');
      }
      if (admission.patientId != widget.patientId || !admission.isActive) {
        throw const FormatException('Admission identity mismatch.');
      }
      final page = await _read.listForAdmission(
        admissionId: admission.id,
        expectedPatientId: widget.patientId,
        page: 1,
        perPage: 50,
      );
      if (!_current(generation, revision)) return;
      final after = await _sessionFor(revision);
      if (!_sameSession(before, after)) {
        throw StateError('Session changed.');
      }
      setState(() {
        _admission = admission;
        _page = page;
        _session = after;
        _loading = false;
      });
    } on Object {
      if (!_current(generation, revision)) return;
      setState(() {
        _admission = null;
        _page = null;
        _session = null;
        _loading = false;
        _error =
            'Medication reconciliation context could not be verified. '
            'Check the current admission, permissions and connection.';
      });
    }
  }

  Future<bool> _authorizedForWrite() async {
    if (!_canWrite || _loading || _admission == null || _page == null) {
      return false;
    }
    final revision = widget.admissions.sessionNavigationGuard.revision;
    try {
      final now = await _sessionFor(revision);
      return mounted &&
          widget.admissions.sessionNavigationGuard.revision == revision &&
          now.role.trim().toLowerCase() == 'doctor' &&
          _session != null &&
          _sameSession(now, _session!);
    } on Object {
      return false;
    }
  }

  Future<void> _review() async {
    final admission = _admission;
    if (admission == null || _loading) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => MedicationReconciliationReviewScreen(
          dataSource: _read,
          patientId: widget.patientId,
          admissionId: admission.id,
          onEditDraftRequested: _canWrite ? _editDraft : null,
          onFinalizationRequested: _canWrite ? _finalizeOrAmend : null,
        ),
      ),
    );
    if (mounted) unawaited(_load());
  }

  Future<void> _editDraft(MedicationReconciliationRecord? record) async {
    if (!await _authorizedForWrite()) return;
    final admission = _admission;
    if (admission == null ||
        (record != null &&
            (record.patientId != widget.patientId ||
                record.admissionId != admission.id ||
                record.status != MedicationReconciliationRecordStatus.draft))) {
      return;
    }
    if (!mounted) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => MedicationReconciliationDecisionScreen(
          readSource: _read,
          mutationSource: MedicationReconciliationMutationRemoteDataSource(
            apiClient: widget.admissions.apiClient,
            loadSession: widget.admissions.loadSession,
            sessionNavigationGuard: widget.admissions.sessionNavigationGuard,
          ),
          patientId: widget.patientId,
          admissionId: admission.id,
          event: record?.event ?? MedicationReconciliationEvent.admission,
          wardTransferId: record?.wardTransferId,
          reconciliationId: record?.id,
        ),
      ),
    );
    if (mounted) unawaited(_load());
  }

  Future<void> _finalizeOrAmend(MedicationReconciliationRecord record) async {
    if (!await _authorizedForWrite()) return;
    final admission = _admission;
    if (admission == null ||
        record.patientId != widget.patientId ||
        record.admissionId != admission.id) {
      return;
    }
    if (!mounted) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => MedicationReconciliationFinalizationScreen(
          source: MedRecS04FinalizationRemoteDataSource(
            transport: MedRecS04ApiTransport(widget.admissions.apiClient),
            loadSession: widget.admissions.loadSession,
            sessionNavigationGuard: widget.admissions.sessionNavigationGuard,
          ),
          reconciliationId: record.id,
          clinicId: record.clinicId,
          patientId: widget.patientId,
          admissionId: admission.id,
        ),
      ),
    );
    if (mounted) unawaited(_load());
  }

  @override
  Widget build(BuildContext context) {
    final admission = _admission;
    final page = _page;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Medication reconciliation'),
        actions: [
          IconButton(
            key: const ValueKey('p23g-route-refresh'),
            tooltip: 'Refresh medication reconciliation',
            icon: const Icon(Icons.refresh),
            onPressed: _loading ? null : () => unawaited(_load()),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Text(
                  _error!,
                  key: const ValueKey('p23g-route-unavailable'),
                  textAlign: TextAlign.center,
                ),
              ),
            )
          : admission == null || page == null
          ? const Center(child: Text('Admission not confirmed.'))
          : ListView(
              key: const ValueKey('p23g-route-workspace'),
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Patient #${widget.patientId} · Admission #${admission.id}',
                ),
                const Text(
                  'Server records only. Medication changes and clinical '
                  'finalization require authorized staff confirmation.',
                ),
                const SizedBox(height: 12),
                OutlinedButton(
                  key: const ValueKey('p23g-route-review'),
                  onPressed: () => unawaited(_review()),
                  child: const Text('Review all reconciliation records'),
                ),
                if (_canWrite &&
                    !page.items.any(
                      (record) =>
                          record.event ==
                          MedicationReconciliationEvent.admission,
                    ))
                  OutlinedButton(
                    key: const ValueKey('p23g-route-create-draft'),
                    onPressed: () => unawaited(_editDraft(null)),
                    child: const Text('Create admission medication draft'),
                  ),
                if (page.items.isEmpty)
                  const Text(
                    'No medication reconciliation records for this admission.',
                  ),
                for (final record in page.items)
                  Card(
                    key: ValueKey('p23g-route-record-${record.id}'),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        ListTile(
                          title: Text('${record.event.name} · #${record.id}'),
                          subtitle: Text(
                            'Server status: ${record.status.name} · Version ${record.version}',
                          ),
                        ),
                        if (_canWrite &&
                            record.status ==
                                MedicationReconciliationRecordStatus.draft)
                          TextButton(
                            key: ValueKey('p23g-route-edit-${record.id}'),
                            onPressed: () => unawaited(_editDraft(record)),
                            child: const Text('Edit draft decisions'),
                          ),
                        if (_canWrite)
                          TextButton(
                            key: ValueKey('p23g-route-finalize-${record.id}'),
                            onPressed: () =>
                                unawaited(_finalizeOrAmend(record)),
                            child: const Text(
                              'Finalize or amend with server review',
                            ),
                          ),
                      ],
                    ),
                  ),
                if (page.hasNext)
                  const Text(
                    'More records exist. Open Review all for server pagination.',
                  ),
              ],
            ),
    );
  }
}
