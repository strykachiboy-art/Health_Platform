import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_remote_data_source.dart';
import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';

class EmergencyAccessRequestScreen extends StatefulWidget {
  const EmergencyAccessRequestScreen({super.key, required this.dataSource});

  final EmergencyAccessRemoteDataSource dataSource;

  @override
  State<EmergencyAccessRequestScreen> createState() =>
      _EmergencyAccessRequestScreenState();
}

class _EmergencyAccessRequestScreenState
    extends State<EmergencyAccessRequestScreen> {
  final _formKey = GlobalKey<FormState>();

  final _patientId = TextEditingController();
  final _reason = TextEditingController();
  final _purpose = TextEditingController();
  final _scope = TextEditingController();

  bool _submitting = false;
  bool _outcomeUnknown = false;

  EmergencyAccessGrant? _createdRequest;
  String? _formMessage;
  Map<String, String> _serverErrors = <String, String>{};

  @override
  void dispose() {
    _patientId.dispose();
    _reason.dispose();
    _purpose.dispose();
    _scope.dispose();
    super.dispose();
  }

  String? _requiredText(
    String? value, {
    required String label,
    required int maxLength,
  }) {
    final normalized = value?.trim() ?? '';

    if (normalized.isEmpty) {
      return '$label is required.';
    }

    if (normalized.length > maxLength) {
      return '$label must be at most $maxLength characters.';
    }

    return null;
  }

  String? _validatePatientId(String? value) {
    final normalized = value?.trim() ?? '';
    final patientId = int.tryParse(normalized);

    if (patientId == null || patientId <= 0) {
      return 'Patient ID must be a positive integer.';
    }

    return null;
  }

  String? _validateScope(String? value) {
    final normalized = value?.trim() ?? '';

    if (normalized.isEmpty) {
      return 'At least one scope is required.';
    }

    final scopes = _scopeValues();

    if (scopes.isEmpty) {
      return 'At least one scope is required.';
    }

    if (scopes.length > 50) {
      return 'Scope cannot contain more than 50 items.';
    }

    if (scopes.any((item) => item.length > 150)) {
      return 'Each scope item must be at most 150 characters.';
    }

    return null;
  }

  List<String> _scopeValues() {
    return _scope.text
        .split(RegExp(r'\r?\n'))
        .map((item) => item.trim())
        .where((item) => item.isNotEmpty)
        .toList(growable: false);
  }

  void _clearServerError(String field) {
    if (!_serverErrors.containsKey(field)) return;

    setState(() {
      _serverErrors.remove(field);
    });
  }

  Widget _serverError(String field) {
    final message = _serverErrors[field];

    if (message == null) {
      return const SizedBox.shrink();
    }

    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Text(
        message,
        key: ValueKey('emergency-access-request-server-error-$field'),
        style: TextStyle(color: Theme.of(context).colorScheme.error),
      ),
    );
  }

  Future<void> _submit() async {
    if (_submitting || _outcomeUnknown) return;

    final form = _formKey.currentState;
    if (form == null || !form.validate()) return;

    final patientId = int.parse(_patientId.text.trim());

    setState(() {
      _submitting = true;
      _formMessage = null;
      _serverErrors = <String, String>{};
    });

    try {
      final request = await widget.dataSource.requestAccess(
        patientId: patientId,
        reason: _reason.text,
        purpose: _purpose.text,
        scope: _scopeValues(),
      );

      if (!mounted) return;

      setState(() {
        _createdRequest = request;
        _formMessage = null;
      });
    } on AuthenticatedSessionUnavailableException catch (error) {
      if (!mounted) return;

      setState(() {
        _outcomeUnknown = false;
        _formMessage =
            error.failure == AuthenticatedSessionAccessFailure.recoveryRequired
            ? 'Your saved session requires recovery before requesting emergency access.'
            : 'Sign in before requesting emergency access.';
      });
    } on ArgumentError {
      if (!mounted) return;

      setState(() {
        _outcomeUnknown = false;
        _formMessage =
            'Review the patient ID and emergency access scope, then try again.';
      });
    } on ApiException catch (error) {
      if (!mounted) return;

      final status = error.statusCode;

      if (status == 422) {
        setState(() {
          _serverErrors = Map<String, String>.from(error.validationErrors);
          _formMessage = _serverErrors.isEmpty
              ? 'The backend rejected the emergency access request. Review the form and try again.'
              : 'Please correct the highlighted fields and try again.';
        });
      } else if (status == 401) {
        setState(() {
          _formMessage = 'Your session is no longer accepted. Sign in again before requesting emergency access.';
        });
      } else if (status == 403) {
        setState(() {
          _formMessage =
              'Your account is not permitted to request emergency access.';
        });
      } else if (error.kind == ApiFailureKind.timeout ||
          error.kind == ApiFailureKind.connection ||
          error.kind == ApiFailureKind.invalidResponse ||
          (status != null && status >= 500)) {
        _markUnknownOutcome();
      } else {
        setState(() {
          _formMessage = status == null
              ? 'The emergency access request could not be completed.'
              : 'The backend rejected the emergency access request (HTTP $status).';
        });
      }
    } on FormatException {
      if (!mounted) return;
      _markUnknownOutcome();
    } catch (_) {
      if (!mounted) return;
      _markUnknownOutcome();
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  void _markUnknownOutcome() {
    setState(() {
      _outcomeUnknown = true;
      _formMessage =
          'We could not confirm whether the emergency access request was created. '
          'Do not submit another request until the outcome has been reconciled.';
    });
  }

  @override
  Widget build(BuildContext context) {
    final request = _createdRequest;

    return Scaffold(
      appBar: AppBar(title: const Text('Emergency access')),
      body: request == null ? _buildForm() : _buildSuccess(request),
    );
  }

  Widget _buildForm() {
    return SafeArea(
      child: Form(
        key: _formKey,
        child: SingleChildScrollView(
          key: const ValueKey('emergency-access-request-scroll'),
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'Request emergency access',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 8),
              const Text(
                'Emergency access is temporary, audited, patient-specific, '
                'and limited to the exact scopes requested.',
              ),
              const SizedBox(height: 20),

              TextFormField(
                key: const ValueKey('emergency-access-request-patient-id'),
                controller: _patientId,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'Patient ID',
                  border: OutlineInputBorder(),
                ),
                validator: _validatePatientId,
                onChanged: (_) => _clearServerError('patient_id'),
              ),
              _serverError('patient_id'),

              const SizedBox(height: 16),

              TextFormField(
                key: const ValueKey('emergency-access-request-reason'),
                controller: _reason,
                maxLength: 500,
                maxLines: 3,
                decoration: const InputDecoration(
                  labelText: 'Emergency reason',
                  border: OutlineInputBorder(),
                ),
                validator: (value) => _requiredText(
                  value,
                  label: 'Emergency reason',
                  maxLength: 500,
                ),
                onChanged: (_) => _clearServerError('reason'),
              ),
              _serverError('reason'),

              const SizedBox(height: 16),

              TextFormField(
                key: const ValueKey('emergency-access-request-purpose'),
                controller: _purpose,
                maxLength: 255,
                maxLines: 2,
                decoration: const InputDecoration(
                  labelText: 'Purpose',
                  border: OutlineInputBorder(),
                ),
                validator: (value) =>
                    _requiredText(value, label: 'Purpose', maxLength: 255),
                onChanged: (_) => _clearServerError('purpose'),
              ),
              _serverError('purpose'),

              const SizedBox(height: 16),

              TextFormField(
                key: const ValueKey('emergency-access-request-scope'),
                controller: _scope,
                minLines: 4,
                maxLines: 8,
                decoration: const InputDecoration(
                  labelText: 'Requested scopes',
                  hintText:
                      'One scope per line\n'
                      'patient:read\n'
                      'nursing_note:42:read',
                  border: OutlineInputBorder(),
                ),
                validator: _validateScope,
                onChanged: (_) => _clearServerError('scope'),
              ),
              _serverError('scope'),

              const SizedBox(height: 12),

              if (_formMessage != null)
                Text(
                  _formMessage!,
                  key: const ValueKey('emergency-access-request-form-message'),
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),

              if (_outcomeUnknown) ...[
                const SizedBox(height: 12),
                Text(
                  'Outcome reconciliation is required before another request '
                  'is submitted.',
                  key: const ValueKey(
                    'emergency-access-request-reconcile-warning',
                  ),
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.error,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ],

              const SizedBox(height: 20),

              if (_outcomeUnknown)
                OutlinedButton(
                  key: const ValueKey('emergency-access-request-return-button'),
                  onPressed: () => Navigator.of(context).maybePop(),
                  child: const Text('Return'),
                )
              else
                ElevatedButton.icon(
                  key: const ValueKey('emergency-access-request-submit-button'),
                  onPressed: _submitting ? null : _submit,
                  icon: _submitting
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.emergency),
                  label: Text(
                    _submitting ? 'Submitting...' : 'Request emergency access',
                  ),
                ),

              const SizedBox(height: 32),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildSuccess(EmergencyAccessGrant request) {
    return SafeArea(
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            key: const ValueKey('emergency-access-request-success'),
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.check_circle_outline, size: 56),
              const SizedBox(height: 16),
              Text(
                'Emergency access requested',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 12),
              Text('Request ID: ${request.id}'),
              Text('Patient ID: ${request.patientId}'),
              Text('Status: ${request.status.name}'),
              const SizedBox(height: 16),
              const Text(
                'Access is not active unless the backend records the request '
                'as active.',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 20),
              ElevatedButton(
                key: const ValueKey(
                  'emergency-access-request-view-status-button',
                ),
                onPressed: () {
                  context.push('/emergency-access/requests/${request.id}');
                },
                child: const Text('View request status'),
              ),
              const SizedBox(height: 8),
              OutlinedButton(
                key: const ValueKey('emergency-access-request-done-button'),
                onPressed: () => Navigator.of(context).maybePop(),
                child: const Text('Done'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
