import 'dart:async';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patient_portal/data/patient_portal_remote_data_source.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

/// Patient-owned, read-only section views. Booking, cancellation, details and
/// messages stay in the original My care screen; this feature creates no writes.
enum PatientWorkspaceSection {
  appointments,
  prescriptions,
  billing,
  messages;

  static PatientWorkspaceSection? fromRoute(String value) =>
      switch (value) {
        'appointments' => PatientWorkspaceSection.appointments,
        'prescriptions' => PatientWorkspaceSection.prescriptions,
        'billing' => PatientWorkspaceSection.billing,
        'messages' => PatientWorkspaceSection.messages,
        _ => null,
      };

  String get title => switch (this) {
        PatientWorkspaceSection.appointments => 'My appointments',
        PatientWorkspaceSection.prescriptions => 'My prescriptions',
        PatientWorkspaceSection.billing => 'Billing & invoices',
        PatientWorkspaceSection.messages => 'My care messages',
      };
}

/// No patient-id or clinic-id input. The existing source fetches only the
/// server-enrolled patient's own records and rechecks the authenticated session.
class PatientSectionScreen extends StatefulWidget {
  const PatientSectionScreen({
    super.key,
    required this.section,
    required this.source,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final PatientWorkspaceSection section;
  final PatientPortalDataSource source;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  @override
  State<PatientSectionScreen> createState() => _PatientSectionScreenState();
}

class _PatientSectionScreenState extends State<PatientSectionScreen> {
  PatientPortalView? _view;
  AuthTokenResponse? _verified;
  bool _loading = true;
  String? _failure;
  int _generation = 0;
  int? _verifiedRevision;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard.addListener(_onSessionChanged);
    unawaited(_refresh());
  }

  @override
  void didUpdateWidget(covariant PatientSectionScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.sessionNavigationGuard, widget.sessionNavigationGuard)) {
      oldWidget.sessionNavigationGuard.removeListener(_onSessionChanged);
      widget.sessionNavigationGuard.addListener(_onSessionChanged);
    }
    if (widget.section != oldWidget.section ||
        !identical(widget.source, oldWidget.source) ||
        !identical(widget.loadSession, oldWidget.loadSession) ||
        !identical(widget.sessionNavigationGuard, oldWidget.sessionNavigationGuard)) {
      unawaited(_refresh());
    }
  }

  @override
  void dispose() {
    widget.sessionNavigationGuard.removeListener(_onSessionChanged);
    _generation++;
    _view = null;
    _verified = null;
    super.dispose();
  }

  void _onSessionChanged() {
    _generation++;
    if (!mounted) return;
    setState(() {
      _view = null;
      _verified = null;
      _verifiedRevision = null;
      _failure = 'Session changed. Patient records were cleared.';
      _loading = false;
    });
    if (widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_refresh());
    }
  }

  Future<AuthTokenResponse?> _patientSession() async {
    final state = await widget.loadSession();
    final session = state.session;
    if (state.disposition != AuthSessionDisposition.ready ||
        session == null ||
        session.role.toLowerCase() != 'patient' ||
        session.userId <= 0 ||
        session.accessToken.isEmpty ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      return null;
    }
    return session;
  }

  bool _current(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.sessionNavigationGuard.canAccessProtectedRoutes &&
      revision == widget.sessionNavigationGuard.revision;

  static bool _sameSession(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.role.toLowerCase() == b.role.toLowerCase() &&
      a.clinicContextId == b.clinicContextId &&
      a.tenantId == b.tenantId &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<void> _refresh() async {
    final generation = ++_generation;
    final revision = widget.sessionNavigationGuard.revision;
    if (!mounted) return;
    setState(() {
      _view = null;
      _verified = null;
      _verifiedRevision = null;
      _failure = null;
      _loading = true;
    });
    if (!widget.sessionNavigationGuard.canAccessProtectedRoutes) {
      if (mounted && generation == _generation) {
        setState(() {
          _loading = false;
          _failure = 'Sign in to access your patient services.';
        });
      }
      return;
    }

    try {
      final before = await _patientSession();
      if (before == null) throw StateError('Patient session unavailable');
      final result = await widget.source.load();
      final after = await _patientSession();
      if (!_current(generation, revision)) return;
      if (after == null || !_sameSession(before, after)) {
        throw StateError('Patient session changed');
      }
      setState(() {
        _view = result;
        _verified = after;
        _verifiedRevision = revision;
        _loading = false;
      });
    } on ApiException catch (error) {
      if (!_current(generation, revision)) return;
      final label = error.statusCode == 409 &&
              error.serverCode == 'PATIENT_ENROLLMENT_REQUIRED'
          ? 'Your Google or password account is signed in, but clinical enrollment is pending. Contact your clinic to verify and link your patient record.'
          : error.statusCode == 401
              ? 'Your session has expired. Sign in again.'
              : error.statusCode == 403
                  ? 'The clinic has not authorized these patient records.'
                  : 'Patient services are unavailable. Check your connection and enrollment, then refresh.';
      setState(() {
        _loading = false;
        _failure = label;
      });
    } on Object {
      if (!_current(generation, revision)) return;
      setState(() {
        _loading = false;
        _failure = 'Patient records could not be verified for this session.';
      });
    }
  }

  Future<void> _openFullCare() async {
    final verified = _verified;
    final revision = _verifiedRevision;
    if (_view == null || verified == null || revision == null ||
        !widget.sessionNavigationGuard.canAccessProtectedRoutes ||
        widget.sessionNavigationGuard.revision != revision) {
      return;
    }
    try {
      final now = await _patientSession();
      if (!mounted ||
          now == null ||
          !_sameSession(verified, now) ||
          widget.sessionNavigationGuard.revision != revision ||
          !widget.sessionNavigationGuard.canAccessProtectedRoutes) {
        return;
      }
      context.push<void>('/patient-portal');
    } on Object {
      // A failed or changed session never grants navigation.
    }
  }

  static String _safe(Object? input) {
    if (input is String && input.isNotEmpty && input.length <= 200) return input;
    if (input is int) return input.toString();
    return 'Not specified';
  }

  Widget _sectionBody(PatientPortalView view) {
    switch (widget.section) {
      case PatientWorkspaceSection.appointments:
        return _records(
          view.appointments,
          empty: 'No appointments are recorded for this patient.',
          tile: (item) => ListTile(
            leading: const Icon(Icons.event_note_outlined),
            title: Text('Appointment #${_safe(item['id'])}'),
            subtitle: Text(
              'Status: ${_safe(item['status'])}\n'
              '${_safe(item['scheduled_start'])} · ${_safe(item['appointment_type'])}',
            ),
            isThreeLine: true,
          ),
        );
      case PatientWorkspaceSection.prescriptions:
        return _records(
          view.prescriptions,
          empty: 'No prescriptions are recorded for this patient.',
          tile: (item) => ListTile(
            leading: const Icon(Icons.medication_outlined),
            title: Text('Prescription #${_safe(item['id'])}'),
            subtitle: Text('Status: ${_safe(item['status'])} · Issued: ${_safe(item['issued_at'])}'),
          ),
        );
      case PatientWorkspaceSection.billing:
        return _records(
          view.invoices,
          empty: 'No invoices are recorded for this patient.',
          tile: (item) => ListTile(
            leading: const Icon(Icons.receipt_long_outlined),
            title: Text(_safe(item['invoice_number'])),
            subtitle: Text(
              'Status: ${_safe(item['status'])} · Due: ${_safe(item['due_date'])}\n'
              'Total: ${_safe(item['total_amount'])} ${_safe(item['currency'])} · '
              'Paid: ${_safe(item['amount_paid'])}',
            ),
            isThreeLine: true,
          ),
        );
      case PatientWorkspaceSection.messages:
        return const Text(
          'Your existing My care screen contains the approved patient-message '
          'entry. Open it to access server-authorized conversations. '
          'This shortcut cannot reveal staff or other patients\' messages.',
        );
    }
  }

  Widget _records(
    List<Map<String, dynamic>> rows, {
    required String empty,
    required Widget Function(Map<String, dynamic>) tile,
  }) {
    if (rows.isEmpty) return Text(empty);
    return Column(
      children: [for (final item in rows) Card(child: tile(item))],
    );
  }

  @override
  Widget build(BuildContext context) {
    final view = _view;
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.section.title),
        actions: [
          IconButton(
            key: const ValueKey('s15-patient-section-refresh'),
            tooltip: 'Refresh patient records',
            onPressed: _loading ? null : () => unawaited(_refresh()),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : view == null
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Text(
                      _failure ?? 'Patient services unavailable.',
                      key: const ValueKey('s15-patient-section-unavailable'),
                      textAlign: TextAlign.center,
                    ),
                  ),
                )
              : ListView(
                  key: const ValueKey('s15-patient-section-content'),
                  padding: const EdgeInsets.all(20),
                  children: [
                    Text('Records for ${view.name}. Only backend-authorized patient information is displayed.'),
                    const SizedBox(height: 16),
                    _sectionBody(view),
                    const SizedBox(height: 12),
                    if (widget.section == PatientWorkspaceSection.billing)
                      const Text('Online payment is not enabled without a separately verified patient checkout contract.'),
                    if (widget.section == PatientWorkspaceSection.appointments)
                      const Text('Appointments are requested, not confirmed availability. Booking and cancellation use the existing server-confirmed My care workflow.'),
                    const SizedBox(height: 16),
                    if (widget.section == PatientWorkspaceSection.appointments ||
                        widget.section == PatientWorkspaceSection.messages ||
                        (widget.section == PatientWorkspaceSection.prescriptions &&
                            view.prescriptions.isNotEmpty) ||
                        (widget.section == PatientWorkspaceSection.billing &&
                            view.invoices.isNotEmpty))
                      FilledButton.icon(
                        key: const ValueKey('s15-open-full-my-care'),
                        onPressed: () => unawaited(_openFullCare()),
                        icon: const Icon(Icons.favorite_outline),
                        label: Text(switch (widget.section) {
                          PatientWorkspaceSection.appointments => 'Book or manage in My care',
                          PatientWorkspaceSection.prescriptions => 'Open prescription details in My care',
                          PatientWorkspaceSection.billing => 'Open invoice details in My care',
                          PatientWorkspaceSection.messages => 'Open My care messages',
                        }),
                      ),
                  ],
                ),
    );
  }
}
