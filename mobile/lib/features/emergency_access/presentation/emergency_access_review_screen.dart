import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_remote_data_source.dart';
import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';

class EmergencyAccessReviewScreen extends StatefulWidget {
  const EmergencyAccessReviewScreen({
    super.key,
    required this.dataSource,
    required this.emergencyAccessId,
    this.emergencyAccessContext,
  });

  final EmergencyAccessRemoteDataSource dataSource;
  final int emergencyAccessId;
  final EmergencyAccessContext? emergencyAccessContext;

  @override
  State<EmergencyAccessReviewScreen> createState() =>
      _EmergencyAccessReviewScreenState();
}

class _EmergencyAccessReviewScreenState
    extends State<EmergencyAccessReviewScreen> {
  final _durationMinutes = TextEditingController(text: '30');
  final _reviewNotes = TextEditingController();
  final _revokeReason = TextEditingController();

  EmergencyAccessGrant? _request;

  bool _loading = true;
  bool _writing = false;
  bool _writeOutcomeUnknown = false;
  bool _refreshRequired = false;

  String? _message;

  bool get _writesBlocked =>
      _writing || _writeOutcomeUnknown || _refreshRequired;

  @override
  void initState() {
    super.initState();
    unawaited(_loadRequest(initial: true));
  }

  @override
  void dispose() {
    _durationMinutes.dispose();
    _reviewNotes.dispose();
    _revokeReason.dispose();
    super.dispose();
  }

  Future<void> _loadRequest({bool initial = false}) async {
    if (!initial) {
      if (_loading || _writing) return;

      setState(() {
        _loading = true;
        _message = null;
      });
    }

    try {
      final request = await widget.dataSource.fetchRequest(
        emergencyAccessId: widget.emergencyAccessId,
      );

      if (!mounted) return;

      widget.emergencyAccessContext?.reconcile(request);

      setState(() {
        _request = request;
        _message = null;
        _writeOutcomeUnknown = false;
        _refreshRequired = false;
      });
    } on AuthenticatedSessionUnavailableException catch (error) {
      if (!mounted) return;

      setState(() {
        _message =
            error.failure == AuthenticatedSessionAccessFailure.recoveryRequired
            ? 'Your saved session requires recovery before viewing this emergency access request.'
            : 'Sign in before viewing this emergency access request.';
      });
    } on ApiException catch (error) {
      if (!mounted) return;

      final status = error.statusCode;

      setState(() {
        if (status == 401) {
          _message = 'Your session is no longer accepted. Sign in again.';
        } else if (status == 403 || status == 404) {
          _message =
              'This emergency access request is not available to your account.';
        } else if (error.kind == ApiFailureKind.timeout ||
            error.kind == ApiFailureKind.connection) {
          _message = 'The request status could not be loaded. Check connectivity and refresh.';
        } else {
          _message = 'The emergency access request status could not be loaded.';
        }
      });
    } on FormatException {
      if (!mounted) return;

      setState(() {
        _message =
            'The backend returned an invalid emergency access status response.';
      });
    } on ArgumentError {
      if (!mounted) return;

      setState(() {
        _message = 'The emergency access request ID is invalid.';
      });
    } catch (_) {
      if (!mounted) return;

      setState(() {
        _message = 'The emergency access request status could not be loaded.';
      });
    } finally {
      if (mounted) {
        setState(() => _loading = false);
      }
    }
  }

  int? _durationValue() {
    final value = int.tryParse(_durationMinutes.text.trim());

    if (value == null || value < 1 || value > 60) {
      setState(() {
        _message = 'Duration must be between 1 and 60 minutes.';
      });
      return null;
    }

    return value;
  }

  Future<void> _grant() async {
    final duration = _durationValue();
    if (duration == null) return;

    final notes = _reviewNotes.text.trim();

    if (notes.length > 1000) {
      setState(() {
        _message = 'Review notes must be at most 1000 characters.';
      });
      return;
    }

    await _performWrite(
      () => widget.dataSource.grantRequest(
        emergencyAccessId: widget.emergencyAccessId,
        durationMinutes: duration,
        reviewNotes: notes.isEmpty ? null : notes,
      ),
    );
  }

  Future<void> _deny() async {
    final duration = _durationValue();
    if (duration == null) return;

    final notes = _reviewNotes.text.trim();

    if (notes.isEmpty) {
      setState(() {
        _message = 'Review notes are required when denying access.';
      });
      return;
    }

    if (notes.length > 1000) {
      setState(() {
        _message = 'Review notes must be at most 1000 characters.';
      });
      return;
    }

    await _performWrite(
      () => widget.dataSource.denyRequest(
        emergencyAccessId: widget.emergencyAccessId,
        durationMinutes: duration,
        reviewNotes: notes,
      ),
    );
  }

  Future<void> _revoke() async {
    final reason = _revokeReason.text.trim();

    if (reason.isEmpty) {
      setState(() {
        _message = 'A revoke reason is required.';
      });
      return;
    }

    if (reason.length > 500) {
      setState(() {
        _message = 'Revoke reason must be at most 500 characters.';
      });
      return;
    }

    await _performWrite(
      () => widget.dataSource.revokeRequest(
        emergencyAccessId: widget.emergencyAccessId,
        reason: reason,
      ),
    );
  }

  Future<void> _performWrite(
    Future<EmergencyAccessGrant> Function() operation,
  ) async {
    if (_writesBlocked) return;

    setState(() {
      _writing = true;
      _message = null;
    });

    try {
      final updated = await operation();

      if (!mounted) return;

      widget.emergencyAccessContext?.reconcile(updated);

      setState(() {
        _request = updated;
        _message = null;
        _writeOutcomeUnknown = false;
        _refreshRequired = false;
      });
    } on AuthenticatedSessionUnavailableException catch (error) {
      if (!mounted) return;

      setState(() {
        _message =
            error.failure == AuthenticatedSessionAccessFailure.recoveryRequired
            ? 'Your saved session requires recovery before reviewing emergency access.'
            : 'Sign in before reviewing emergency access.';
      });
    } on ApiException catch (error) {
      if (!mounted) return;

      final status = error.statusCode;

      if (status == 409) {
        _clearActiveContextForRequest();
        setState(() {
          _refreshRequired = true;
          _message = 'The emergency access request changed before this action was applied. Refresh status before another review action.';
        });
      } else if (status == 401) {
        _clearActiveContextForRequest();
        setState(() {
          _message = 'Your session is no longer accepted. Sign in again.';
        });
      } else if (status == 403 || status == 404) {
        _clearActiveContextForRequest();
        setState(() {
          _message = 'This review action is not permitted for your account.';
        });
      } else if (status == 422) {
        setState(() {
          _message = 'The backend rejected the review input. Check the fields and try again.';
        });
      } else if (error.kind == ApiFailureKind.timeout ||
          error.kind == ApiFailureKind.connection ||
          error.kind == ApiFailureKind.invalidResponse ||
          (status != null && status >= 500)) {
        _markUnknownWriteOutcome();
      } else {
        setState(() {
          _message = status == null
              ? 'The review action could not be completed.'
              : 'The backend rejected the review action (HTTP $status).';
        });
      }
    } on FormatException {
      if (!mounted) return;
      _markUnknownWriteOutcome();
    } catch (_) {
      if (!mounted) return;
      _markUnknownWriteOutcome();
    } finally {
      if (mounted) {
        setState(() => _writing = false);
      }
    }
  }

  void _clearActiveContextForRequest() {
    widget.emergencyAccessContext?.clearIfGrantId(widget.emergencyAccessId);
  }

  void _markUnknownWriteOutcome() {
    _clearActiveContextForRequest();

    setState(() {
      _writeOutcomeUnknown = true;
      _message =
          'We could not confirm whether the emergency access action completed. '
          'Do not perform another review action until status has been refreshed.';
    });
  }

  void _activateForClinicalUse() {
    final request = _request;
    final accessContext = widget.emergencyAccessContext;

    if (request == null ||
        request.status != EmergencyAccessStatus.active ||
        accessContext == null) {
      return;
    }

    try {
      accessContext.activate(request);

      setState(() {
        _message =
            'Emergency access is active for patient ${request.patientId}.';
      });
    } on ArgumentError catch (error) {
      setState(() {
        _message =
            error.message?.toString() ??
            'Emergency access cannot be activated.';
      });
    } on StateError {
      setState(() {
        _message = 'An authenticated session is required before emergency access can be activated.';
      });
    }
  }

  String _statusLabel(EmergencyAccessStatus status) {
    return switch (status) {
      EmergencyAccessStatus.requested => 'Requested',
      EmergencyAccessStatus.active => 'Active',
      EmergencyAccessStatus.denied => 'Denied',
      EmergencyAccessStatus.revoked => 'Revoked',
      EmergencyAccessStatus.expired => 'Expired',
    };
  }

  @override
  Widget build(BuildContext context) {
    final request = _request;

    return Scaffold(
      appBar: AppBar(
        title: Text('Emergency access ${widget.emergencyAccessId}'),
      ),
      body: SafeArea(
        child: _loading && request == null
            ? const Center(child: CircularProgressIndicator())
            : SingleChildScrollView(
                key: const ValueKey('emergency-access-review-scroll'),
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    if (request != null) ...[
                      Text(
                        'Emergency access request ${request.id}',
                        key: const ValueKey('emergency-access-review-title'),
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                      const SizedBox(height: 12),
                      Text('Patient ID: ${request.patientId}'),
                      Text(
                        'Status: ${_statusLabel(request.status)}',
                        key: const ValueKey('emergency-access-review-status'),
                      ),
                      Text('Requester role: ${request.requesterRole}'),
                      const SizedBox(height: 8),
                      Text('Reason: ${request.reason}'),
                      Text('Purpose: ${request.purpose}'),
                      const SizedBox(height: 8),
                      Text('Scopes: ${request.scope.join(', ')}'),
                      if (request.expiresAt != null)
                        Text(
                          'Expires: ${request.expiresAt!.toIso8601String()}',
                        ),
                    ],

                    if (_message != null) ...[
                      const SizedBox(height: 16),
                      Text(
                        _message!,
                        key: const ValueKey('emergency-access-review-message'),
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ],

                    if (_writeOutcomeUnknown) ...[
                      const SizedBox(height: 12),
                      Text(
                        'Review outcome reconciliation is required.',
                        key: const ValueKey(
                          'emergency-access-review-reconciliation-warning',
                        ),
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],

                    const SizedBox(height: 16),

                    OutlinedButton.icon(
                      key: const ValueKey(
                        'emergency-access-review-refresh-button',
                      ),
                      onPressed: _loading || _writing
                          ? null
                          : () => _loadRequest(),
                      icon: const Icon(Icons.refresh),
                      label: Text(
                        _loading ? 'Refreshing...' : 'Refresh status',
                      ),
                    ),

                    if (_loading && request != null) ...[
                      const SizedBox(height: 12),
                      const LinearProgressIndicator(),
                    ],

                    if (request?.status == EmergencyAccessStatus.requested) ...[
                      const SizedBox(height: 24),
                      _buildRequestedActions(),
                    ],

                    if (request?.status == EmergencyAccessStatus.active) ...[
                      const SizedBox(height: 24),
                      _buildActiveActions(),
                    ],

                    if (request != null &&
                        const <EmergencyAccessStatus>{
                          EmergencyAccessStatus.denied,
                          EmergencyAccessStatus.revoked,
                          EmergencyAccessStatus.expired,
                        }.contains(request.status)) ...[
                      const SizedBox(height: 24),
                      const Text(
                        'This request is in a terminal state. '
                        'No additional review action is available.',
                        key: ValueKey('emergency-access-review-terminal-state'),
                      ),
                    ],
                  ],
                ),
              ),
      ),
    );
  }

  Widget _buildRequestedActions() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          'Review requested access',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 12),
        TextField(
          key: const ValueKey('emergency-access-review-duration'),
          controller: _durationMinutes,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(
            labelText: 'Duration minutes',
            helperText: 'Required by the review API. Grant duration must be 1?60 minutes.',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          key: const ValueKey('emergency-access-review-notes'),
          controller: _reviewNotes,
          maxLength: 1000,
          maxLines: 4,
          decoration: const InputDecoration(
            labelText: 'Review notes',
            helperText: 'Required for denial; optional for grant.',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        ElevatedButton.icon(
          key: const ValueKey('emergency-access-review-grant-button'),
          onPressed: _writesBlocked ? null : _grant,
          icon: const Icon(Icons.check_circle_outline),
          label: const Text('Grant emergency access'),
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          key: const ValueKey('emergency-access-review-deny-button'),
          onPressed: _writesBlocked ? null : _deny,
          icon: const Icon(Icons.block),
          label: const Text('Deny emergency access'),
        ),
      ],
    );
  }

  Widget _buildActiveActions() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          'Active emergency access',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        if (widget.emergencyAccessContext != null) ...[
          const SizedBox(height: 12),
          FilledButton.tonalIcon(
            key: const ValueKey('emergency-access-review-use-button'),
            onPressed: _writesBlocked ? null : _activateForClinicalUse,
            icon: const Icon(Icons.emergency_outlined),
            label: const Text('Use emergency access for this patient'),
          ),
        ],
        const SizedBox(height: 12),
        TextField(
          key: const ValueKey('emergency-access-review-revoke-reason'),
          controller: _revokeReason,
          maxLength: 500,
          maxLines: 3,
          decoration: const InputDecoration(
            labelText: 'Revoke reason',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        OutlinedButton.icon(
          key: const ValueKey('emergency-access-review-revoke-button'),
          onPressed: _writesBlocked ? null : _revoke,
          icon: const Icon(Icons.cancel_outlined),
          label: const Text('Revoke emergency access'),
        ),
      ],
    );
  }
}
