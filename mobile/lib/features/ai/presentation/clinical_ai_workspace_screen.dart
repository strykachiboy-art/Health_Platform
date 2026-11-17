import 'dart:convert';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/ai/data/clinical_ai_contract.dart';
import 'package:clinic_system_pro/features/ai/data/clinical_ai_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient.dart';

/// Patient-bound AI assistance. The patient GET and authoritative AI POST are
/// separate. No AI result is copied into clinical orders or stored locally.
class ClinicalAiWorkspaceScreen extends StatefulWidget {
  const ClinicalAiWorkspaceScreen({
    super.key,
    required this.patientId,
    required this.loadPatient,
    required this.source,
    required this.guard,
  });

  final int patientId;
  final Future<Patient> Function(int) loadPatient;
  final ClinicalAiRemoteDataSource source;
  final SessionNavigationGuard guard;

  @override
  State<ClinicalAiWorkspaceScreen> createState() =>
      _ClinicalAiWorkspaceScreenState();
}

class _ClinicalAiWorkspaceScreenState extends State<ClinicalAiWorkspaceScreen> {
  final _drugs = TextEditingController();
  final _symptoms = TextEditingController();
  final _labOrder = TextEditingController();
  final _labData = TextEditingController();
  final Set<ClinicalAiFeature> _attempted = <ClinicalAiFeature>{};
  ClinicalAiFeature _feature = ClinicalAiFeature.drugInteractions;
  Patient? _patient;
  ClinicalAiAdvisory? _advisory;
  bool _loading = true;
  bool _busy = false;
  bool _confirmed = false;
  int _loadGeneration = 0;
  String? _notice;

  @override
  void initState() {
    super.initState();
    widget.guard.addListener(_onAuthorityChanged);
    _load();
  }

  @override
  void didUpdateWidget(covariant ClinicalAiWorkspaceScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.guard, widget.guard)) {
      oldWidget.guard.removeListener(_onAuthorityChanged);
      widget.guard.addListener(_onAuthorityChanged);
    }
    if (oldWidget.patientId != widget.patientId ||
        !identical(oldWidget.source, widget.source)) {
      _clearSensitive();
      _patient = null;
      _loading = true;
      _load();
    }
  }

  void _clearSensitive() {
    _advisory = null;
    _drugs.clear();
    _symptoms.clear();
    _labOrder.clear();
    _labData.clear();
    _confirmed = false;
    _attempted.clear();
  }

  void _onAuthorityChanged() {
    if (!widget.guard.canAccessProtectedRoutes && mounted) {
      _loadGeneration++;
      _clearSensitive();
      setState(() {
        _patient = null;
        _notice = 'Session unavailable. Return to sign in.';
      });
    }
  }

  Future<void> _load() async {
    final generation = ++_loadGeneration;
    try {
      if (widget.patientId <= 0) throw const ClinicalAiUnavailable();
      final revision = widget.guard.revision;
      final before = await widget.source.requireActiveSession();
      final patient = await widget.loadPatient(widget.patientId);
      final after = await widget.source.requireActiveSession();
      if (revision != widget.guard.revision ||
          before.userId != after.userId ||
          before.clinicContextId != after.clinicContextId ||
          before.role != after.role ||
          before.accessToken != after.accessToken ||
          before.refreshToken != after.refreshToken ||
          patient.id != widget.patientId ||
          patient.clinicId != after.clinicContextId ||
          !patient.isActive) {
        throw const ClinicalAiUnavailable();
      }
      if (!mounted || generation != _loadGeneration) return;
      setState(() {
        _patient = patient;
        _loading = false;
        _notice = null;
      });
    } on Object {
      if (!mounted || generation != _loadGeneration) return;
      setState(() {
        _patient = null;
        _loading = false;
        _notice = 'Patient context or authorization could not be verified.';
      });
    }
  }

  @override
  void dispose() {
    widget.guard.removeListener(_onAuthorityChanged);
    _clearSensitive();
    _drugs.dispose();
    _symptoms.dispose();
    _labOrder.dispose();
    _labData.dispose();
    super.dispose();
  }

  Future<void> _run() async {
    if (_busy ||
        !_confirmed ||
        _patient == null ||
        _attempted.contains(_feature) ||
        !widget.guard.canAccessProtectedRoutes) {
      return;
    }
    late final Future<ClinicalAiAdvisory> Function() execute;
    try {
      switch (_feature) {
        case ClinicalAiFeature.drugInteractions:
          final drugs = _drugs.text
              .split(',')
              .map((drug) => drug.trim())
              .toList();
          if (drugs.length < 2 || drugs.any((drug) => drug.isEmpty)) {
            throw const FormatException(
              'Enter at least two drug names separated by commas.',
            );
          }
          execute = () => widget.source.checkDrugInteractions(
            patientId: widget.patientId,
            drugNames: drugs,
          );
          break;
        case ClinicalAiFeature.triage:
          if (_symptoms.text.trim().isEmpty) {
            throw const FormatException('Enter the observed symptoms.');
          }
          execute = () => widget.source.assistTriage(
            patientId: widget.patientId,
            symptoms: _symptoms.text,
          );
          break;
        case ClinicalAiFeature.labInterpretation:
          final order = int.tryParse(_labOrder.text.trim());
          final value = jsonDecode(_labData.text);
          if (order == null ||
              order <= 0 ||
              value is! Map<String, dynamic> ||
              value.isEmpty ||
              value.length > 100) {
            throw const FormatException(
              'Enter a valid lab order ID and results JSON object.',
            );
          }
          execute = () => widget.source.interpretLabResults(
            patientId: widget.patientId,
            labOrderId: order,
            resultData: value,
          );
          break;
      }
    } on Object {
      setState(
        () => _notice = 'Check the inputs before requesting AI assistance.',
      );
      return;
    }
    final revision = widget.guard.revision;
    final feature = _feature;
    setState(() {
      _busy = true;
      _attempted.add(feature);
      _notice = null;
      _advisory = null;
    });
    try {
      final advisory = await execute();
      if (!mounted ||
          !widget.guard.canAccessProtectedRoutes ||
          widget.guard.revision != revision) {
        return;
      }
      setState(() => _advisory = advisory);
    } on ClinicalAiUnavailable {
      if (!mounted) return;
      setState(
        () => _notice = 'AI assistance unavailable or not authorized. No result will be shown.',
      );
    } on Object {
      if (!mounted) return;
      setState(
        () => _notice = 'AI outcome uncertain. Do not repeat this request until audit and credit usage are reviewed.',
      );
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _input() => switch (_feature) {
    ClinicalAiFeature.drugInteractions => TextField(
      key: const ValueKey('gap-ai-drugs'),
      controller: _drugs,
      maxLength: 256,
      decoration: const InputDecoration(
        labelText: 'Drug names (comma separated)',
        helperText:
            'Two or more verified drug names. No automatic chart extraction.',
      ),
    ),
    ClinicalAiFeature.triage => TextField(
      key: const ValueKey('gap-ai-symptoms'),
      controller: _symptoms,
      maxLines: 4,
      maxLength: 5000,
      decoration: const InputDecoration(labelText: 'Observed symptoms'),
    ),
    ClinicalAiFeature.labInterpretation => Column(
      children: [
        TextField(
          key: const ValueKey('gap-ai-lab-order'),
          controller: _labOrder,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(labelText: 'Verified lab order ID'),
        ),
        TextField(
          key: const ValueKey('gap-ai-lab-data'),
          controller: _labData,
          maxLines: 4,
          decoration: const InputDecoration(
            labelText: 'Verified results as JSON object',
            helperText: 'Example: {"hemoglobin": "12 g/dL"}',
          ),
        ),
      ],
    ),
  };

  Widget _output(ClinicalAiAdvisory result) {
    final theme = Theme.of(context);
    return Card(
      key: const ValueKey('gap-ai-advisory'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'AI-generated advisory — clinician review required',
              style: theme.textTheme.titleMedium,
            ),
            if (result.summary != null) Text(result.summary!),
            if (result.riskScore != null)
              Text('Suggested risk: ${result.riskScore}'),
            if (result.recommendation != null) Text(result.recommendation!),
            if (result.interpretation != null) Text(result.interpretation!),
            if (result.interactions.isNotEmpty)
              Text('Potential interactions: ${result.interactions.length}'),
            for (final entry in result.interactions)
              if (entry['description'] is String)
                Text(entry['description'] as String),
            for (final item in result.abnormalFindings) Text('Finding: $item'),
            for (final item in result.recommendations)
              Text('Consideration: $item'),
            const Text(
              'Not a diagnosis, prescription, order, or approved clinical record.',
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final patient = _patient;
    return Scaffold(
      key: const ValueKey('gap-ai-workspace'),
      appBar: AppBar(title: const Text('Clinical AI assistance')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : patient == null
          ? Center(child: Text(_notice ?? 'Patient context unavailable.'))
          : SingleChildScrollView(
              padding: const EdgeInsets.all(16),
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 840),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Verified patient: ${patient.patientNumber}',
                        key: const ValueKey('gap-ai-patient-context'),
                      ),
                      const SizedBox(height: 8),
                      const Text(
                        'An AI request may consume one clinic AI credit. Results are advisory only; review independently. No automatic clinical action.',
                      ),
                      const SizedBox(height: 16),
                      Wrap(
                        spacing: 8,
                        children: [
                          for (final choice in ClinicalAiFeature.values)
                            ChoiceChip(
                              label: Text(choice.label),
                              selected: _feature == choice,
                              onSelected: _busy
                                  ? null
                                  : (selected) {
                                      if (!selected) return;
                                      setState(() {
                                        _feature = choice;
                                        _advisory = null;
                                        _notice = null;
                                        _confirmed = false;
                                      });
                                    },
                            ),
                        ],
                      ),
                      const SizedBox(height: 16),
                      _input(),
                      CheckboxListTile(
                        key: const ValueKey('gap-ai-confirm'),
                        value: _confirmed,
                        onChanged: _busy
                            ? null
                            : (value) =>
                                  setState(() => _confirmed = value ?? false),
                        title: const Text(
                          'I have verified the patient, inputs and AI-credit use.',
                        ),
                        controlAffinity: ListTileControlAffinity.leading,
                      ),
                      FilledButton(
                        key: const ValueKey('gap-ai-submit'),
                        onPressed:
                            _busy ||
                                !_confirmed ||
                                _attempted.contains(_feature)
                            ? null
                            : _run,
                        child: Text(
                          _busy ? 'Requesting…' : 'Request advisory once',
                        ),
                      ),
                      if (_attempted.contains(_feature))
                        const Text(
                          'This request cannot be repeated from this session.',
                        ),
                      if (_notice != null)
                        Text(_notice!, key: const ValueKey('gap-ai-notice')),
                      if (_advisory != null) _output(_advisory!),
                    ],
                  ),
                ),
              ),
            ),
    );
  }
}
