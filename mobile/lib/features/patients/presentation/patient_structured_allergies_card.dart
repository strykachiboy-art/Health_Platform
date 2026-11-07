import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_allergy.dart';
import 'package:clinic_system_pro/features/patients/data/patient_allergy_remote_data_source.dart';

/// Read-only structured allergy surface for an already verified patient.
///
/// Never substitutes legacy free-text allergies for the structured registry.
/// In-memory results are cleared on access failure or session transition.
class PatientStructuredAllergiesCard extends StatefulWidget {
  const PatientStructuredAllergiesCard({
    super.key,
    required this.patientId,
    required this.dataSource,
    this.pageSize = 20,
  }) : assert(patientId > 0),
       assert(pageSize > 0 && pageSize <= 500);

  final int patientId;
  final PatientAllergyRemoteDataSource dataSource;
  final int pageSize;

  @override
  State<PatientStructuredAllergiesCard> createState() =>
      _PatientStructuredAllergiesCardState();
}

class _PatientStructuredAllergiesCardState
    extends State<PatientStructuredAllergiesCard> {
  List<PatientAllergy> _items = const [];
  int? _total;
  int _page = 0;
  int _generation = 0;
  bool _loading = false;
  String? _failure;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_onGuardChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_load());
    });
  }

  @override
  void didUpdateWidget(covariant PatientStructuredAllergiesCard oldWidget) {
    super.didUpdateWidget(oldWidget);

    if (oldWidget.patientId == widget.patientId &&
        identical(oldWidget.dataSource, widget.dataSource) &&
        oldWidget.pageSize == widget.pageSize) {
      return;
    }

    oldWidget.dataSource.sessionNavigationGuard.removeListener(_onGuardChanged);
    widget.dataSource.sessionNavigationGuard.addListener(_onGuardChanged);

    _generation++;
    _items = const [];
    _total = null;
    _page = 0;
    _failure = null;
    _loading = false;

    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_load());
    });
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onGuardChanged);
    super.dispose();
  }

  void _onGuardChanged() {
    if (!mounted) return;

    _generation++;

    setState(() {
      _items = const [];
      _total = null;
      _page = 0;
      _loading = false;
      _failure =
          widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes
          ? null
          : 'Session unavailable. Allergy records have been cleared.';
    });

    if (widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  Future<void> _load({bool nextPage = false}) async {
    if (_loading) return;

    final source = widget.dataSource;
    final guard = source.sessionNavigationGuard;

    if (!guard.canAccessProtectedRoutes) {
      setState(() {
        _items = const [];
        _total = null;
        _page = 0;
        _failure = 'Session unavailable. Allergy records have been cleared.';
      });
      return;
    }

    final patientId = widget.patientId;
    final revision = guard.revision;
    final generation = ++_generation;
    final requestedPage = nextPage ? _page + 1 : 1;

    setState(() {
      _loading = true;
      _failure = null;

      if (!nextPage) {
        _items = const [];
        _total = null;
        _page = 0;
      }
    });

    bool stillCurrent() =>
        mounted &&
        generation == _generation &&
        identical(source, widget.dataSource) &&
        patientId == widget.patientId &&
        guard.canAccessProtectedRoutes &&
        guard.revision == revision;

    try {
      final result = await source.fetchAllergies(
        patientId: patientId,
        page: requestedPage,
        perPage: widget.pageSize,
        activeOnly: false,
        includeRefuted: true,
      );

      if (!stillCurrent()) return;

      final combined = nextPage
          ? <PatientAllergy>[..._items, ...result.items]
          : <PatientAllergy>[...result.items];

      if (combined.length > result.total ||
          combined.map((item) => item.id).toSet().length != combined.length) {
        throw const FormatException('Invalid combined allergy pages.');
      }

      setState(() {
        _items = List<PatientAllergy>.unmodifiable(combined);
        _total = result.total;
        _page = result.page;
        _loading = false;
      });
    } catch (error) {
      if (!stillCurrent()) return;

      setState(() {
        _items = const [];
        _total = null;
        _page = 0;
        _loading = false;
        _failure = _safeFailure(error);
      });
    }
  }

  String _safeFailure(Object error) {
    if (error is PatientAllergySessionMismatchException) {
      return 'Session unavailable. Allergy records have been cleared.';
    }

    if (error is ApiException) {
      if (error.statusCode == 401 ||
          error.statusCode == 403 ||
          error.statusCode == 404 ||
          error.statusCode == 422) {
        return 'Access denied or patient allergy records unavailable.';
      }
      return 'Structured allergy records are temporarily unavailable.';
    }

    return 'Structured allergy records could not be verified.';
  }

  String _severity(PatientAllergySeverity severity) => switch (severity) {
    PatientAllergySeverity.unknown => 'Unknown',
    PatientAllergySeverity.mild => 'Mild',
    PatientAllergySeverity.moderate => 'Moderate',
    PatientAllergySeverity.severe => 'Severe',
    PatientAllergySeverity.lifeThreatening => 'Life-threatening',
  };

  String _clinicalStatus(PatientAllergyClinicalStatus status) =>
      switch (status) {
        PatientAllergyClinicalStatus.active => 'Active',
        PatientAllergyClinicalStatus.inactive => 'Inactive',
        PatientAllergyClinicalStatus.resolved => 'Resolved',
      };

  String _verification(PatientAllergyVerificationStatus status) =>
      switch (status) {
        PatientAllergyVerificationStatus.unconfirmed => 'Unconfirmed',
        PatientAllergyVerificationStatus.confirmed => 'Confirmed',
        PatientAllergyVerificationStatus.refuted => 'Refuted',
      };

  Widget _allergyRow(PatientAllergy item) {
    final warning =
        item.clinicalStatus == PatientAllergyClinicalStatus.active &&
        item.verificationStatus != PatientAllergyVerificationStatus.refuted &&
        (item.severity == PatientAllergySeverity.severe ||
            item.severity == PatientAllergySeverity.lifeThreatening);

    final details = <String>[
      'Severity: ${_severity(item.severity)}',
      'Clinical: ${_clinicalStatus(item.clinicalStatus)}',
      'Verification: ${_verification(item.verificationStatus)}',
      if (item.reaction != null && item.reaction!.trim().isNotEmpty)
        'Reaction: ${item.reaction}',
    ];

    return ListTile(
      key: ValueKey('patient-structured-allergy-${item.id}'),
      leading: Icon(
        warning
            ? Icons.warning_amber_rounded
            : Icons.medical_information_outlined,
        color: warning ? Theme.of(context).colorScheme.error : null,
      ),
      title: Text(item.allergenName),
      subtitle: Text(details.join('\n')),
      isThreeLine: true,
    );
  }

  @override
  Widget build(BuildContext context) {
    final canLoadMore = !_loading && _total != null && _items.length < _total!;

    return Card(
      key: const ValueKey('patient-structured-allergies-card'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Structured allergies',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                if (!_loading && _failure == null && _total != null)
                  IconButton(
                    key: const ValueKey('patient-allergies-refresh'),
                    tooltip: 'Refresh structured allergies',
                    onPressed: () => unawaited(_load()),
                    icon: const Icon(Icons.refresh),
                  ),
              ],
            ),
            const SizedBox(height: 6),
            const Text(
              'Backend-recorded allergy information. '
              'Unconfirmed, refuted and resolved records are labelled.',
            ),
            const SizedBox(height: 12),
            if (_failure != null) ...[
              Text(_failure!, key: const ValueKey('patient-allergies-error')),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                key: const ValueKey('patient-allergies-retry'),
                onPressed: () => unawaited(_load()),
                icon: const Icon(Icons.refresh),
                label: const Text('Retry allergy records'),
              ),
            ] else if (_loading && _items.isEmpty) ...[
              const Center(
                child: CircularProgressIndicator(
                  key: ValueKey('patient-allergies-loading'),
                ),
              ),
            ] else ...[
              if (_total == 0)
                const Text(
                  'No structured allergy records returned. '
                  'This does not confirm that the patient has no allergies.',
                  key: ValueKey('patient-allergies-empty'),
                ),
              if (_items.isEmpty && _total != null && _total != 0)
                const Text('No records on this page. Refresh to verify.'),
              for (final item in _items) _allergyRow(item),
              if (_loading)
                const Center(child: CircularProgressIndicator())
              else if (canLoadMore)
                OutlinedButton(
                  key: const ValueKey('patient-allergies-load-more'),
                  onPressed: () => unawaited(_load(nextPage: true)),
                  child: const Text('Load more allergies'),
                ),
            ],
          ],
        ),
      ),
    );
  }
}
