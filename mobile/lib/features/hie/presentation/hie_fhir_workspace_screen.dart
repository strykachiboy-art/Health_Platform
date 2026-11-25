import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/hie/data/hie_remote_data_source.dart';
import 'package:clinic_system_pro/features/hie/data/models/hie_models.dart';

/// Online-only HIE / FHIR R4 workspace.
///
/// Mirrors the backend's role boundaries:
/// * `HIE_VIEW_ROLES` (`admin`, `doctor`, `nurse`, `lab_technician`,
///   `pharmacist`) can query external FHIR servers and inspect audit logs.
/// * `admin` only can configure `fhir-r4` integrations and export patient FHIR
///   R4 collection bundles.
///
/// Never caches PHI locally and clears all loaded state immediately when the
/// session guard invalidates or changes.
class HieFhirWorkspaceScreen extends StatefulWidget {
  const HieFhirWorkspaceScreen({
    super.key,
    required this.dataSource,
    this.initialPatientId,
  });

  final HieRemoteDataSource dataSource;
  final int? initialPatientId;

  @override
  State<HieFhirWorkspaceScreen> createState() => _HieFhirWorkspaceScreenState();
}

class _HieFhirWorkspaceScreenState extends State<HieFhirWorkspaceScreen> {
  final _queryIdentifierController = TextEditingController();
  final _queryIntegrationIdController = TextEditingController();
  final _exportPatientIdController = TextEditingController();
  final _integrationLookupIdController = TextEditingController();
  final _providerController = TextEditingController(text: 'fhir-r4');
  final _endpointUrlController = TextEditingController();
  final _organizationIdController = TextEditingController();
  final _facilityIdController = TextEditingController();

  bool _loading = true;
  bool _authorized = false;
  bool _isAdmin = false;
  bool _busy = false;
  int _generation = 0;
  int _selectedSectionIndex = 0;

  String _purposeOfUse = 'treatment';
  String _queryMode = 'patient';
  String _resourceType = 'Observation';
  String _exportSection = 'full';
  String _updateStatus = 'active';

  String? _statusMessage;
  HieQueryResult? _queryResult;
  FhirExportBundle? _exportBundle;
  HieIntegration? _integration;
  HieSubmissionPage? _submissionPage;

  @override
  void initState() {
    super.initState();
    if (widget.initialPatientId != null && widget.initialPatientId! > 0) {
      _exportPatientIdController.text = '${widget.initialPatientId}';
      _queryIdentifierController.text = '${widget.initialPatientId}';
    }
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
    unawaited(_authorize());
  }

  @override
  void didUpdateWidget(covariant HieFhirWorkspaceScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.dataSource, widget.dataSource) ||
        oldWidget.initialPatientId != widget.initialPatientId) {
      oldWidget.dataSource.sessionNavigationGuard.removeListener(
        _onSessionChanged,
      );
      widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
      _clearSensitiveState();
      if (widget.initialPatientId != null && widget.initialPatientId! > 0) {
        _exportPatientIdController.text = '${widget.initialPatientId}';
        _queryIdentifierController.text = '${widget.initialPatientId}';
      }
      unawaited(_authorize());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onSessionChanged);
    for (final c in <TextEditingController>[
      _queryIdentifierController,
      _queryIntegrationIdController,
      _exportPatientIdController,
      _integrationLookupIdController,
      _providerController,
      _endpointUrlController,
      _organizationIdController,
      _facilityIdController,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  void _clearSensitiveState() {
    _generation++;
    _queryIdentifierController.clear();
    _queryIntegrationIdController.clear();
    _exportPatientIdController.clear();
    _integrationLookupIdController.clear();
    _endpointUrlController.clear();
    _organizationIdController.clear();
    _facilityIdController.clear();
    _queryResult = null;
    _exportBundle = null;
    _integration = null;
    _submissionPage = null;
    _statusMessage = null;
    _authorized = false;
    _isAdmin = false;
    _busy = false;
    _loading = false;
  }

  void _onSessionChanged() {
    _clearSensitiveState();
    if (!mounted) return;
    setState(() {});
    if (widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_authorize());
    }
  }

  Future<void> _authorize() async {
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_generation;

    if (!mounted) return;
    setState(() {
      _loading = true;
      _authorized = false;
      _isAdmin = false;
      _statusMessage = null;
    });

    if (!guard.canAccessProtectedRoutes) {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
      return;
    }

    try {
      final session = await widget.dataSource.authorizedSession();
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _loading = false;
        _authorized = true;
        _isAdmin = session.role.trim().toLowerCase() == 'admin';
      });
    } on HieSessionException {
      if (!mounted || generation != _generation) return;
      setState(() {
        _loading = false;
        _authorized = false;
        _statusMessage = 'HIE / FHIR access is unavailable for this role or session.';
      });
    } on Object {
      if (!mounted || generation != _generation) return;
      setState(() {
        _loading = false;
        _authorized = false;
        _statusMessage = 'Unable to verify HIE / FHIR session.';
      });
    }
  }

  String _describeError(Object error) {
    if (error is HieSessionException) {
      return 'Session changed or unauthorized. Sign in again.';
    }
    if (error is ApiException) {
      if (error.serverMessage != null && error.serverMessage!.trim().isNotEmpty) {
        return error.serverMessage!;
      }
      return error.message;
    }
    if (error is ArgumentError) {
      return 'Check input value for ${error.name ?? 'field'}.';
    }
    if (error is FormatException) {
      return error.message;
    }
    return 'HIE / FHIR operation failed.';
  }

  Future<void> _runQuery() async {
    if (_busy || !_authorized) return;
    final identifier = _queryIdentifierController.text.trim();
    if (identifier.isEmpty) {
      setState(() => _statusMessage = 'Enter a patient identifier to query.');
      return;
    }

    int? integrationId;
    final rawIntegrationId = _queryIntegrationIdController.text.trim();
    if (rawIntegrationId.isNotEmpty) {
      integrationId = int.tryParse(rawIntegrationId);
      if (integrationId == null || integrationId <= 0) {
        setState(() => _statusMessage = 'Integration ID must be a positive integer.');
        return;
      }
    }

    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;

    setState(() {
      _busy = true;
      _statusMessage = null;
      _queryResult = null;
    });

    try {
      final result = _queryMode == 'patient'
          ? await widget.dataSource.queryPatient(
              patientIdentifier: identifier,
              purposeOfUse: _purposeOfUse,
              integrationId: integrationId,
            )
          : await widget.dataSource.queryClinicalData(
              patientIdentifier: identifier,
              purposeOfUse: _purposeOfUse,
              resourceType: _resourceType,
              integrationId: integrationId,
            );
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _busy = false;
        _queryResult = result;
        _statusMessage =
            'Returned ${result.records.length} FHIR record(s) (HTTP ${result.statusCode}).';
      });
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _busy = false;
        _statusMessage = _describeError(error);
      });
    }
  }

  Future<void> _runExport() async {
    if (_busy || !_authorized || !_isAdmin) return;
    final patientId = int.tryParse(_exportPatientIdController.text.trim());
    if (patientId == null || patientId <= 0) {
      setState(() => _statusMessage = 'Enter a valid positive patient ID.');
      return;
    }

    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;

    setState(() {
      _busy = true;
      _statusMessage = null;
      _exportBundle = null;
    });

    try {
      final bundle = await widget.dataSource.exportPatientFhirBundle(
        patientId: patientId,
        section: _exportSection,
      );
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _busy = false;
        _exportBundle = bundle;
        _statusMessage =
            'Exported FHIR R4 ${bundle.type} Bundle with ${bundle.total} resource(s).';
      });
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _busy = false;
        _statusMessage = _describeError(error);
      });
    }
  }

  Future<void> _createIntegration() async {
    if (_busy || !_authorized || !_isAdmin) return;
    final provider = _providerController.text.trim();
    if (provider.isEmpty) {
      setState(() => _statusMessage = 'Provider name is required (e.g. fhir-r4).');
      return;
    }

    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;

    setState(() {
      _busy = true;
      _statusMessage = null;
    });

    try {
      final integration = await widget.dataSource.createIntegration(
        provider: provider,
        endpointUrl: _endpointUrlController.text.trim().isEmpty
            ? null
            : _endpointUrlController.text.trim(),
        organizationId: _organizationIdController.text.trim().isEmpty
            ? null
            : _organizationIdController.text.trim(),
        facilityId: _facilityIdController.text.trim().isEmpty
            ? null
            : _facilityIdController.text.trim(),
      );
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _busy = false;
        _integration = integration;
        _integrationLookupIdController.text = '${integration.id}';
        _statusMessage =
            'Created integration #${integration.id} (${integration.provider}, ${integration.status}).';
      });
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _busy = false;
        _statusMessage = _describeError(error);
      });
    }
  }

  Future<void> _lookupIntegration() async {
    if (_busy || !_authorized) return;
    final id = int.tryParse(_integrationLookupIdController.text.trim());
    if (id == null || id <= 0) {
      setState(() => _statusMessage = 'Enter a valid integration ID.');
      return;
    }

    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;

    setState(() {
      _busy = true;
      _statusMessage = null;
    });

    try {
      final integration = await widget.dataSource.fetchIntegration(
        integrationId: id,
      );
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _busy = false;
        _integration = integration;
        _updateStatus = integration.status;
        _statusMessage =
            'Loaded integration #${integration.id} (${integration.status}).';
      });
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _busy = false;
        _statusMessage = _describeError(error);
      });
    }
  }

  Future<void> _updateIntegrationStatus() async {
    if (_busy || !_authorized || !_isAdmin || _integration == null) return;

    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;

    setState(() {
      _busy = true;
      _statusMessage = null;
    });

    try {
      final updated = await widget.dataSource.updateIntegration(
        integrationId: _integration!.id,
        status: _updateStatus,
      );
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _busy = false;
        _integration = updated;
        _statusMessage =
            'Updated integration #${updated.id} status to ${updated.status}.';
      });
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _busy = false;
        _statusMessage = _describeError(error);
      });
    }
  }

  Future<void> _loadSubmissions() async {
    if (_busy || !_authorized) return;

    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;

    setState(() {
      _busy = true;
      _statusMessage = null;
    });

    try {
      final page = await widget.dataSource.fetchSubmissions(
        integrationId: _integration?.id,
      );
      if (!mounted ||
          generation != _generation ||
          guard.revision != revision ||
          !guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _busy = false;
        _submissionPage = page;
        _statusMessage = 'Loaded ${page.items.length} of ${page.total} audit record(s).';
      });
    } on Object catch (error) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _busy = false;
        _statusMessage = _describeError(error);
      });
    }
  }

  Future<void> _copyExportJson() async {
    final bundle = _exportBundle;
    if (bundle == null) return;
    const encoder = JsonEncoder.withIndent('  ');
    await Clipboard.setData(ClipboardData(text: encoder.convert(bundle.rawBundle)));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('FHIR R4 Bundle JSON copied to clipboard.')),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('HIE / FHIR R4')),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(
        key: ValueKey('hie-loading'),
        child: CircularProgressIndicator(),
      );
    }

    if (!_authorized) {
      return Center(
        key: const ValueKey('hie-unauthorized'),
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(
            _statusMessage ?? 'HIE / FHIR access is unavailable.',
            textAlign: TextAlign.center,
          ),
        ),
      );
    }

    return Column(
      key: const ValueKey('hie-workspace'),
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: SegmentedButton<int>(
            segments: <ButtonSegment<int>>[
              const ButtonSegment<int>(
                value: 0,
                label: Text('FHIR Query'),
                icon: Icon(Icons.search),
              ),
              if (_isAdmin)
                const ButtonSegment<int>(
                  value: 1,
                  label: Text('FHIR Export'),
                  icon: Icon(Icons.upload_file),
                ),
              const ButtonSegment<int>(
                value: 2,
                label: Text('Integrations'),
                icon: Icon(Icons.settings_ethernet),
              ),
            ],
            selected: <int>{_selectedSectionIndex},
            onSelectionChanged: (selection) {
              setState(() {
                _selectedSectionIndex = selection.first;
                _statusMessage = null;
              });
            },
          ),
        ),
        if (_statusMessage != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            child: Material(
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(8),
              child: Padding(
                padding: const EdgeInsets.all(10),
                child: Row(
                  children: [
                    const Icon(Icons.info_outline, size: 18),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        _statusMessage!,
                        key: const ValueKey('hie-status-message'),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        if (_busy) const LinearProgressIndicator(),
        Expanded(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(16),
            child: switch (_selectedSectionIndex) {
              0 => _buildQueryTab(),
              1 when _isAdmin => _buildExportTab(),
              _ => _buildIntegrationsTab(),
            },
          ),
        ),
      ],
    );
  }

  Widget _buildQueryTab() {
    return Column(
      key: const ValueKey('hie-query-tab'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          key: const ValueKey('hie-query-identifier-field'),
          controller: _queryIdentifierController,
          decoration: const InputDecoration(
            labelText: 'External Patient Identifier',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: DropdownButtonFormField<String>(
                key: const ValueKey('hie-query-mode-dropdown'),
                isExpanded: true,
                initialValue: _queryMode,
                decoration: const InputDecoration(
                  labelText: 'Query Mode',
                  border: OutlineInputBorder(),
                ),
                items: const [
                  DropdownMenuItem(
                    value: 'patient',
                    child: Text('Patient', overflow: TextOverflow.ellipsis),
                  ),
                  DropdownMenuItem(
                    value: 'clinical',
                    child: Text(
                      'Clinical Data',
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
                onChanged: (v) {
                  if (v != null) setState(() => _queryMode = v);
                },
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: DropdownButtonFormField<String>(
                key: const ValueKey('hie-purpose-dropdown'),
                isExpanded: true,
                initialValue: _purposeOfUse,
                decoration: const InputDecoration(
                  labelText: 'Purpose of Use',
                  border: OutlineInputBorder(),
                ),
                items: hiePurposesOfUse
                    .map(
                      (p) => DropdownMenuItem(
                        value: p,
                        child: Text(p, overflow: TextOverflow.ellipsis),
                      ),
                    )
                    .toList(),
                onChanged: (v) {
                  if (v != null) setState(() => _purposeOfUse = v);
                },
              ),
            ),
          ],
        ),
        if (_queryMode == 'clinical') ...[
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            key: const ValueKey('hie-resource-type-dropdown'),
            isExpanded: true,
            initialValue: _resourceType,
            decoration: const InputDecoration(
              labelText: 'FHIR R4 Resource Type',
              border: OutlineInputBorder(),
            ),
            items: fhirReadOnlyResourceTypes
                .map(
                  (r) => DropdownMenuItem(
                    value: r,
                    child: Text(r, overflow: TextOverflow.ellipsis),
                  ),
                )
                .toList(),
            onChanged: (v) {
              if (v != null) setState(() => _resourceType = v);
            },
          ),
        ],
        const SizedBox(height: 12),
        TextField(
          key: const ValueKey('hie-query-integration-id-field'),
          controller: _queryIntegrationIdController,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(
            labelText: 'Integration ID (optional)',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        FilledButton.icon(
          key: const ValueKey('hie-run-query-button'),
          onPressed: _busy ? null : _runQuery,
          icon: const Icon(Icons.cloud_download_outlined),
          label: const Text('Query External FHIR'),
        ),
        if (_queryResult != null) ...[
          const SizedBox(height: 16),
          Text(
            'Results (${_queryResult!.records.length})',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          if (_queryResult!.externalReference != null)
            Text('External Reference: ${_queryResult!.externalReference}'),
          const SizedBox(height: 8),
          if (_queryResult!.records.isEmpty)
            const Card(
              child: Padding(
                padding: EdgeInsets.all(16),
                child: Text('No matching FHIR resources returned.'),
              ),
            )
          else
            ..._queryResult!.records.map(
              (record) => Card(
                child: ListTile(
                  title: Text(
                    '${record['resourceType'] ?? 'Resource'} / ${record['id'] ?? '-'}',
                  ),
                  subtitle: Text(
                    jsonEncode(record),
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ),
            ),
        ],
      ],
    );
  }

  Widget _buildExportTab() {
    const encoder = JsonEncoder.withIndent('  ');
    return Column(
      key: const ValueKey('hie-export-tab'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          key: const ValueKey('hie-export-patient-id-field'),
          controller: _exportPatientIdController,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(
            labelText: 'Clinic Patient ID',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(
          key: const ValueKey('hie-export-section-dropdown'),
          isExpanded: true,
          initialValue: _exportSection,
          decoration: const InputDecoration(
            labelText: 'Export Scope',
            border: OutlineInputBorder(),
          ),
          items: fhirExportSections
              .map(
                (s) => DropdownMenuItem(
                  value: s,
                  child: Text(s, overflow: TextOverflow.ellipsis),
                ),
              )
              .toList(),
          onChanged: (v) {
            if (v != null) setState(() => _exportSection = v);
          },
        ),
        const SizedBox(height: 12),
        FilledButton.icon(
          key: const ValueKey('hie-run-export-button'),
          onPressed: _busy ? null : _runExport,
          icon: const Icon(Icons.download_outlined),
          label: const Text('Export FHIR R4 Bundle'),
        ),
        if (_exportBundle != null) ...[
          const SizedBox(height: 16),
          Card(
            key: const ValueKey('hie-export-summary-card'),
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Expanded(
                        child: Text(
                          'Bundle (${_exportBundle!.type}) — ${_exportBundle!.total} resource(s)',
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                      ),
                      TextButton.icon(
                        key: const ValueKey('hie-copy-export-json-button'),
                        onPressed: _copyExportJson,
                        icon: const Icon(Icons.copy, size: 18),
                        label: const Text('Copy JSON'),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  ..._exportBundle!.resources.map(
                    (r) => Padding(
                      padding: const EdgeInsets.symmetric(vertical: 2),
                      child: Text(
                        '• ${r['resourceType']} (${r['id'] ?? '-'})',
                      ),
                    ),
                  ),
                  const Divider(),
                  SelectableText(
                    encoder.convert(_exportBundle!.rawBundle),
                    key: const ValueKey('hie-export-json-preview'),
                    style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
                  ),
                ],
              ),
            ),
          ),
        ],
      ],
    );
  }

  Widget _buildIntegrationsTab() {
    return Column(
      key: const ValueKey('hie-integrations-tab'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (_isAdmin) ...[
          Text(
            'Register HIE / FHIR Integration',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 8),
          TextField(
            key: const ValueKey('hie-provider-field'),
            controller: _providerController,
            decoration: const InputDecoration(
              labelText: 'Provider (e.g. fhir-r4)',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 8),
          TextField(
            key: const ValueKey('hie-endpoint-url-field'),
            controller: _endpointUrlController,
            decoration: const InputDecoration(
              labelText: 'FHIR R4 Base URL (must be allowlisted)',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: TextField(
                  key: const ValueKey('hie-org-id-field'),
                  controller: _organizationIdController,
                  decoration: const InputDecoration(
                    labelText: 'Organization ID (optional)',
                    border: OutlineInputBorder(),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: TextField(
                  key: const ValueKey('hie-facility-id-field'),
                  controller: _facilityIdController,
                  decoration: const InputDecoration(
                    labelText: 'Facility ID (optional)',
                    border: OutlineInputBorder(),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          FilledButton(
            key: const ValueKey('hie-create-integration-button'),
            onPressed: _busy ? null : _createIntegration,
            child: const Text('Create Integration'),
          ),
          const Divider(height: 28),
        ],
        Row(
          children: [
            Expanded(
              child: TextField(
                key: const ValueKey('hie-lookup-integration-id-field'),
                controller: _integrationLookupIdController,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                  labelText: 'Integration ID',
                  border: OutlineInputBorder(),
                ),
              ),
            ),
            const SizedBox(width: 8),
            FilledButton.tonal(
              key: const ValueKey('hie-lookup-integration-button'),
              onPressed: _busy ? null : _lookupIntegration,
              child: const Text('Load'),
            ),
          ],
        ),
        if (_integration != null) ...[
          const SizedBox(height: 12),
          Card(
            key: const ValueKey('hie-integration-detail-card'),
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Integration #${_integration!.id} — ${_integration!.provider}',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                  Text('Status: ${_integration!.status}'),
                  if (_integration!.endpointUrl != null)
                    Text('Endpoint: ${_integration!.endpointUrl}'),
                  if (_isAdmin) ...[
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Expanded(
                          child: DropdownButtonFormField<String>(
                            key: const ValueKey('hie-update-status-dropdown'),
                            isExpanded: true,
                            initialValue: _updateStatus,
                            decoration: const InputDecoration(
                              labelText: 'New Status',
                              border: OutlineInputBorder(),
                            ),
                            items: hieIntegrationStatuses
                                .map(
                                  (s) => DropdownMenuItem(
                                    value: s,
                                    child: Text(
                                      s,
                                      overflow: TextOverflow.ellipsis,
                                    ),
                                  ),
                                )
                                .toList(),
                            onChanged: (v) {
                              if (v != null) setState(() => _updateStatus = v);
                            },
                          ),
                        ),
                        const SizedBox(width: 8),
                        FilledButton(
                          key: const ValueKey('hie-update-status-button'),
                          onPressed: _busy ? null : _updateIntegrationStatus,
                          child: const Text('Update'),
                        ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
        ],
        const SizedBox(height: 16),
        OutlinedButton.icon(
          key: const ValueKey('hie-load-submissions-button'),
          onPressed: _busy ? null : _loadSubmissions,
          icon: const Icon(Icons.history),
          label: const Text('Load HIE Submission Audit Log'),
        ),
        if (_submissionPage != null) ...[
          const SizedBox(height: 12),
          if (_submissionPage!.items.isEmpty)
            const Text('No HIE submissions recorded yet.')
          else
            ..._submissionPage!.items.map(
              (s) => Card(
                child: ListTile(
                  title: Text('#${s.id} • ${s.operation} (${s.status})'),
                  subtitle: Text(
                    'Integration ${s.integrationId}'
                    '${s.externalReference != null ? ' • Ref: ${s.externalReference}' : ''}'
                    '${s.failureClass != null ? ' • Failure: ${s.failureClass}' : ''}',
                  ),
                ),
              ),
            ),
        ],
      ],
    );
  }
}
