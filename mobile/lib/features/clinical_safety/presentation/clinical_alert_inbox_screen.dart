import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert_page.dart';

/// Read-only first-page alert inbox. No offline caching and no clinical writes.
///
/// The backend is the only authority for rule evaluation, action, status and
/// authorization. Subsequent slices provide detail and decision workflows.
class ClinicalAlertInboxScreen extends StatefulWidget {
  const ClinicalAlertInboxScreen({
    super.key,
    required this.dataSource,
    this.sessionNavigationGuard,
    this.onAlertSelected,
  });

  final ClinicalSafetyRemoteDataSource dataSource;
  final SessionNavigationGuard? sessionNavigationGuard;
  final ValueChanged<int>? onAlertSelected;

  @override
  State<ClinicalAlertInboxScreen> createState() =>
      _ClinicalAlertInboxScreenState();
}

class _ClinicalAlertInboxScreenState extends State<ClinicalAlertInboxScreen> {
  ClinicalAlertPage? _page;
  final Set<int> _previouslyObservedCriticalIds = <int>{};
  bool get _previouslyObservedCritical =>
      _previouslyObservedCriticalIds.isNotEmpty;
  bool _loading = true;
  bool _requiresSignIn = false;
  String? _errorMessage;
  int _requestGeneration = 0;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard?.addListener(_onSessionChanged);
    if (widget.sessionNavigationGuard?.canAccessProtectedRoutes == false) {
      _loading = false;
      _requiresSignIn = true;
      _errorMessage = 'Clinical session unavailable. Sign in again.';
    } else {
      unawaited(_reload());
    }
  }

  @override
  void didUpdateWidget(covariant ClinicalAlertInboxScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
      widget.sessionNavigationGuard,
      oldWidget.sessionNavigationGuard,
    )) {
      oldWidget.sessionNavigationGuard?.removeListener(_onSessionChanged);
      widget.sessionNavigationGuard?.addListener(_onSessionChanged);
      _onSessionChanged();
    }
    if (!identical(widget.dataSource, oldWidget.dataSource)) {
      _previouslyObservedCriticalIds.clear();
      unawaited(_reload());
    }
  }

  @override
  void dispose() {
    widget.sessionNavigationGuard?.removeListener(_onSessionChanged);
    _requestGeneration++;
    super.dispose();
  }

  void _onSessionChanged() {
    // A logout/rejection/context transition invalidates earlier asynchronous
    // GET and POST completions. Never display an earlier session's PHI.
    _requestGeneration++;
    if (!mounted) return;
    setState(() {
      _page = null;
      _previouslyObservedCriticalIds.clear();
      _loading = false;
      _requiresSignIn = true;
      _errorMessage = 'Clinical session changed. Sign in and reload.';
    });
  }

  Future<void> _reload() async {
    final generation = ++_requestGeneration;
    setState(() {
      // Never retain sensitive clinical alerts during an unconfirmed refresh.
      // They may belong to a previous session or be clinically outdated.
      _page = null;
      _loading = true;
      _requiresSignIn = false;
      _errorMessage = null;
    });

    try {
      final result = await widget.dataSource.fetchAlerts();
      if (!mounted || generation != _requestGeneration) return;
      setState(() {
        _page = result;
        // Retain prior critical IDs missing from the first-page response.
        // Only same-ID server-reported resolved/expired status clears them.
        for (final alert in result.items) {
          if (_criticalNotResolved(alert)) {
            _previouslyObservedCriticalIds.add(alert.id);
          } else if (alert.status == ClinicalAlertStatus.resolved ||
              alert.status == ClinicalAlertStatus.expired) {
            _previouslyObservedCriticalIds.remove(alert.id);
          }
        }
        _loading = false;
      });
    } catch (error) {
      if (!mounted || generation != _requestGeneration) return;
      setState(() {
        _page = null;
        _loading = false;
        _requiresSignIn =
            error is AuthenticatedSessionUnavailableException ||
            (error is ApiException && error.statusCode == 401);
        if (_requiresSignIn ||
            (error is ApiException &&
                (error.statusCode == 403 || error.statusCode == 404))) {
          _previouslyObservedCriticalIds.clear();
        }
        _errorMessage = _messageFor(error);
      });
    }
  }

  static String _messageFor(Object error) {
    if (error is AuthenticatedSessionUnavailableException) {
      return error.failure == AuthenticatedSessionAccessFailure.recoveryRequired
          ? 'Your saved session requires recovery. Sign in again to view clinical alerts.'
          : 'No authenticated session is available. Sign in again to view clinical alerts.';
    }
    if (error is ApiException) {
      if (error.statusCode == 401) {
        return 'Your session was rejected (HTTP 401). Sign in again to view clinical alerts.';
      }
      if (error.statusCode == 403) {
        return 'Your account is not permitted to view clinical alerts (HTTP 403).';
      }
      return switch (error.kind) {
        ApiFailureKind.timeout => 'Clinical alerts could not be confirmed because the request timed out.',
        ApiFailureKind.connection =>
          'Clinical alerts could not be confirmed. Check network connectivity.',
        ApiFailureKind.httpStatus =>
          'Clinical alerts could not be confirmed (HTTP ${error.statusCode}).',
        ApiFailureKind.invalidResponse =>
          'The backend returned an invalid clinical alert response.',
      };
    }
    if (error is FormatException) {
      return 'The backend returned incomplete or malformed clinical alert data.';
    }
    return 'Clinical alerts could not be confirmed. Please retry online.';
  }

  static String _severity(ClinicalAlertSeverity severity) => switch (severity) {
    ClinicalAlertSeverity.info => 'Info',
    ClinicalAlertSeverity.low => 'Low',
    ClinicalAlertSeverity.moderate => 'Moderate',
    ClinicalAlertSeverity.high => 'High',
    ClinicalAlertSeverity.critical => 'Critical',
  };

  static String _status(ClinicalAlertStatus status) => switch (status) {
    ClinicalAlertStatus.open => 'Open',
    ClinicalAlertStatus.acknowledged => 'Acknowledged',
    ClinicalAlertStatus.overridden => 'Overridden',
    ClinicalAlertStatus.resolved => 'Resolved',
    ClinicalAlertStatus.expired => 'Expired',
  };

  static bool _criticalNotResolved(ClinicalAlert alert) =>
      alert.severity == ClinicalAlertSeverity.critical &&
      alert.status != ClinicalAlertStatus.resolved &&
      alert.status != ClinicalAlertStatus.expired;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Clinical safety alerts'),
        actions: [
          IconButton(
            key: const ValueKey('clinical-alert-inbox-refresh'),
            tooltip: 'Refresh alerts',
            onPressed: _loading ? null : () => unawaited(_reload()),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: SafeArea(child: _buildBody()),
    );
  }

  Widget _priorCriticalWarning() => const Padding(
    padding: EdgeInsets.all(12),
    child: Text(
      'A critical alert was previously observed in this session. Its current '
      'status is UNCONFIRMED. Reconnect and verify online before clinical action.',
      key: ValueKey('clinical-alert-inbox-prior-critical-unconfirmed'),
      textAlign: TextAlign.center,
    ),
  );

  Widget _buildBody() {
    if (_loading) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const CircularProgressIndicator(
              key: ValueKey('clinical-alert-inbox-loading'),
            ),
            if (_previouslyObservedCritical) _priorCriticalWarning(),
            const SizedBox(height: 12),
            const Text('Loading current clinical alerts...'),
          ],
        ),
      );
    }

    final errorMessage = _errorMessage;
    if (errorMessage != null) {
      return Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.warning_amber_rounded, size: 40),
              const SizedBox(height: 12),
              const Text(
                'No verified clinical alert data is displayed.',
                key: ValueKey('clinical-alert-inbox-unconfirmed'),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              Text(errorMessage, textAlign: TextAlign.center),
              if (_previouslyObservedCritical) _priorCriticalWarning(),
              if (!_requiresSignIn) ...[
                const SizedBox(height: 16),
                OutlinedButton.icon(
                  key: const ValueKey('clinical-alert-inbox-retry'),
                  onPressed: () => unawaited(_reload()),
                  icon: const Icon(Icons.refresh),
                  label: const Text('Retry online'),
                ),
              ],
            ],
          ),
        ),
      );
    }

    final page = _page;
    if (page == null) {
      return const Center(child: Text('Clinical alert data is unavailable.'));
    }

    if (page.items.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.notifications_none, size: 44),
              const SizedBox(height: 12),
              const Text('No clinical alerts returned by the backend.'),
              if (_previouslyObservedCritical) _priorCriticalWarning(),
              if (page.total > 0) ...[
                const SizedBox(height: 8),
                Text(
                  'The backend reports ${page.total} alerts, but none were returned on this page.',
                  textAlign: TextAlign.center,
                ),
              ],
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: () => unawaited(_reload()),
                icon: const Icon(Icons.refresh),
                label: const Text('Refresh alerts'),
              ),
            ],
          ),
        ),
      );
    }

    final unresolvedCritical = page.items.where(_criticalNotResolved).length;
    final hasUnconfirmedPrior = _previouslyObservedCriticalIds.any(
      (id) => !page.items.any(
        (alert) => alert.id == id && _criticalNotResolved(alert),
      ),
    );
    final hasMore = page.total > page.items.length;

    return RefreshIndicator(
      onRefresh: _reload,
      child: ListView.separated(
        key: const ValueKey('clinical-alert-inbox-list'),
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 24),
        itemCount: page.items.length + 1,
        separatorBuilder: (_, _) => const Divider(height: 1),
        itemBuilder: (context, index) {
          if (index == 0) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Showing ${page.items.length} of ${page.total} alerts (first page)',
                  key: const ValueKey('clinical-alert-inbox-count'),
                ),
                if (unresolvedCritical > 0) ...[
                  const SizedBox(height: 10),
                  Container(
                    key: const ValueKey(
                      'clinical-alert-inbox-critical-warning',
                    ),
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      border: Border.all(
                        color: Theme.of(context).colorScheme.error,
                      ),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(
                      '$unresolvedCritical critical alert${unresolvedCritical == 1 ? '' : 's'} not resolved or expired on this page. Review authoritative backend status.',
                    ),
                  ),
                ],
                if (hasUnconfirmedPrior) ...[
                  const SizedBox(height: 10),
                  _priorCriticalWarning(),
                ],
                if (hasMore) ...[
                  const SizedBox(height: 10),
                  const Text(
                    'More alerts exist on the backend. Additional pages are unavailable until the query contract is corrected.',
                    key: ValueKey('clinical-alert-inbox-partial-warning'),
                  ),
                ],
                const SizedBox(height: 10),
                const Text(
                  'Read-only live view. No alert decisions are made in this screen.',
                ),
              ],
            );
          }

          final alert = page.items[index - 1];
          final critical = _criticalNotResolved(alert);
          return ListTile(
            key: ValueKey('clinical-alert-row-${alert.id}'),
            onTap: widget.onAlertSelected == null
                ? null
                : () => widget.onAlertSelected!(alert.id),
            contentPadding: const EdgeInsets.symmetric(
              horizontal: 4,
              vertical: 6,
            ),
            leading: Icon(
              critical
                  ? Icons.report_problem_outlined
                  : Icons.notification_important_outlined,
              color: critical ? Theme.of(context).colorScheme.error : null,
            ),
            title: Text(alert.title),
            subtitle: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Patient ID: ${alert.patientId}'),
                Text('Status: ${_status(alert.status)}'),
                Text(
                  alert.message,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
            trailing: Text(
              _severity(alert.severity),
              style: TextStyle(
                fontWeight: critical ? FontWeight.bold : FontWeight.normal,
                color: critical ? Theme.of(context).colorScheme.error : null,
              ),
            ),
          );
        },
      ),
    );
  }
}
