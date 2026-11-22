import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/administration/data/wd09cf_operation_contract.dart';
import 'package:clinic_system_pro/features/administration/data/wd09cf_operations_remote_data_source.dart';

/// One online action at a time. Never automatically submits or replays writes.
/// Every action requires an explicit human confirmation and Flask authority.
class Wd09cfOperationsScreen extends StatefulWidget {
  const Wd09cfOperationsScreen({super.key, required this.source});
  final Wd09cfOperationsRemoteDataSource source;

  @override
  State<Wd09cfOperationsScreen> createState() => _Wd09cfOperationsScreenState();
}

class _Wd09cfOperationsScreenState extends State<Wd09cfOperationsScreen> {
  AuthTokenResponse? _actor;
  Wd09cfAction? _action;
  final TextEditingController _recordId = TextEditingController();
  final Map<String, TextEditingController> _fields =
      <String, TextEditingController>{};
  bool _busy = false;
  int _generation = 0;
  String? _message;
  Wd09cfReceipt? _receipt;

  @override
  void initState() {
    super.initState();
    widget.source.sessionNavigationGuard.addListener(_onSessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant Wd09cfOperationsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(widget.source, oldWidget.source)) {
      oldWidget.source.sessionNavigationGuard.removeListener(_onSessionChanged);
      widget.source.sessionNavigationGuard.addListener(_onSessionChanged);
      _onSessionChanged();
    }
  }

  void _disposeFields() {
    for (final controller in _fields.values) {
      controller.dispose();
    }
    _fields.clear();
    _recordId.clear();
  }

  @override
  void dispose() {
    _generation++;
    widget.source.sessionNavigationGuard.removeListener(_onSessionChanged);
    _disposeFields();
    _recordId.dispose();
    super.dispose();
  }

  void _onSessionChanged() {
    if (!mounted) {
      return;
    }
    _generation++;
    _disposeFields();
    setState(() {
      _actor = null;
      _action = null;
      _receipt = null;
      _busy = false;
      _message = 'Session changed. All form values were cleared.';
    });
    if (widget.source.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  bool _current(
    int generation,
    int revision,
    Wd09cfOperationsRemoteDataSource source,
  ) =>
      mounted &&
      identical(source, widget.source) &&
      _generation == generation &&
      source.sessionNavigationGuard.canAccessProtectedRoutes &&
      source.sessionNavigationGuard.revision == revision;

  Future<void> _load() async {
    final source = widget.source;
    final generation = ++_generation;
    final revision = source.sessionNavigationGuard.revision;
    setState(() {
      _busy = true;
      _message = null;
      _receipt = null;
    });
    try {
      final actor = await source.authorizedSession();
      if (!_current(generation, revision, source)) {
        return;
      }
      final allowed = Wd09cfAction.values
          .where(
            (action) => action.allowedFor(actor.role, actor.clinicContextId),
          )
          .toList(growable: false);
      if (allowed.isEmpty) {
        throw const Wd09cfUnavailable();
      }
      setState(() {
        _actor = actor;
        _busy = false;
        _select(allowed.first, notify: false);
      });
    } on Object {
      if (!_current(generation, revision, source)) {
        return;
      }
      setState(() {
        _actor = null;
        _action = null;
        _busy = false;
        _message = 'Operations unavailable for the authenticated clinic role.';
      });
    }
  }

  void _select(Wd09cfAction action, {bool notify = true}) {
    _disposeFields();
    for (final field in action.fields) {
      _fields[field.key] = TextEditingController(
        text: field.kind == Wd09cfInputKind.choice && field.values.isNotEmpty
            ? field.values.first
            : '',
      );
    }
    if (notify) {
      setState(() {
        _action = action;
        _message = null;
        _receipt = null;
      });
    } else {
      _action = action;
      _message = null;
      _receipt = null;
    }
  }

  static String _errorMessage(Object error) {
    if (error is Wd09cfAlreadyAttempted) {
      return 'This action was already attempted in this session. Check the server record; no automatic retry is permitted.';
    }
    if (error is Wd09cfUnavailable) {
      return 'Access or clinic session changed. The operation was not confirmed.';
    }
    if (error is FormatException || error is ArgumentError) {
      return 'Input or server response did not match the verified contract. Refresh and check values.';
    }
    if (error is ApiException) {
      if (error.kind == ApiFailureKind.timeout ||
          error.kind == ApiFailureKind.connection) {
        return 'Server response uncertain. This mutation will NOT be retried. Verify server state before a new session.';
      }
      if (const <int>{401, 403}.contains(error.statusCode)) {
        return 'The backend denied this operation.';
      }
    }
    return 'The operation could not be confirmed by the server. Do not retry automatically.';
  }

  Future<void> _submit() async {
    if (_busy) {
      return;
    }
    final action = _action;
    final actor = _actor;
    if (action == null ||
        actor == null ||
        !action.allowedFor(actor.role, actor.clinicContextId)) {
      return;
    }
    final source = widget.source;
    final generation = _generation;
    final revision = source.sessionNavigationGuard.revision;
    int? id;
    final values = <String, String>{};
    try {
      if (action.needsRecordId) {
        id = int.tryParse(_recordId.text.trim());
        if (id == null || id <= 0 || id > 2147483647) {
          throw const FormatException('Invalid record ID.');
        }
      }
      for (final entry in _fields.entries) {
        values[entry.key] = entry.value.text;
      }
      action.parseBody(values);
    } on Object catch (error) {
      setState(() => _message = _errorMessage(error));
      return;
    }
    final preview = values.entries
        .map((entry) {
          final value = entry.value;
          final display = value.length > 120
              ? '${value.substring(0, 120)}…'
              : value;
          return '${entry.key}: $display';
        })
        .join('\n');
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Confirm: ${action.title}'),
        content: SingleChildScrollView(
          child: Text(
            'Clinic ${actor.clinicContextId} · ${actor.role}\n'
            '${id == null ? 'New record / workflow' : 'Record #$id'}\n\n'
            '$preview\n\n'
            'Send exactly one ${action.verb} to the clinic backend? '
            'This action is not queued, retried or replayed automatically. '
            'For payroll and lifecycle actions, verify the record carefully.',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('wd09cf-confirm-submit'),
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Confirm once'),
          ),
        ],
      ),
    );
    if (confirmed != true || !_current(generation, revision, source)) {
      return;
    }
    setState(() {
      _busy = true;
      _receipt = null;
      _message = null;
    });
    try {
      final result = await source.execute(
        action: action,
        fields: values,
        recordId: id,
      );
      if (!_current(generation, revision, source)) {
        return;
      }
      setState(() {
        _busy = false;
        _receipt = result;
        _message =
            'Server confirmed ${result.label} for clinic ${result.clinicId}, record #${result.recordId}.';
      });
    } on Object catch (error) {
      if (!_current(generation, revision, source)) {
        return;
      }
      setState(() {
        _busy = false;
        _receipt = null;
        _message = _errorMessage(error);
      });
    }
  }

  Widget _entry(Wd09cfField field) {
    final controller = _fields[field.key]!;
    if (field.kind == Wd09cfInputKind.choice) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(field.label),
          DropdownButton<String>(
            key: ValueKey('wd09cf-field-${field.key}'),
            isExpanded: true,
            value: controller.text,
            items: [
              for (final choice in field.values)
                DropdownMenuItem<String>(value: choice, child: Text(choice)),
            ],
            onChanged: _busy
                ? null
                : (value) {
                    if (value != null) {
                      setState(() => controller.text = value);
                    }
                  },
          ),
        ],
      );
    }
    return TextField(
      key: ValueKey('wd09cf-field-${field.key}'),
      controller: controller,
      enabled: !_busy,
      maxLength: field.kind == Wd09cfInputKind.text ? field.maxLength : null,
      keyboardType: switch (field.kind) {
        Wd09cfInputKind.positiveId ||
        Wd09cfInputKind.nonNegativeInt ||
        Wd09cfInputKind.decimal => const TextInputType.numberWithOptions(
          decimal: true,
        ),
        _ => TextInputType.text,
      },
      decoration: InputDecoration(
        labelText: field.label,
        helperText: field.required ? 'Required' : 'Optional',
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final actor = _actor;
    final action = _action;
    final options = actor == null
        ? const <Wd09cfAction>[]
        : Wd09cfAction.values
              .where((op) => op.allowedFor(actor.role, actor.clinicContextId))
              .toList(growable: false);
    return ListView(
      key: const ValueKey('wd09cf-operations-workspace'),
      padding: const EdgeInsets.all(20),
      children: [
        Text(
          'Hospital operations',
          style: Theme.of(context).textTheme.headlineSmall,
        ),
        const SizedBox(height: 8),
        const Text(
          'Live Flask operations only. The selected clinic and role are verified on every request. No automatic retry, queue, or hidden privileges.',
        ),
        if (_busy)
          const LinearProgressIndicator(key: ValueKey('wd09cf-loading')),
        if (actor == null && !_busy)
          const Text('An authorized clinic session is required.'),
        if (actor != null && action != null) ...[
          const SizedBox(height: 14),
          Text('Clinic #${actor.clinicContextId} · ${actor.role}'),
          DropdownButton<Wd09cfAction>(
            key: const ValueKey('wd09cf-action-select'),
            isExpanded: true,
            value: action,
            items: [
              for (final op in options)
                DropdownMenuItem<Wd09cfAction>(
                  value: op,
                  child: Text(op.title),
                ),
            ],
            onChanged: _busy
                ? null
                : (value) {
                    if (value != null) {
                      _select(value);
                    }
                  },
          ),
          if (action.needsRecordId)
            TextField(
              key: const ValueKey('wd09cf-record-id'),
              controller: _recordId,
              enabled: !_busy,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: 'Existing record ID (from verified directory)',
              ),
            ),
          for (final field in action.fields) _entry(field),
          const SizedBox(height: 12),
          FilledButton.icon(
            key: const ValueKey('wd09cf-review-submit'),
            onPressed: _busy ? null : () => unawaited(_submit()),
            icon: const Icon(Icons.fact_check_outlined),
            label: const Text('Review and confirm once'),
          ),
        ],
        if (_message != null) ...[
          const SizedBox(height: 12),
          Text(_message!, key: const ValueKey('wd09cf-result')),
        ],
        if (_receipt != null)
          const Text(
            'The server receipt was verified. Refresh the relevant directory to inspect the latest state.',
          ),
      ],
    );
  }
}
