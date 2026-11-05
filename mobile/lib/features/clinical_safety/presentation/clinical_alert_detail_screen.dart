import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert.dart';

/// Read-only clinical alert detail. The backend owns clinical decisions.
///
/// The in-memory alert is discarded before every refresh or context change.
/// This screen does not cache PHI, acknowledge/resolve alerts, or authorize
/// medical actions. Route wiring and write actions belong to later slices.
class ClinicalAlertDetailScreen extends StatefulWidget {
  const ClinicalAlertDetailScreen({
    super.key,
    required this.dataSource,
    required this.alertId,
  });

  final ClinicalSafetyRemoteDataSource dataSource;
  final int alertId;

  @override
  State<ClinicalAlertDetailScreen> createState() =>
      _ClinicalAlertDetailScreenState();
}

class _ClinicalAlertDetailScreenState extends State<ClinicalAlertDetailScreen> {
  ClinicalAlert? _alert;
  bool _loading = true;
  bool _requiresSignIn = false;
  String? _errorMessage;
  int _requestGeneration = 0;

  @override
  void initState() {
    super.initState();
    unawaited(_reload());
  }

  @override
  void didUpdateWidget(covariant ClinicalAlertDetailScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.alertId != oldWidget.alertId ||
        !identical(widget.dataSource, oldWidget.dataSource)) {
      unawaited(_reload());
    }
  }

  @override
  void dispose() {
    _requestGeneration++;
    _alert = null;
    super.dispose();
  }

  Future<void> _reload() async {
    final generation = ++_requestGeneration;
    final alertId = widget.alertId;
    final dataSource = widget.dataSource;

    setState(() {
      // Do not show potentially stale or previous-session clinical data.
      _alert = null;
      _loading = true;
      _requiresSignIn = false;
      _errorMessage = null;
    });

    if (alertId <= 0) {
      setState(() {
        _loading = false;
        _errorMessage = 'The clinical alert ID is invalid.';
      });
      return;
    }

    try {
      final alert = await dataSource.fetchAlert(alertId: alertId);
      if (!mounted || generation != _requestGeneration) return;

      // Do not present a record that contradicts the selected resource ID.
      if (alert.id != alertId) {
        throw const FormatException('Clinical alert ID mismatch.');
      }

      setState(() {
        _alert = alert;
        _loading = false;
      });
    } catch (error) {
      if (!mounted || generation != _requestGeneration) return;
      setState(() {
        _alert = null;
        _loading = false;
        _requiresSignIn =
            error is AuthenticatedSessionUnavailableException ||
            (error is ApiException && error.statusCode == 401);
        _errorMessage = _messageFor(error);
      });
    }
  }

  static String _messageFor(Object error) {
    if (error is AuthenticatedSessionUnavailableException) {
      return error.failure == AuthenticatedSessionAccessFailure.recoveryRequired
          ? 'Your saved session requires recovery. Sign in again before viewing this clinical alert.'
          : 'No authenticated session is available. Sign in again before viewing this clinical alert.';
    }
    if (error is ApiException) {
      if (error.statusCode == 401) {
        return 'Your session was rejected (HTTP 401). Sign in again.';
      }
      if (error.statusCode == 403 || error.statusCode == 404) {
        return 'This clinical alert is not available to your account.';
      }
      return switch (error.kind) {
        ApiFailureKind.timeout => 'The clinical alert could not be confirmed because the request timed out.',
        ApiFailureKind.connection => 'The clinical alert could not be confirmed. Check network connectivity.',
        ApiFailureKind.httpStatus =>
          'The clinical alert could not be confirmed (HTTP ${error.statusCode}).',
        ApiFailureKind.invalidResponse =>
          'The backend returned an invalid clinical alert response.',
      };
    }
    if (error is FormatException) {
      return 'The backend returned incomplete or mismatched clinical alert data.';
    }
    return 'The clinical alert could not be confirmed. Please retry online.';
  }

  static String _severity(ClinicalAlertSeverity value) => switch (value) {
    ClinicalAlertSeverity.info => 'Info',
    ClinicalAlertSeverity.low => 'Low',
    ClinicalAlertSeverity.moderate => 'Moderate',
    ClinicalAlertSeverity.high => 'High',
    ClinicalAlertSeverity.critical => 'Critical',
  };

  static String _status(ClinicalAlertStatus value) => switch (value) {
    ClinicalAlertStatus.open => 'Open',
    ClinicalAlertStatus.acknowledged => 'Acknowledged',
    ClinicalAlertStatus.overridden => 'Overridden',
    ClinicalAlertStatus.resolved => 'Resolved',
    ClinicalAlertStatus.expired => 'Expired',
  };

  static String _action(ClinicalAlertAction value) => switch (value) {
    ClinicalAlertAction.inform => 'Inform',
    ClinicalAlertAction.alert => 'Alert',
    ClinicalAlertAction.requireAcknowledgement => 'Require acknowledgement',
    ClinicalAlertAction.requireJustification => 'Require justification',
    ClinicalAlertAction.block => 'Block',
  };

  static bool _unresolvedCritical(ClinicalAlert alert) =>
      alert.severity == ClinicalAlertSeverity.critical &&
      alert.status != ClinicalAlertStatus.resolved &&
      alert.status != ClinicalAlertStatus.expired;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Clinical alert detail'),
        actions: [
          IconButton(
            key: const ValueKey('clinical-alert-detail-refresh'),
            tooltip: 'Refresh alert',
            onPressed: _loading ? null : () => unawaited(_reload()),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: SafeArea(child: _buildBody()),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircularProgressIndicator(
              key: ValueKey('clinical-alert-detail-loading'),
            ),
            SizedBox(height: 12),
            Text('Loading current clinical alert detail...'),
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
                'No verified clinical alert detail is displayed.',
                key: ValueKey('clinical-alert-detail-unconfirmed'),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              Text(errorMessage, textAlign: TextAlign.center),
              if (!_requiresSignIn && widget.alertId > 0) ...[
                const SizedBox(height: 16),
                OutlinedButton.icon(
                  key: const ValueKey('clinical-alert-detail-retry'),
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

    final alert = _alert;
    if (alert == null) {
      return const Center(child: Text('Clinical alert detail is unavailable.'));
    }

    final critical = _unresolvedCritical(alert);
    return ListView(
      key: const ValueKey('clinical-alert-detail-list'),
      padding: const EdgeInsets.all(16),
      children: [
        Text(
          alert.title,
          key: const ValueKey('clinical-alert-detail-title'),
          style: Theme.of(context).textTheme.titleLarge,
        ),
        const SizedBox(height: 12),
        if (critical) ...[
          Container(
            key: const ValueKey('clinical-alert-detail-critical-warning'),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              border: Border.all(color: Theme.of(context).colorScheme.error),
              borderRadius: BorderRadius.circular(8),
            ),
            child: const Text(
              'Critical alert is not resolved or expired according to the latest backend response. Acknowledgement is not resolution.',
            ),
          ),
          const SizedBox(height: 12),
        ],
        if (alert.action == ClinicalAlertAction.block) ...[
          const Text(
            'Backend rule action: Block. This read-only screen does not authorize any clinical operation.',
            key: ValueKey('clinical-alert-detail-block-warning'),
          ),
          const SizedBox(height: 12),
        ],
        Text('Alert ID: ${alert.id}'),
        Text('Patient ID: ${alert.patientId}'),
        Text('Severity: ${_severity(alert.severity)}'),
        Text('Status: ${_status(alert.status)}'),
        Text('Action: ${_action(alert.action)}'),
        const SizedBox(height: 12),
        Text(
          'Message: ${alert.message}',
          key: const ValueKey('clinical-alert-detail-message'),
        ),
        const SizedBox(height: 12),
        Text('Rule ID: ${alert.ruleId}'),
        Text('Rule version: ${alert.ruleVersion}'),
        Text('Source: ${alert.sourceType}'),
        if (alert.sourceId != null) Text('Source ID: ${alert.sourceId}'),
        Text('Generated (UTC): ${alert.generatedAt.toIso8601String()}'),
        if (alert.resolvedAt != null)
          Text('Resolved (UTC): ${alert.resolvedAt!.toIso8601String()}'),
        const SizedBox(height: 16),
        const Text(
          'Read-only server-reported status. Refresh online to confirm current state. No alert decisions are made here.',
        ),
      ],
    );
  }
}
