import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_profile_edit_screen.dart';

class PatientDetailScreen extends StatefulWidget {
  const PatientDetailScreen({
    super.key,
    required this.dataSource,
    required this.bearerToken,
    required this.patientId,
  });

  final PatientRemoteDataSource dataSource;
  final String bearerToken;
  final int patientId;

  @override
  State<PatientDetailScreen> createState() => _PatientDetailScreenState();
}

class _PatientDetailScreenState extends State<PatientDetailScreen> {
  Patient? _patient;
  Object? _lastError;
  String? _errorMessage;
  bool _loading = true;
  int _requestGeneration = 0;
  bool _statusUpdating = false;
  bool _statusOutcomeUnknown = false;
  bool _statusPermissionDenied = false;
  bool _statusAuthenticationRequired = false;
  String? _statusMessage;

  @override
  void initState() {
    super.initState();
    unawaited(_loadPatient());
  }

  Future<void> _loadPatient() async {
    final generation = ++_requestGeneration;
    setState(() {
      _loading = true;
      _lastError = null;
      _errorMessage = null;
    });

    try {
      final patient = await widget.dataSource.fetchPatient(
        bearerToken: widget.bearerToken,
        patientId: widget.patientId,
      );
      if (!mounted || generation != _requestGeneration) return;
      setState(() {
        _patient = patient;
        _loading = false;
      });
    } catch (error) {
      if (!mounted || generation != _requestGeneration) return;
      setState(() {
        _lastError = error;
        _errorMessage = _messageFor(error);
        _loading = false;
        if (error is ApiException &&
            const <int>{401, 403, 404}.contains(error.statusCode)) {
          // Do not keep showing a snapshot after access is rejected or
          // the backend reports that this patient is no longer available.
          _patient = null;
        }
      });
    }
  }

  String _messageFor(Object error) {
    if (widget.bearerToken.trim().isEmpty) {
      return 'No saved access token was provided. Return to sign in and establish a session.';
    }
    if (widget.patientId <= 0) {
      return 'This patient address is invalid.';
    }
    if (error is ApiException) {
      if (error.statusCode == 401) {
        return 'The backend rejected this session (HTTP 401). Return to sign in, sign out, and authenticate again.';
      }
      if (error.statusCode == 403) {
        return 'Your account is not permitted to view this patient.';
      }
      if (error.statusCode == 404) {
        return 'This patient could not be found.';
      }
      return switch (error.kind) {
        ApiFailureKind.timeout =>
          'The patient request timed out. Check the connection and retry.',
        ApiFailureKind.connection =>
          'Cannot connect to the backend. Check the API address and network.',
        ApiFailureKind.httpStatus =>
          'The backend returned HTTP ${error.statusCode}. Retry or contact support.',
        ApiFailureKind.invalidResponse =>
          'The backend returned an invalid response. Retry or contact support.',
      };
    }
    if (error is FormatException) {
      return 'The backend returned incomplete or malformed patient data.';
    }
    return 'Unable to load this patient. Check the connection and retry.';
  }

  bool get _needsSignIn =>
      widget.bearerToken.trim().isEmpty ||
      (_lastError is ApiException &&
          (_lastError as ApiException).statusCode == 401);

  /// True when the displayed patient snapshot has not been re-confirmed.
  bool get _patientSnapshotStale =>
      _patient != null && (_errorMessage != null || _statusOutcomeUnknown);

  Future<void> _openEditPatient() async {
    final patient = _patient;
    if (patient == null || _loading || _patientSnapshotStale) return;

    final updated = await Navigator.of(context).push<Patient>(
      MaterialPageRoute(
        builder: (_) => PatientProfileEditScreen(
          dataSource: widget.dataSource,
          bearerToken: widget.bearerToken,
          patient: patient,
        ),
      ),
    );

    if (!mounted) return;
    if (updated == null) {
      // Also reconcile after cancellation: the editor may have encountered an
      // uncertain network outcome before the user returned to this screen.
      await _loadPatient();
      return;
    }

    setState(() {
      _patient = updated;
      _lastError = null;
      _errorMessage = null;
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Patient details'),
        actions: [
          if (_patient != null && !_loading && !_statusOutcomeUnknown)
            IconButton(
              key: const ValueKey('patient-detail-refresh-button'),
              tooltip: 'Refresh patient details',
              icon: const Icon(Icons.refresh),
              onPressed: () => unawaited(_loadPatient()),
            ),
          if (_patient != null && !_loading && !_patientSnapshotStale)
            IconButton(
              key: const ValueKey('patient-detail-edit-button'),
              tooltip: 'Edit patient',
              icon: const Icon(Icons.edit),
              onPressed: () => unawaited(_openEditPatient()),
            ),
        ],
      ),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircularProgressIndicator(key: ValueKey('patient-detail-loading')),
            SizedBox(height: 12),
            Text('Loading patient details...'),
          ],
        ),
      );
    }

    final patient = _patient;
    if (patient == null) {
      return Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.warning_amber_rounded, size: 42),
              const SizedBox(height: 12),
              Text(
                _errorMessage ?? 'Patient details are unavailable.',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 12),
              if (_needsSignIn)
                ElevatedButton(
                  onPressed: () => context.go('/login'),
                  child: const Text('Return to sign in'),
                )
              else
                ElevatedButton.icon(
                  onPressed: () => unawaited(_loadPatient()),
                  icon: const Icon(Icons.refresh),
                  label: const Text('Retry'),
                ),
            ],
          ),
        ),
      );
    }

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (_patientSnapshotStale) _buildStalePatientNotice(),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    CircleAvatar(
                      radius: 28,
                      child: Text(patient.firstName[0].toUpperCase()),
                    ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            patient.fullName,
                            key: const ValueKey('patient-detail-name'),
                            style: Theme.of(context).textTheme.titleLarge,
                          ),
                          const SizedBox(height: 4),
                          Text('Patient no. ${patient.patientNumber}'),
                          const SizedBox(height: 8),
                          Chip(
                            label: Text(
                              patient.isActive ? 'Active' : 'Inactive',
                            ),
                            avatar: Icon(
                              patient.isActive
                                  ? Icons.check_circle_outline
                                  : Icons.pause_circle_outline,
                              size: 18,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        _statusManagementCard(patient),
        const SizedBox(height: 12),
        _section('Patient information', [
          _detail('Date of birth', _dateOnly(patient.dateOfBirth)),
          _detail('Gender', patient.gender),
          _detail('Blood type', patient.bloodType),
          _detail('Phone', patient.phone),
          _detail('Email', patient.email),
          _detail('Address', patient.address),
        ]),
        _section('Clinical information', [
          _detail('Allergies', patient.allergies),
          _detail('Chronic conditions', patient.chronicConditions),
        ]),
        _section('Identifiers', [
          _detail('Emirates ID', patient.emiratesId),
          _detail('UMRN', patient.umrn),
        ]),
      ],
    );
  }

  Widget _buildStalePatientNotice() {
    final colors = Theme.of(context).colorScheme;

    return Card(
      key: const ValueKey('patient-detail-stale-data-notice'),
      color: colors.errorContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Last confirmed patient details may be out of date.',
              style: Theme.of(context).textTheme.titleMedium?.copyWith(
                    color: colors.onErrorContainer,
                  ),
            ),
            if (_errorMessage != null) ...[
              const SizedBox(height: 8),
              Text(
                _errorMessage!,
                style: TextStyle(color: colors.onErrorContainer),
              ),
            ],
            const SizedBox(height: 8),
            Text(
              'The latest request did not confirm the current record. '
              'Refresh the patient details before making changes.',
              style: TextStyle(color: colors.onErrorContainer),
            ),
          ],
        ),
      ),
    );
  }

  Widget _statusManagementCard(Patient patient) {
    final isActive = patient.isActive;

    return Card(
      key: const ValueKey('patient-status-management-card'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Manage patient status',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 6),
            Text(
              isActive
                  ? 'This patient is active and appears in active-only lists.'
                  : 'This patient is inactive and is excluded from active-only lists.',
            ),
            if (_statusMessage != null) ...[
              const SizedBox(height: 10),
              Text(
                _statusMessage!,
                key: const ValueKey('patient-status-message'),
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ],
            const SizedBox(height: 12),
            if (_statusAuthenticationRequired)
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton(
                  key: const ValueKey('patient-status-sign-in-button'),
                  onPressed: () => context.go('/login'),
                  child: const Text('Return to sign in'),
                ),
              )
            else if (_statusPermissionDenied)
              const SizedBox.shrink()
            else if (_statusOutcomeUnknown)
              OutlinedButton.icon(
                key: const ValueKey('patient-status-reconcile-button'),
                onPressed: _statusUpdating
                    ? null
                    : () => unawaited(_reconcilePatientStatus()),
                icon: _statusUpdating
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.refresh),
                label: Text(
                  _statusUpdating ? 'Refreshing status...' : 'Refresh status',
                ),
              )
            else if (_patientSnapshotStale)
              const Text(
                'Status changes are disabled until patient details are '
                'confirmed current.',
                key: ValueKey('patient-status-stale-disabled-message'),
              )
            else
              FilledButton.tonalIcon(
                key: const ValueKey('patient-status-action-button'),
                onPressed: _statusUpdating
                    ? null
                    : () => unawaited(_confirmPatientStatusChange(patient)),
                icon: _statusUpdating
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : Icon(
                        isActive
                            ? Icons.person_off_outlined
                            : Icons.person_add_alt_1_outlined,
                      ),
                label: Text(
                  _statusUpdating
                      ? 'Updating status...'
                      : isActive
                      ? 'Deactivate patient'
                      : 'Activate patient',
                ),
              ),
          ],
        ),
      ),
    );
  }

  Future<void> _confirmPatientStatusChange(Patient patient) async {
    if (_statusUpdating ||
        _patientSnapshotStale ||
        _statusOutcomeUnknown ||
        _statusPermissionDenied ||
        _statusAuthenticationRequired) {
      return;
    }

    final desiredStatus = !patient.isActive;
    final actionLabel = desiredStatus
        ? 'Activate patient'
        : 'Deactivate patient';
    final explanation = desiredStatus
        ? 'This will mark the patient as active and include them in active-only patient lists.'
        : 'This will mark the patient as inactive and remove them from active-only patient lists. You can reactivate them later.';

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        key: const ValueKey('patient-status-confirmation-dialog'),
        title: Text(
          desiredStatus ? 'Activate this patient?' : 'Deactivate this patient?',
        ),
        content: Text(explanation),
        actions: [
          TextButton(
            key: const ValueKey('patient-status-cancel-button'),
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('patient-status-confirm-button'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: Text(actionLabel),
          ),
        ],
      ),
    );

    if (!mounted || confirmed != true) return;

    setState(() {
      _statusUpdating = true;
      _statusMessage = null;
    });

    try {
      final updated = await widget.dataSource.setPatientStatus(
        bearerToken: widget.bearerToken,
        patientId: patient.id,
        isActive: desiredStatus,
      );

      if (!mounted) return;
      if (updated.id != patient.id || updated.isActive != desiredStatus) {
        setState(() {
          _statusUpdating = false;
          _statusOutcomeUnknown = true;
          _statusMessage = 'The response did not confirm the requested status. Refresh status before trying again.';
        });
        return;
      }

      setState(() {
        _patient = updated;
        _statusUpdating = false;
        _statusOutcomeUnknown = false;
        _statusPermissionDenied = false;
        _statusAuthenticationRequired = false;
        _statusMessage = null;
        _lastError = null;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            desiredStatus
                ? 'Patient activated successfully.'
                : 'Patient deactivated successfully.',
          ),
        ),
      );
    } catch (error) {
      if (!mounted) return;
      final outcomeUnknown = _isStatusOutcomeUnknown(error);
      setState(() {
        _statusUpdating = false;
        _statusOutcomeUnknown = outcomeUnknown;
        _statusPermissionDenied =
            error is ApiException && error.statusCode == 403;
        _statusAuthenticationRequired =
            error is ApiException && error.statusCode == 401;
        _statusMessage = _statusFailureMessage(
          error,
          outcomeUnknown: outcomeUnknown,
        );
      });
    }
  }

  bool _isStatusOutcomeUnknown(Object error) {
    if (error is ApiException) {
      final status = error.statusCode;
      if (status == 408 || (status != null && status >= 500)) return true;
      if (status != null && status >= 400 && status < 500) return false;
      return error.kind == ApiFailureKind.timeout ||
          error.kind == ApiFailureKind.connection ||
          error.kind == ApiFailureKind.invalidResponse;
    }

    // A malformed success envelope, unexpected parse failure, or unclassified
    // exception cannot prove whether the server committed the status change.
    return true;
  }

  String _statusFailureMessage(Object error, {required bool outcomeUnknown}) {
    if (error is ApiException) {
      if (error.statusCode == 401) {
        return 'Your session is no longer authorized. Sign in again before changing patient status.';
      }
      if (error.statusCode == 403) {
        return 'Your account is not permitted to change patient status.';
      }
      if (error.statusCode == 404) {
        return 'This patient could not be found. Refresh the patient details.';
      }
      if (outcomeUnknown) {
        return 'We could not confirm whether the server applied this change. Refresh status before trying again.';
      }
      return 'The backend rejected the status change (HTTP ${error.statusCode}).';
    }

    if (outcomeUnknown) {
      return 'We could not confirm whether the server applied this change. Refresh status before trying again.';
    }
    return 'The status change could not be completed. Please try again.';
  }

  Future<void> _reconcilePatientStatus() async {
    if (_statusUpdating || !_statusOutcomeUnknown) return;
    setState(() => _statusUpdating = true);

    try {
      final current = await widget.dataSource.fetchPatient(
        bearerToken: widget.bearerToken,
        patientId: widget.patientId,
      );
      if (!mounted) return;

      setState(() {
        _patient = current;
        _statusUpdating = false;
        _statusOutcomeUnknown = false;
        _statusPermissionDenied = false;
        _statusAuthenticationRequired = false;
        _statusMessage = null;
        _lastError = null;
        _errorMessage = null;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Patient status refreshed from the server.'),
        ),
      );
    } catch (error) {
      if (!mounted) return;
      final needsSignIn = error is ApiException && error.statusCode == 401;
      setState(() {
        _statusUpdating = false;
        _statusOutcomeUnknown = !needsSignIn;
        _statusAuthenticationRequired = needsSignIn;
        if (needsSignIn) {
          _lastError = error;
          _errorMessage = _messageFor(error);
          _patient = null;
        }
        _statusMessage = needsSignIn
            ? 'Your session is no longer authorized. Sign in again to view current patient status.'
            : 'Could not refresh the status. The outcome remains unconfirmed; refresh again before attempting another change.';
      });
    }
  }

  Widget _section(String title, List<Widget?> details) {
    final visibleDetails = details.whereType<Widget>().toList(growable: false);
    if (visibleDetails.isEmpty) return const SizedBox.shrink();
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
              child: Text(
                title,
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ),
            ...visibleDetails,
          ],
        ),
      ),
    );
  }

  Widget? _detail(String label, String? value) {
    if (value == null || value.trim().isEmpty) return null;
    return ListTile(dense: true, title: Text(label), subtitle: Text(value));
  }

  String? _dateOnly(DateTime? value) {
    if (value == null) return null;
    return value.toIso8601String().split('T').first;
  }
}
