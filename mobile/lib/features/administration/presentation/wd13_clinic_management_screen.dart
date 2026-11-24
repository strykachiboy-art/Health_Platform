import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/administration/data/wd13_clinic_management_remote_data_source.dart';

/// A system-scoped Super Admin workspace. No clinic selection is required.
/// The feature is production-locked until backend quota enforcement is approved.
class Wd13ClinicManagementScreen extends StatefulWidget {
  const Wd13ClinicManagementScreen({
    super.key,
    required this.source,
    this.allowUnmeteredStagingCreation = const bool.fromEnvironment(
      'WD13_ALLOW_UNMETERED_STAGING_CREATION',
    ),
    this.allowExtendedOperatingHours = const bool.fromEnvironment(
      'CLINIC_HOURS_01_STAGING_UI',
    ),
  });

  final Wd13ClinicManagementRemoteDataSource source;
  final bool allowUnmeteredStagingCreation;

  /// Staged-off independently of the server's own migration/feature gate.
  final bool allowExtendedOperatingHours;

  @override
  State<Wd13ClinicManagementScreen> createState() =>
      _Wd13ClinicManagementScreenState();
}

class _Wd13ClinicManagementScreenState
    extends State<Wd13ClinicManagementScreen> {
  final Map<String, TextEditingController> _fields = {
    for (final field in <String>[
      'name',
      'address',
      'city',
      'country',
      'phone',
      'email',
      'timezone',
      'opening_time',
      'closing_time',
    ])
      field: TextEditingController(
        text: field == 'timezone' ? 'Africa/Lagos' : '',
      ),
  };

  List<Wd13Clinic>? _clinics;
  Wd13Clinic? _detail;
  Wd13Clinic? _receipt;
  String? _error;
  String? _createNotice;
  bool _loading = true;
  bool _creating = false;
  bool _headquarters = false;
  String _type = 'general';
  String? _operatingMode;
  String _filter = 'all';
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant Wd13ClinicManagementScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source)) {
      oldWidget.source.sessionNavigationGuard.removeListener(_sessionChanged);
      widget.source.sessionNavigationGuard.addListener(_sessionChanged);
      _generation++;
      _clinics = null;
      _detail = null;
      _receipt = null;
      _operatingMode = null;
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.sessionNavigationGuard.removeListener(_sessionChanged);
    for (final field in _fields.values) {
      field.dispose();
    }
    super.dispose();
  }

  bool _current(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.source.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.source.sessionNavigationGuard.revision == revision;

  void _sessionChanged() {
    _generation++;
    if (!mounted) return;
    setState(() {
      _clinics = null;
      _detail = null;
      _receipt = null;
      _operatingMode = null;
      _loading = false;
      _creating = false;
      _error = 'Session context changed. Reopen with a verified Super Admin session.';
    });
  }

  String _describe(Object failure) {
    if (failure is Wd13ClinicMutationAlreadyAttempted) {
      return 'Creation was already attempted for this name. Check the server '
          'before starting a different request; never retry an uncertain POST.';
    }
    if (failure is Wd13ClinicAccessUnavailable) {
      return 'Super Admin session is unavailable. Sign in again.';
    }
    if (failure is ApiException) {
      if (failure.statusCode == 401 || failure.statusCode == 403) {
        return 'The backend denied this action. Verify the Super Admin role '
            'and the clinic LIST/READ/CREATE authorization gates.';
      }
      if (failure.statusCode == 409) {
        return 'The backend reported a clinic name conflict. Refresh the list.';
      }
      if (failure.statusCode == 422 || failure.statusCode == 400) {
        return failure.serverMessage ??
            'The backend rejected the clinic details.';
      }
      return 'The backend outcome could not be confirmed. Do not resubmit.';
    }
    if (failure is FormatException) return failure.message;
    return 'The backend result could not be verified. Do not resubmit.';
  }

  Future<void> _load() async {
    final generation = ++_generation;
    final revision = widget.source.sessionNavigationGuard.revision;
    setState(() {
      _loading = true;
      _clinics = null;
      _detail = null;
      _error = null;
    });
    try {
      final result = await widget.source.list(
        status: _filter == 'all' ? null : _filter,
      );
      if (!_current(generation, revision)) return;
      setState(() {
        _clinics = result;
        _loading = false;
        if (!_advancedHoursAvailable) _operatingMode = null;
      });
    } on Object catch (failure) {
      if (!_current(generation, revision)) return;
      setState(() {
        _loading = false;
        _error = _describe(failure);
      });
    }
  }

  Future<void> _openClinic(int id) async {
    final generation = _generation;
    final revision = widget.source.sessionNavigationGuard.revision;
    setState(() {
      _detail = null;
      _error = null;
    });
    try {
      final result = await widget.source.read(id);
      if (!_current(generation, revision)) return;
      setState(() => _detail = result);
    } on Object catch (failure) {
      if (!_current(generation, revision)) return;
      setState(() => _error = _describe(failure));
    }
  }

  // Mode metadata is advertised only by an enabled backend. Both gates must
  // pass; an empty list cannot establish server capability and stays legacy.
  bool get _advancedHoursAvailable =>
      widget.allowExtendedOperatingHours &&
      _clinics != null &&
      _clinics!.isNotEmpty &&
      _clinics!.every((clinic) => clinic.operatingMode != null);

  Wd13RootClinicDraft _draft() => Wd13RootClinicDraft(
    name: _fields['name']!.text,
    type: _type,
    headquarters: _headquarters,
    timezone: _fields['timezone']!.text,
    address: _fields['address']!.text,
    city: _fields['city']!.text,
    country: _fields['country']!.text,
    phone: _fields['phone']!.text,
    email: _fields['email']!.text,
    openingTime:
        _advancedHoursAvailable &&
            (_operatingMode == 'always_open' || _operatingMode == 'unspecified')
        ? ''
        : _fields['opening_time']!.text,
    closingTime:
        _advancedHoursAvailable &&
            (_operatingMode == 'always_open' || _operatingMode == 'unspecified')
        ? ''
        : _fields['closing_time']!.text,
    operatingMode: _advancedHoursAvailable ? _operatingMode : null,
  );

  Future<void> _create() async {
    if (_creating ||
        _loading ||
        _clinics == null ||
        !widget.allowUnmeteredStagingCreation) {
      return;
    }
    final Wd13RootClinicDraft draft;
    try {
      draft = _draft();
      draft.toBackendJson();
    } on FormatException catch (failure) {
      setState(() => _error = failure.message);
      return;
    }
    final generation = _generation;
    final revision = widget.source.sessionNavigationGuard.revision;
    final approved = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Create root clinic?'),
        content: Text(
          'Create "${draft.name.trim()}" as a root clinic? '
          'This sends exactly one live POST. The backend does not yet '
          'enforce purchased-plan clinic quotas; use staging only.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('wd13-confirm-create'),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Confirm one creation'),
          ),
        ],
      ),
    );
    if (approved != true ||
        !_current(generation, revision) ||
        _loading ||
        _creating ||
        _clinics == null) {
      return;
    }
    setState(() {
      _creating = true;
      _receipt = null;
      _error = null;
      _createNotice = null;
    });
    try {
      final result = await widget.source.createRoot(draft);
      if (!_current(generation, revision)) return;
      setState(() {
        _receipt = result;
        _createNotice =
            'Flask confirmed root clinic #${result.id}. '
            'This result was not queued or retried.';
        _creating = false;
      });
      // A separate GET provides readback. No further POST is ever attempted.
      try {
        final verification = await widget.source.read(result.id);
        if (!_current(generation, revision)) return;
        if (verification.id != result.id ||
            verification.name != result.name ||
            verification.parentId != null) {
          throw const FormatException('Clinic readback mismatch.');
        }
        setState(() => _detail = verification);
      } on Object {
        if (!_current(generation, revision)) return;
        setState(
          () => _createNotice =
              'Flask accepted clinic #${result.id}, '
              'but GET readback could not be verified. Check backend manually; '
              'do not resubmit.',
        );
      }
    } on Object catch (failure) {
      if (!_current(generation, revision)) return;
      setState(() {
        _error = _describe(failure);
        _creating = false;
      });
    }
  }

  Widget _field(String name, String label, {int lines = 1}) => TextField(
    controller: _fields[name],
    key: ValueKey('wd13-input-$name'),
    maxLines: lines,
    enabled: !_creating,
    decoration: InputDecoration(
      labelText: label,
      border: const OutlineInputBorder(),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final canCreate =
        widget.allowUnmeteredStagingCreation &&
        !_creating &&
        !_loading &&
        _clinics != null &&
        widget.source.sessionNavigationGuard.canAccessProtectedRoutes;
    return Scaffold(
      appBar: AppBar(title: const Text('Super Admin · Root clinics')),
      body: ListView(
        key: const ValueKey('wd13-clinic-workspace'),
        padding: const EdgeInsets.all(20),
        children: [
          const Text(
            'System-level clinic registry. The backend authorizes '
            'each GET and POST; clinic switching is a separate workflow.',
          ),
          const SizedBox(height: 12),
          const Text(
            'Plan-based clinic quotas are not yet enforced by Flask. '
            'Production creation is locked pending the entitlement contract.',
            key: ValueKey('wd13-quota-warning'),
          ),
          const SizedBox(height: 20),
          Text(
            'Registered clinics',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          Row(
            children: [
              const Text('Status: '),
              DropdownButton<String>(
                value: _filter,
                items: const [
                  DropdownMenuItem(value: 'all', child: Text('All')),
                  DropdownMenuItem(value: 'active', child: Text('Active')),
                  DropdownMenuItem(value: 'inactive', child: Text('Inactive')),
                  DropdownMenuItem(
                    value: 'suspended',
                    child: Text('Suspended'),
                  ),
                ],
                onChanged: _loading || _creating
                    ? null
                    : (value) {
                        if (value == null) return;
                        setState(() => _filter = value);
                        unawaited(_load());
                      },
              ),
              const SizedBox(width: 12),
              OutlinedButton(
                key: const ValueKey('wd13-refresh-clinics'),
                onPressed: _loading || _creating
                    ? null
                    : () => unawaited(_load()),
                child: const Text('Refresh'),
              ),
            ],
          ),
          if (_loading) const LinearProgressIndicator(),
          if (_error != null)
            Text(_error!, key: const ValueKey('wd13-clinic-error')),
          if (_clinics != null) ...[
            Text('Server records: ${_clinics!.length}'),
            if (_clinics!.isEmpty) const Text('No clinics returned.'),
            for (final clinic in _clinics!)
              ListTile(
                key: ValueKey('wd13-clinic-${clinic.id}'),
                title: Text(clinic.name),
                subtitle: Text(
                  '#${clinic.id} · ${clinic.type} · ${clinic.status}'
                  '${clinic.parentId == null ? ' · Root' : ' · Branch'}',
                ),
                onTap: _creating
                    ? null
                    : () => unawaited(_openClinic(clinic.id)),
              ),
          ],
          if (_detail != null) ...[
            const Divider(),
            Text(
              'Verified clinic: ${_detail!.name} (#${_detail!.id})',
              key: const ValueKey('wd13-detail'),
            ),
            Text(
              'Status: ${_detail!.status} · ${_detail!.type} · '
              'HQ: ${_detail!.headquarters} · TZ: ${_detail!.timezone}',
            ),
            Text(
              'Address: ${_detail!.address ?? '—'} · '
              '${_detail!.city ?? '—'}, ${_detail!.country ?? '—'}',
            ),
            Text(
              'Contact: ${_detail!.phone ?? '—'} · ${_detail!.email ?? '—'}',
            ),
            Text(
              _detail!.operatingMode == 'always_open'
                  ? 'Hours: Open 24/7'
                  : _detail!.operatingMode == 'unspecified'
                  ? 'Hours: Not configured'
                  : 'Hours: ${_detail!.openingTime ?? '—'} - '
                        '${_detail!.closingTime ?? '—'}',
            ),
            if (_detail!.hoursStatus != null)
              Text(
                _detail!.hoursStatus!.summary,
                key: const ValueKey('clinic-hours-02-registry-status'),
              ),
          ],
          const Divider(height: 35),
          Text(
            'Create a root clinic',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          if (!widget.allowUnmeteredStagingCreation)
            const Text(
              'Creation disabled: backend purchased-plan quota '
              'contract is not yet implemented. No bypass is provided.',
              key: ValueKey('wd13-create-locked'),
            ),
          if (widget.allowUnmeteredStagingCreation && _clinics == null)
            const Text(
              'Creation requires a successful Super Admin clinic '
              'LIST response first. Resolve backend RBAC before proceeding.',
            ),
          const SizedBox(height: 12),
          _field('name', 'Clinic name (required)'),
          const SizedBox(height: 10),
          DropdownButton<String>(
            value: _type,
            items: const [
              DropdownMenuItem(value: 'general', child: Text('General')),
              DropdownMenuItem(value: 'specialist', child: Text('Specialist')),
              DropdownMenuItem(value: 'dental', child: Text('Dental')),
              DropdownMenuItem(
                value: 'diagnostic_center',
                child: Text('Diagnostic center'),
              ),
              DropdownMenuItem(
                value: 'pharmacy_only',
                child: Text('Pharmacy only'),
              ),
            ],
            onChanged: _creating
                ? null
                : (value) {
                    if (value != null) setState(() => _type = value);
                  },
          ),
          SwitchListTile(
            title: const Text('Headquarters'),
            value: _headquarters,
            onChanged: _creating
                ? null
                : (value) => setState(() => _headquarters = value),
          ),
          const Text(
            'Parent clinic: none (root clinic). Branch creation remains '
            'an Admin-only backend workflow.',
          ),
          const SizedBox(height: 10),
          _field('address', 'Address (optional)'),
          const SizedBox(height: 10),
          _field('city', 'City (optional)'),
          const SizedBox(height: 10),
          _field('country', 'Country (optional)'),
          const SizedBox(height: 10),
          _field('phone', 'Phone (optional)'),
          const SizedBox(height: 10),
          _field('email', 'Email (optional)'),
          const SizedBox(height: 10),
          _field('timezone', 'IANA timezone (required)'),
          const SizedBox(height: 10),
          if (_advancedHoursAvailable) ...[
            DropdownButton<String>(
              key: const ValueKey('clinic-hours-01-mode-selector'),
              value: _operatingMode ?? 'legacy',
              items: const [
                DropdownMenuItem(
                  value: 'legacy',
                  child: Text('Legacy operating hours'),
                ),
                DropdownMenuItem(
                  value: 'unspecified',
                  child: Text('Hours not configured'),
                ),
                DropdownMenuItem(
                  value: 'always_open',
                  child: Text('Open 24/7'),
                ),
                DropdownMenuItem(
                  value: 'scheduled',
                  child: Text('Scheduled / overnight'),
                ),
              ],
              onChanged: _creating
                  ? null
                  : (value) => setState(() {
                      _operatingMode = value == 'legacy' ? null : value;
                      if (_operatingMode == 'always_open' ||
                          _operatingMode == 'unspecified') {
                        _fields['opening_time']!.clear();
                        _fields['closing_time']!.clear();
                      }
                    }),
            ),
            const Text(
              'Clinic hours describe routine availability; they never disable '
              'login, emergency care, or overnight staff access.',
              key: ValueKey('clinic-hours-01-availability-note'),
            ),
            const SizedBox(height: 10),
          ] else
            const Text(
              'Extended clinic hours are not enabled. Legacy hours remain available.',
              key: ValueKey('clinic-hours-01-staged-off'),
            ),
          if (_operatingMode != 'always_open' &&
              _operatingMode != 'unspecified') ...[
            _field('opening_time', 'Opening time HH:mm (optional)'),
            const SizedBox(height: 10),
            _field('closing_time', 'Closing time HH:mm (optional)'),
          ],
          const SizedBox(height: 14),
          FilledButton(
            key: const ValueKey('wd13-create-root-clinic'),
            onPressed: canCreate ? () => unawaited(_create()) : null,
            child: const Text('Create root clinic (one request)'),
          ),
          if (_creating) const LinearProgressIndicator(),
          if (_receipt != null)
            Text(
              'Server accepted: ${_receipt!.name} (#${_receipt!.id})',
              key: const ValueKey('wd13-create-receipt'),
            ),
          if (_createNotice != null) Text(_createNotice!),
        ],
      ),
    );
  }
}
