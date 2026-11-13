import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/surgery/data/surgery_action_contract.dart';
import 'package:clinic_system_pro/features/surgery/data/surgery_mutation_remote_data_source.dart';
import 'package:clinic_system_pro/features/surgery/data/surgery_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/surgery/data/models/surgery_read_models.dart';

/// Deliberately does not offer consent upload, credential attestation, Sign-Out,
/// induction, operative completion, medication administration or transfer.
/// A consent/legal document needs a protected source store before upload UI.
class SurgeryActionScreen extends StatefulWidget {
  const SurgeryActionScreen({
    super.key,
    required this.caseId,
    required this.reader,
    required this.writer,
    required this.guard,
  });

  final int caseId;
  final SurgeryReadRemoteDataSource reader;
  final SurgeryMutationRemoteDataSource writer;
  final SessionNavigationGuard guard;

  @override
  State<SurgeryActionScreen> createState() => _SurgeryActionScreenState();
}

class _SurgeryActionScreenState extends State<SurgeryActionScreen> {
  SurgeryCaseDetail? _detail;
  SurgeryActionKind? _action;
  final Map<String, String> _values = <String, String>{};
  String? _message;
  bool _loading = true;
  bool _sending = false;
  int _generation = 0;
  int? _identityRevision;
  String? _role;

  @override
  void initState() {
    super.initState();
    widget.guard.addListener(_onSession);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant SurgeryActionScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.guard, widget.guard)) {
      oldWidget.guard.removeListener(_onSession);
      widget.guard.addListener(_onSession);
    }
    if (!identical(oldWidget.writer, widget.writer) ||
        !identical(oldWidget.reader, widget.reader) ||
        !identical(oldWidget.guard, widget.guard) ||
        oldWidget.caseId != widget.caseId) {
      _onSession();
    }
  }

  void _onSession() {
    ++_generation;
    if (!mounted) {
      return;
    }
    setState(() {
      _values.clear();
      _detail = null;
      _action = null;
      _message = 'Session changed. Clinical entry was cleared.';
      _loading = false;
      _identityRevision = null;
      _role = null;
    });
    if (widget.guard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  Future<void> _load() async {
    final request = ++_generation;
    final revision = widget.guard.revision;
    if (!mounted) {
      return;
    }
    setState(() {
      _loading = true;
      _detail = null;
      _message = null;
      _values.clear();
      _action = null;
    });
    try {
      final result = await widget.reader.fetchCase(widget.caseId);
      final session =
          (await widget.reader.dashboardSource.loadSession()).session;
      if (session == null ||
          !const <String>{
            'doctor',
            'nurse',
          }.contains(session.role.toLowerCase())) {
        throw const SurgeryMutationUnavailable();
      }
      if (!mounted ||
          request != _generation ||
          widget.guard.revision != revision ||
          !widget.guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _detail = result;
        _loading = false;
        _identityRevision = revision;
        _role = session.role.toLowerCase();
      });
    } on Object {
      if (!mounted ||
          request != _generation ||
          widget.guard.revision != revision) {
        return;
      }
      setState(() {
        _loading = false;
        _detail = null;
        _message = 'Surgery case unavailable or not authorized.';
      });
    }
  }

  Future<void> _reconcile() async {
    final kind = _action;
    if (kind == null || _sending || _detail == null) {
      return;
    }
    setState(() => _loading = true);
    final revision = widget.guard.revision;
    try {
      final result = await widget.writer.reconcile(
        caseId: widget.caseId,
        kind: kind,
      );
      if (!mounted ||
          widget.guard.revision != revision ||
          !widget.guard.canAccessProtectedRoutes) {
        return;
      }
      final itemCount = result['items'] is List<dynamic>
          ? (result['items'] as List<dynamic>).length
          : 1;
      setState(() {
        _loading = false;
        _message =
            'Server readback received ($itemCount record(s)). '
            'Review authoritative records; this does not permit retrying a write.';
      });
    } on Object {
      if (!mounted || widget.guard.revision != revision) {
        return;
      }
      setState(() {
        _loading = false;
        _message =
            'Server status could not be confirmed. Do not repeat the write.';
      });
    }
  }

  Future<void> _submit() async {
    final kind = _action;
    if (kind == null ||
        _sending ||
        _loading ||
        _detail == null ||
        widget.guard.revision != _identityRevision ||
        !widget.guard.canAccessProtectedRoutes) {
      return;
    }
    final input = SurgeryActionInput(
      caseId: widget.caseId,
      kind: kind,
      raw: Map<String, String>.of(_values),
    );
    try {
      input.encode();
    } on Object {
      setState(
        () => _message = 'Complete every required field before submission.',
      );
      return;
    }
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('Confirm one clinical documentation action'),
        content: Text(
          '${kind.label} for case #${widget.caseId}? '
          'This online-only action may commit even if the response is lost. '
          'It cannot be retried automatically or through this screen.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialog).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialog).pop(true),
            child: const Text('Submit once'),
          ),
        ],
      ),
    );
    if (!mounted ||
        confirmed != true ||
        widget.guard.revision != _identityRevision ||
        !widget.guard.canAccessProtectedRoutes) {
      return;
    }
    setState(() {
      _sending = true;
      _message = null;
    });
    try {
      final accepted = await widget.writer.submitOnce(input);
      if (!mounted ||
          widget.guard.revision != _identityRevision ||
          !widget.guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _values.clear();
        _message =
            'Documentary action confirmed by server readback. '
            'Record #${accepted.recordId}; this is NOT operative clearance.';
      });
    } on SurgeryMutationAlreadyAttempted {
      if (mounted) {
        setState(
          () =>
              _message = 'Already attempted. Use server status readback only.',
        );
      }
    } on SurgeryMutationUnconfirmed {
      if (mounted) {
        setState(
          () => _message =
              'Outcome UNKNOWN. Do not submit again. '
              'Use server status readback and clinical reconciliation.',
        );
      }
    } on SurgeryMutationUnavailable {
      if (mounted) {
        setState(
          () => _message =
              'No Surgery mutation sent: offline, '
              'session/clinic changed or clinical authority unavailable.',
        );
      }
    } on Object {
      if (mounted) {
        setState(
          () => _message =
              'Clinical action could not be confirmed. '
              'Do not repeat without authoritative reconciliation.',
        );
      }
    } finally {
      if (mounted) {
        setState(() => _sending = false);
      }
    }
  }

  Widget _field(SurgeryField field, SurgeryActionKind action) {
    if (field.type == SurgeryFieldType.affirmative) {
      return CheckboxListTile(
        key: ValueKey('surgery-field-${action.name}-${field.name}'),
        title: Text(field.label),
        value: _values[field.name] == 'true',
        onChanged: _sending
            ? null
            : (value) => setState(() {
                if (value == true) {
                  _values[field.name] = 'true';
                } else {
                  _values.remove(field.name);
                }
              }),
      );
    }
    if (field.type == SurgeryFieldType.choice) {
      return DropdownButtonFormField<String>(
        key: ValueKey('surgery-field-${action.name}-${field.name}'),
        initialValue: _values[field.name],
        decoration: InputDecoration(labelText: field.label),
        items: [
          for (final choice in field.choices)
            DropdownMenuItem(value: choice, child: Text(choice)),
        ],
        onChanged: _sending
            ? null
            : (value) => setState(() {
                if (value == null) {
                  _values.remove(field.name);
                } else {
                  _values[field.name] = value;
                }
              }),
      );
    }
    return TextFormField(
      key: ValueKey('surgery-field-${action.name}-${field.name}'),
      initialValue: _values[field.name] ?? '',
      enabled: !_sending,
      maxLength: field.type == SurgeryFieldType.boundedText
          ? field.maxLength
          : null,
      minLines: field.maxLength > 1000 ? 2 : 1,
      maxLines: field.maxLength > 1000 ? 4 : 1,
      keyboardType: field.type == SurgeryFieldType.positiveId
          ? TextInputType.number
          : TextInputType.text,
      decoration: InputDecoration(labelText: field.label),
      onChanged: (value) {
        if (value.isEmpty) {
          _values.remove(field.name);
        } else {
          _values[field.name] = value;
        }
      },
    );
  }

  @override
  void dispose() {
    ++_generation;
    widget.guard.removeListener(_onSession);
    _values.clear();
    _detail = null;
    _role = null;
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final caseRecord = _detail?.caseRecord;
    // Role remains checked again by the writer immediately before the POST.
    // UI availability is never clinical authorization.
    return Scaffold(
      key: const ValueKey('surgery-c-actions'),
      appBar: AppBar(title: const Text('Surgery clinical documentation')),
      body: _loading && caseRecord == null
          ? const Center(child: CircularProgressIndicator())
          : caseRecord == null
          ? Center(child: Text(_message ?? 'Surgery unavailable.'))
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Case #${caseRecord.caseId} · ${caseRecord.caseStatus}',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 8),
                const Text(
                  'Documentation only. This screen does not release '
                  'surgery, induce anesthesia, administer medication, transfer '
                  'a patient, complete Sign-Out, or finalize clinical care.',
                ),
                const SizedBox(height: 8),
                const Text(
                  'New operative report creation, consent document recording '
                  'and independent verification require source-proven journal and '
                  'protected-document workflows; they are not available on mobile yet.',
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<SurgeryActionKind>(
                  key: const ValueKey('surgery-c-action-picker'),
                  initialValue: _action,
                  decoration: const InputDecoration(
                    labelText: 'Documentation action',
                  ),
                  items: [
                    for (final action in SurgeryActionKind.values.where(
                      (a) => a.role == _role,
                    ))
                      DropdownMenuItem(
                        value: action,
                        child: Text(action.label),
                      ),
                  ],
                  onChanged: _sending
                      ? null
                      : (value) => setState(() {
                          _action = value;
                          _values.clear();
                          _message = null;
                        }),
                ),
                if (_action case final SurgeryActionKind action) ...[
                  const SizedBox(height: 14),
                  for (final field in action.fields) _field(field, action),
                  const SizedBox(height: 14),
                  FilledButton(
                    key: const ValueKey('surgery-c-submit-once'),
                    onPressed:
                        _sending ||
                            _loading ||
                            caseRecord.caseStatus != 'planned'
                        ? null
                        : () => unawaited(_submit()),
                    child: const Text('Review and submit once'),
                  ),
                  TextButton(
                    key: const ValueKey('surgery-c-reconcile-get'),
                    onPressed: _sending || _loading
                        ? null
                        : () => unawaited(_reconcile()),
                    child: const Text('Read server status (GET only)'),
                  ),
                ],
                if (_sending) const LinearProgressIndicator(),
                if (_message != null)
                  Text(_message!, key: const ValueKey('surgery-c-result')),
              ],
            ),
    );
  }
}
