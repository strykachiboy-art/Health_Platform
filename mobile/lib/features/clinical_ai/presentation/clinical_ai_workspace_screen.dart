import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/clinical_ai/data/clinical_ai_remote_data_source.dart';

enum ClinicalAiWorkflow { interactions, triage, laboratory }

/// Explicit, online-only clinical assistance. No AI output changes chart data.
class ClinicalAiWorkspaceScreen extends StatefulWidget {
  const ClinicalAiWorkspaceScreen({
    super.key,
    required this.source,
    required this.guard,
  });

  final ClinicalAiRemoteDataSource source;
  final SessionNavigationGuard guard;

  @override
  State<ClinicalAiWorkspaceScreen> createState() =>
      _ClinicalAiWorkspaceScreenState();
}

class _ClinicalAiWorkspaceScreenState extends State<ClinicalAiWorkspaceScreen> {
  final _patientId = TextEditingController();
  final _labOrderId = TextEditingController();
  final _drugs = TextEditingController();
  final _symptoms = TextEditingController();
  final _labData = TextEditingController();
  ClinicalAiWorkflow _workflow = ClinicalAiWorkflow.interactions;
  ClinicalAiSuggestion? _suggestion;
  bool _busy = false;
  String? _error;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.guard.addListener(_onGuardChange);
  }

  @override
  void dispose() {
    widget.guard.removeListener(_onGuardChange);
    _generation++;
    _patientId.dispose();
    _labOrderId.dispose();
    _drugs.dispose();
    _symptoms.dispose();
    _labData.dispose();
    super.dispose();
  }

  void _onGuardChange() {
    _generation++;
    if (!mounted) return;
    setState(() {
      _suggestion = null;
      _busy = false;
      _error =
          'Session or clinic changed. Previous AI suggestions were cleared.';
    });
  }

  int? _optionalId(String raw) {
    if (raw.trim().isEmpty) return null;
    final parsed = int.tryParse(raw.trim());
    if (parsed == null || parsed <= 0) {
      throw const FormatException('Enter a positive numeric patient/order ID.');
    }
    return parsed;
  }

  Future<void> _submit() async {
    if (_busy || !widget.guard.canAccessProtectedRoutes) return;
    final generation = ++_generation;
    final revision = widget.guard.revision;
    setState(() {
      _busy = true;
      _suggestion = null;
      _error = null;
    });
    try {
      final patientId = _optionalId(_patientId.text);
      final ClinicalAiSuggestion result;
      switch (_workflow) {
        case ClinicalAiWorkflow.interactions:
          result = await widget.source.drugInteractions(
            drugNames: _drugs.text
                .split(',')
                .map((value) => value.trim())
                .toList(),
            patientId: patientId,
          );
          break;
        case ClinicalAiWorkflow.triage:
          if (patientId == null) {
            throw const FormatException('A verified patient ID is required.');
          }
          result = await widget.source.triage(
            patientId: patientId,
            symptoms: _symptoms.text.trim(),
          );
          break;
        case ClinicalAiWorkflow.laboratory:
          final raw = jsonDecode(_labData.text);
          if (raw is! Map<String, dynamic>) {
            throw const FormatException(
              'Enter a JSON object of laboratory results.',
            );
          }
          result = await widget.source.interpretLab(
            resultData: raw,
            patientId: patientId,
            labOrderId: _optionalId(_labOrderId.text),
          );
          break;
      }
      if (!mounted ||
          generation != _generation ||
          !widget.guard.canAccessProtectedRoutes ||
          widget.guard.revision != revision) {
        return;
      }
      setState(() => _suggestion = result);
    } on Object {
      if (!mounted ||
          generation != _generation ||
          widget.guard.revision != revision) {
        return;
      }
      setState(
        () => _error =
            'AI assistance could not be confirmed. No clinical action was saved or queued. '
            'Review the patient and manually submit again only if appropriate.',
      );
    } finally {
      if (mounted && generation == _generation) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Clinical AI assistance')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          const Text(
            'AI-generated clinical assistance — advisory only. '
            'Clinical decisions require review in the existing authoritative workflows.',
            key: ValueKey('gapai-advisory-disclaimer'),
          ),
          const SizedBox(height: 16),
          DropdownButtonFormField<ClinicalAiWorkflow>(
            initialValue: _workflow,
            key: const ValueKey('gapai-workflow'),
            decoration: const InputDecoration(labelText: 'Assistance type'),
            items: const [
              DropdownMenuItem(
                value: ClinicalAiWorkflow.interactions,
                child: Text('Drug interactions'),
              ),
              DropdownMenuItem(
                value: ClinicalAiWorkflow.triage,
                child: Text('Clinical triage'),
              ),
              DropdownMenuItem(
                value: ClinicalAiWorkflow.laboratory,
                child: Text('Laboratory interpretation'),
              ),
            ],
            onChanged: _busy
                ? null
                : (value) {
                    if (value == null) return;
                    setState(() {
                      _workflow = value;
                      _suggestion = null;
                      _error = null;
                    });
                  },
          ),
          TextField(
            controller: _patientId,
            key: const ValueKey('gapai-patient-id'),
            keyboardType: TextInputType.number,
            decoration: InputDecoration(
              labelText: _workflow == ClinicalAiWorkflow.triage
                  ? 'Verified patient ID (required)'
                  : 'Verified patient ID (optional)',
            ),
          ),
          if (_workflow == ClinicalAiWorkflow.interactions)
            TextField(
              controller: _drugs,
              key: const ValueKey('gapai-drugs'),
              decoration: const InputDecoration(
                labelText: 'Drug names (comma-separated, at least two)',
              ),
            ),
          if (_workflow == ClinicalAiWorkflow.triage)
            TextField(
              controller: _symptoms,
              key: const ValueKey('gapai-symptoms'),
              maxLines: 5,
              decoration: const InputDecoration(labelText: 'Symptoms'),
            ),
          if (_workflow == ClinicalAiWorkflow.laboratory) ...[
            TextField(
              controller: _labOrderId,
              key: const ValueKey('gapai-lab-order-id'),
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: 'Lab order ID (optional)',
              ),
            ),
            TextField(
              controller: _labData,
              key: const ValueKey('gapai-lab-data'),
              maxLines: 5,
              decoration: const InputDecoration(
                labelText: 'Laboratory result JSON object',
                hintText: '{"glucose": "100 mg/dL"}',
              ),
            ),
          ],
          const SizedBox(height: 16),
          FilledButton(
            key: const ValueKey('gapai-request'),
            onPressed: _busy || !widget.guard.canAccessProtectedRoutes
                ? null
                : () => unawaited(_submit()),
            child: Text(
              _busy ? 'Request in progress' : 'Request advisory assistance',
            ),
          ),
          if (_busy) const LinearProgressIndicator(),
          if (_error != null) Text(_error!, key: const ValueKey('gapai-error')),
          if (_suggestion != null) ...[
            const Divider(),
            const Text(
              'UNVERIFIED AI SUGGESTION — NOT A CHART ENTRY',
              key: ValueKey('gapai-result-warning'),
            ),
            Text(_suggestion!.title),
            if (_suggestion!.riskLevel != null)
              Text('AI risk assessment: ${_suggestion!.riskLevel}'),
            ..._suggestion!.lines.map((line) => Text(line)),
            const Text(
              'No diagnosis, medication, admission or laboratory result '
              'has been created, verified or released.',
            ),
          ],
        ],
      ),
    );
  }
}
