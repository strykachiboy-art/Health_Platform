import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/networking/device_connectivity_source.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert_acknowledgement.dart';

/// Clinical alert detail and staff acknowledgement requests.
///
/// All clinical decisions, permissions, and alert state remain server-owned.
/// No local replay, offline clinical writes, or implied clinical authorization.
/// Uncertain write outcomes block repeat submissions within this screen;
/// verified per-actor audit reconciliation is not available from the GET API.
class ClinicalAlertDetailScreen extends StatefulWidget {
  const ClinicalAlertDetailScreen({
    super.key,
    required this.dataSource,
    this.sessionNavigationGuard,
    required this.alertId,
  });

  final ClinicalSafetyRemoteDataSource dataSource;
  final SessionNavigationGuard? sessionNavigationGuard;
  final int alertId;

  @override
  State<ClinicalAlertDetailScreen> createState() =>
      _ClinicalAlertDetailScreenState();
}

class _ClinicalAlertDetailScreenState extends State<ClinicalAlertDetailScreen> {
  ClinicalAlert? _alert;
  DeviceNetworkStatus _deviceNetworkStatus = DeviceNetworkStatus.connected;
  StreamSubscription<DeviceNetworkStatus>? _deviceStatusSubscription;

  void _startDeviceStatusMonitor() {
    final previous = _deviceStatusSubscription;
    if (previous != null) unawaited(previous.cancel());
    _deviceStatusSubscription = null;
    final device = widget.dataSource.deviceConnectivitySource;
    _deviceNetworkStatus = device == null
        ? DeviceNetworkStatus.connected
        : DeviceNetworkStatus.unknown;
    if (device == null) return;
    _deviceStatusSubscription = device.statusChanges.listen(
      (status) {
        if (!mounted ||
            !identical(widget.dataSource.deviceConnectivitySource, device)) {
          return;
        }
        setState(() => _deviceNetworkStatus = status);
      },
      onError: (Object _, StackTrace _) {
        if (!mounted ||
            !identical(widget.dataSource.deviceConnectivitySource, device)) {
          return;
        }
        setState(() => _deviceNetworkStatus = DeviceNetworkStatus.unknown);
      },
    );
    unawaited(_checkDeviceNetwork(device));
  }

  Future<void> _checkDeviceNetwork(DeviceConnectivitySource device) async {
    DeviceNetworkStatus status;
    try {
      status = await device.checkStatus();
    } on Object {
      status = DeviceNetworkStatus.unknown;
    }
    if (!mounted ||
        !identical(widget.dataSource.deviceConnectivitySource, device)) {
      return;
    }
    setState(() => _deviceNetworkStatus = status);
  }

  bool _previouslyObservedCritical = false;
  bool _loading = true;
  bool _requiresSignIn = false;
  String? _errorMessage;
  int _requestGeneration = 0;
  int _writeGeneration = 0;
  final TextEditingController _justificationController =
      TextEditingController();
  bool _sending = false;
  bool _writeBlocked = false;
  bool _writeOutcomeUnknown = false;
  bool _writeReadConfirmed = false;
  String? _writeMessage;
  bool _confirmingResolution = false;
  bool _resolutionAttempted = false;

  @override
  void initState() {
    super.initState();
    _startDeviceStatusMonitor();
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
  void didUpdateWidget(covariant ClinicalAlertDetailScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(widget.dataSource, oldWidget.dataSource)) {
      _startDeviceStatusMonitor();
    }
    if (!identical(
      widget.sessionNavigationGuard,
      oldWidget.sessionNavigationGuard,
    )) {
      oldWidget.sessionNavigationGuard?.removeListener(_onSessionChanged);
      widget.sessionNavigationGuard?.addListener(_onSessionChanged);
      _onSessionChanged();
    }
    if (widget.alertId != oldWidget.alertId ||
        !identical(widget.dataSource, oldWidget.dataSource)) {
      _previouslyObservedCritical = false;
      _writeGeneration++;
      _sending = false;
      _writeBlocked = false;
      _writeOutcomeUnknown = false;
      _writeReadConfirmed = false;
      _writeMessage = null;
      _confirmingResolution = false;
      _resolutionAttempted = false;
      _justificationController.clear();
      unawaited(_reload());
    }
  }

  @override
  void dispose() {
    final subscription = _deviceStatusSubscription;
    if (subscription != null) unawaited(subscription.cancel());
    widget.sessionNavigationGuard?.removeListener(_onSessionChanged);
    _requestGeneration++;
    _writeGeneration++;
    _justificationController.dispose();
    _alert = null;
    super.dispose();
  }

  void _onSessionChanged() {
    // A logout/rejection/context transition invalidates earlier asynchronous
    // GET and POST completions. Never display an earlier session's PHI.
    _requestGeneration++;
    _writeGeneration++;
    _sending = false;
    _writeBlocked = true;
    _writeOutcomeUnknown = true;
    _writeReadConfirmed = false;
    _justificationController.clear();
    if (!mounted) return;
    setState(() {
      _alert = null;
      _previouslyObservedCritical = false;
      _loading = false;
      _requiresSignIn = true;
      _errorMessage = 'Clinical session changed. Sign in and reload.';
    });
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
      _writeReadConfirmed = false;
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
        _previouslyObservedCritical = _unresolvedCritical(alert);
        _loading = false;
        _writeReadConfirmed = _writeBlocked;
      });
    } catch (error) {
      if (!mounted || generation != _requestGeneration) return;
      setState(() {
        _alert = null;
        _loading = false;
        _requiresSignIn =
            error is AuthenticatedSessionUnavailableException ||
            (error is ApiException && error.statusCode == 401);
        if (_requiresSignIn ||
            (error is ApiException &&
                (error.statusCode == 403 || error.statusCode == 404))) {
          _previouslyObservedCritical = false;
        }
        _errorMessage = _messageFor(error);
      });
    }
  }

  /// Never unlock a possibly duplicated POST merely because GET returns
  /// the current alert status. GET cannot identify the current actor's audit
  /// acknowledgement or prove that a delayed write will not still complete.
  String get _writeNotice {
    if (_writeOutcomeUnknown) {
      if (_resolutionAttempted) {
        return _writeReadConfirmed
            ? 'Latest alert status was fetched, but GET cannot establish '
                  'whether the previous resolution POST committed. '
                  'Do not retry; request backend audit reconciliation.'
            : 'Resolution outcome UNKNOWN. Do not repeat the POST. '
                  'Refresh online and obtain authoritative backend audit '
                  'reconciliation before further clinical action.';
      }
      return _writeReadConfirmed
          ? 'Latest alert status was fetched, but GET cannot establish whether '
                'your previous acknowledgement was recorded for this user. '
                'Do not repeat the write. Request server audit review.'
          : 'Acknowledgement outcome is UNKNOWN. Do not repeat the write. '
                'Refresh online to inspect current status, then request '
                'server audit review before taking further action.';
    }
    return _writeMessage ??
        'Further acknowledgement or override submissions are blocked '
            'for this alert in the current screen.';
  }

  void _markWriteBlocked({
    required String message,
    bool uncertain = false,
    bool requiresSignIn = false,
  }) {
    setState(() {
      // Never display earlier clinical state as verified after a failed write.
      _alert = null;
      _loading = false;
      _writeBlocked = true;
      _writeOutcomeUnknown = uncertain;
      _writeReadConfirmed = false;
      _requiresSignIn = requiresSignIn;
      _writeMessage = message;
      _errorMessage = message;
    });
  }

  Future<void> _submitAcknowledgement(
    ClinicalAlertAcknowledgementType type,
  ) async {
    final alert = _alert;
    if (_loading ||
        _sending ||
        _confirmingResolution ||
        _writeBlocked ||
        _deviceNetworkStatus != DeviceNetworkStatus.connected ||
        alert == null ||
        alert.id != widget.alertId ||
        alert.status == ClinicalAlertStatus.resolved ||
        alert.status == ClinicalAlertStatus.expired) {
      return;
    }

    final justification = _justificationController.text.trim();
    if (justification.length > 5000) {
      setState(
        () => _writeMessage = 'Justification must not exceed 5000 characters.',
      );
      return;
    }
    if (type == ClinicalAlertAcknowledgementType.overridden &&
        justification.isEmpty) {
      setState(
        () => _writeMessage =
            'Justification is required before requesting an override.',
      );
      return;
    }

    final writeGeneration = _writeGeneration;
    final alertId = alert.id;
    final source = widget.dataSource;
    setState(() {
      _sending = true;
      _writeMessage = null;
    });

    try {
      // One POST only. There is no documented idempotency contract.
      await source.acknowledgeAlert(
        alertId: alertId,
        type: type,
        justification: justification.isEmpty ? null : justification,
      );
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }

      setState(() {
        _writeBlocked = true;
        _writeOutcomeUnknown = false;
        _writeReadConfirmed = false;
        _writeMessage =
            'The backend recorded the acknowledgement. '
            'Fetching current alert status. This is not clinical resolution.';
        _justificationController.clear();
      });
      // A read-only GET is safe after a confirmed POST. No second POST.
      await _reload();
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      setState(() {
        _writeMessage = _alert != null
            ? 'Backend acknowledgement recorded and current alert status '
                  'refreshed. This does not resolve an alert or authorize a '
                  'blocked clinical operation. Repeat submission is blocked.'
            : 'Backend acknowledgement recorded, but fresh alert status '
                  'could not be confirmed. Repeat submission is blocked. '
                  'Refresh online before further action.';
      });
    } on ClinicalSafetyOfflineWriteBlockedException {
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      setState(() {
        _writeMessage =
            'No confirmed device network connection. '
            'No clinical POST was sent; reconnect before trying again.';
      });
    } on ClinicalSafetyWriteOutcomeUnknownException {
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      _markWriteBlocked(
        message:
            'Session or clinic changed after the clinical POST may have '
            'been dispatched. Outcome UNKNOWN: do not retry. '
            'Request authoritative server audit reconciliation.',
        uncertain: true,
      );
    } on AuthenticatedSessionUnavailableException {
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      setState(() {
        // The token loader failed before an HTTP POST was sent.
        _alert = null;
        _loading = false;
        _requiresSignIn = true;
        _errorMessage =
            'The authenticated session is unavailable. '
            'Sign in again before viewing this clinical alert.';
      });
    } on ApiException catch (error) {
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      final status = error.statusCode;
      if (status == 401 || status == 403 || status == 404) {
        _markWriteBlocked(
          message: status == 401
              ? 'Your session was rejected (HTTP 401). Sign in again.'
              : 'This clinical operation is not available to your account '
                    '(HTTP $status).',
          requiresSignIn: status == 401,
        );
      } else if (status == 409) {
        _markWriteBlocked(
          message:
              'The backend reported an acknowledgement conflict '
              '(HTTP 409). Refresh and review server audit history. '
              'Do not repeat the submission.',
        );
      } else if (status == 422 || status == 400) {
        _markWriteBlocked(
          message:
              'The backend denied the request (HTTP $status). '
              'Critical hard rules cannot be overridden. '
              'No clinical permission was granted.',
        );
      } else if (error.kind == ApiFailureKind.timeout ||
          error.kind == ApiFailureKind.connection ||
          error.kind == ApiFailureKind.invalidResponse ||
          (status != null && status >= 500)) {
        _markWriteBlocked(
          message:
              'Acknowledgement outcome is UNKNOWN. Do not retry. '
              'Refresh online, then request server audit review.',
          uncertain: true,
        );
      } else {
        _markWriteBlocked(
          message:
              'The backend did not confirm this acknowledgement. '
              'Do not repeat it without reviewing server status.',
          uncertain: true,
        );
      }
    } on ArgumentError {
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      // Local validation can be corrected; no HTTP was sent.
      setState(
        () =>
            _writeMessage = 'Please review the acknowledgement justification.',
      );
    } on Object {
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      // A 2xx response with a malformed body may still have committed.
      _markWriteBlocked(
        message:
            'Acknowledgement outcome is UNKNOWN after response '
            'validation. Do not retry. Request server audit review.',
        uncertain: true,
      );
    } finally {
      if (mounted &&
          writeGeneration == _writeGeneration &&
          widget.alertId == alertId) {
        setState(() => _sending = false);
      }
    }
  }

  /// Resolution is a distinct backend-authoritative, non-idempotent write.
  /// A confirmation is required and an uncertain result can never be retried.
  Future<void> _submitResolution() async {
    final alert = _alert;
    if (_loading ||
        _sending ||
        _confirmingResolution ||
        _writeBlocked ||
        _deviceNetworkStatus != DeviceNetworkStatus.connected ||
        widget.sessionNavigationGuard?.canAccessProtectedRoutes == false ||
        alert == null ||
        alert.id != widget.alertId ||
        alert.status == ClinicalAlertStatus.resolved ||
        alert.status == ClinicalAlertStatus.expired) {
      return;
    }

    final requestGeneration = _requestGeneration;
    final writeGeneration = _writeGeneration;
    final source = widget.dataSource;
    setState(() => _confirmingResolution = true);
    final bool accepted;
    try {
      accepted =
          await showDialog<bool>(
            context: context,
            barrierDismissible: false,
            builder: (dialogContext) => AlertDialog(
              title: const Text('Resolve this clinical alert?'),
              content: const Text(
                'This is a separate, audited server-side resolution request. '
                'Only an authorized member of the clinical team may resolve '
                'this alert. It cannot be undone or automatically retried. '
                'Resolution does not by itself authorize a blocked clinical '
                'operation. Verify the underlying clinical issue separately.',
              ),
              actions: [
                TextButton(
                  key: const ValueKey('clinical-alert-resolve-cancel'),
                  onPressed: () => Navigator.of(dialogContext).pop(false),
                  child: const Text('Cancel'),
                ),
                FilledButton(
                  key: const ValueKey('clinical-alert-resolve-confirm'),
                  onPressed: () => Navigator.of(dialogContext).pop(true),
                  child: const Text('Confirm resolution'),
                ),
              ],
            ),
          ) ??
          false;
    } finally {
      if (mounted && writeGeneration == _writeGeneration) {
        setState(() => _confirmingResolution = false);
      }
    }

    // A refresh, logout, clinic switch, or change of alert while the dialog
    // was open invalidates the confirmation. In that case send zero POSTs.
    if (!accepted ||
        !mounted ||
        writeGeneration != _writeGeneration ||
        requestGeneration != _requestGeneration ||
        !identical(widget.dataSource, source) ||
        !identical(_alert, alert) ||
        _sending ||
        _writeBlocked ||
        _deviceNetworkStatus != DeviceNetworkStatus.connected ||
        widget.sessionNavigationGuard?.canAccessProtectedRoutes == false) {
      return;
    }

    final alertId = alert.id;
    setState(() {
      _sending = true;
      _resolutionAttempted = true;
      _writeMessage = null;
    });
    try {
      // Exactly one POST. The data source rechecks device network and session.
      // Neither timeout nor unknown response may trigger a second POST.
      await source.resolveAlert(alertId: alertId);
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      setState(() {
        _writeBlocked = true;
        _writeOutcomeUnknown = false;
        _writeReadConfirmed = false;
        _writeMessage =
            'The backend reported a resolution transition. '
            'Verifying current alert status with a fresh read. '
            'This does not authorize blocked care.';
      });
      await _reload(); // Read-only: never resend the resolution POST.
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      setState(() {
        if (_alert?.status == ClinicalAlertStatus.resolved &&
            _alert?.resolvedAt != null) {
          _writeMessage =
              'Backend resolution confirmed by a fresh alert read. '
              'No further submission is permitted in this screen. '
              'Resolution does not grant permission for blocked clinical care.';
        } else {
          _writeOutcomeUnknown = true;
          _writeMessage =
              'A resolution response was received, but the latest read did '
              'not confirm resolved status. Do not repeat the write. '
              'Request authoritative backend audit review.';
        }
      });
    } on ClinicalSafetyOfflineWriteBlockedException {
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      setState(() {
        // Pre-dispatch device check rejected the write: zero HTTP POSTs.
        _resolutionAttempted = false;
        _writeMessage =
            'No confirmed device network connection. No resolution POST '
            'was sent. Reconnect and refresh before trying again.';
      });
    } on AuthenticatedSessionUnavailableException {
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      setState(() {
        // Authentication was rejected before POST dispatch.
        _resolutionAttempted = false;
        _alert = null;
        _loading = false;
        _requiresSignIn = true;
        _errorMessage =
            'The authenticated session is unavailable. '
            'Sign in again before viewing this clinical alert.';
      });
    } on ClinicalSafetyWriteOutcomeUnknownException {
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      _markWriteBlocked(
        message:
            'Session or clinic changed after a resolution POST may have '
            'been dispatched. Outcome UNKNOWN. Do not retry; obtain '
            'authoritative backend audit reconciliation.',
        uncertain: true,
      );
    } on ApiException catch (error) {
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      final status = error.statusCode;
      if (status == 401 || status == 403 || status == 404) {
        _markWriteBlocked(
          message: status == 401
              ? 'The session was rejected (HTTP 401). Sign in again.'
              : 'This clinical resolution is unavailable to this account '
                    '(HTTP $status). No resolution was confirmed.',
          requiresSignIn: status == 401,
        );
      } else if (status == 409 || status == 400 || status == 422) {
        _markWriteBlocked(
          message:
              'The backend rejected clinical resolution (HTTP $status). '
              'Do not retry without verifying current status and clinical '
              'authorization with the server.',
        );
      } else if (error.kind == ApiFailureKind.timeout ||
          error.kind == ApiFailureKind.connection ||
          error.kind == ApiFailureKind.invalidResponse ||
          (status != null && status >= 500)) {
        _markWriteBlocked(
          message:
              'Resolution outcome UNKNOWN. Do not retry. Refresh online '
              'and request authoritative backend audit reconciliation.',
          uncertain: true,
        );
      } else {
        _markWriteBlocked(
          message:
              'Resolution was not confirmed. Do not repeat the request '
              'without authoritative backend status review.',
          uncertain: true,
        );
      }
    } on Object {
      if (!mounted ||
          writeGeneration != _writeGeneration ||
          widget.alertId != alertId) {
        return;
      }
      // A malformed success response may be hiding a committed transition.
      _markWriteBlocked(
        message:
            'Resolution outcome UNKNOWN after response validation. '
            'Do not retry. Request authoritative backend audit review.',
        uncertain: true,
      );
    } finally {
      if (mounted &&
          writeGeneration == _writeGeneration &&
          widget.alertId == alertId) {
        setState(() => _sending = false);
      }
    }
  }

  Widget _resolutionControl() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Divider(),
        const SizedBox(height: 12),
        Text(
          'Separate clinical resolution',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 8),
        const Text(
          'Resolving changes the backend alert status and creates an audit '
          'record. It is NOT acknowledgement, override, or permission to '
          'continue blocked care. Confirm only after clinical review.',
        ),
        const SizedBox(height: 8),
        if (_deviceNetworkStatus != DeviceNetworkStatus.connected) ...[
          const Text(
            'Resolution is unavailable without confirmed device connectivity.',
            key: ValueKey('clinical-alert-resolve-offline-warning'),
          ),
          const SizedBox(height: 8),
        ],
        FilledButton.icon(
          key: const ValueKey('clinical-alert-resolve-submit'),
          onPressed:
              _sending ||
                  _confirmingResolution ||
                  _writeBlocked ||
                  _deviceNetworkStatus != DeviceNetworkStatus.connected ||
                  widget.sessionNavigationGuard?.canAccessProtectedRoutes ==
                      false
              ? null
              : () => unawaited(_submitResolution()),
          icon: const Icon(Icons.task_alt_outlined),
          label: const Text('Resolve alert'),
        ),
      ],
    );
  }

  Widget _writeNoticeWidget() {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Text(
        _writeNotice,
        key: ValueKey(
          _writeOutcomeUnknown
              ? 'clinical-alert-detail-write-uncertain'
              : 'clinical-alert-detail-write-blocked',
        ),
        style: TextStyle(
          color: Theme.of(context).colorScheme.error,
          fontWeight: FontWeight.bold,
        ),
      ),
    );
  }

  Widget _acknowledgementControls() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Acknowledgement is an audited backend request, not resolution '
          'or permission to proceed with blocked clinical care.',
        ),
        const SizedBox(height: 12),
        TextField(
          key: const ValueKey('clinical-alert-acknowledgement-justification'),
          controller: _justificationController,
          maxLength: 5000,
          minLines: 1,
          maxLines: 3,
          decoration: const InputDecoration(
            labelText: 'Clinical justification',
            helperText: 'Optional for acknowledgement; required for override.',
            border: OutlineInputBorder(),
          ),
        ),
        if (_writeMessage != null) ...[
          Text(
            _writeMessage!,
            key: const ValueKey('clinical-alert-detail-write-validation'),
          ),
          const SizedBox(height: 8),
        ],
        if (_deviceNetworkStatus != DeviceNetworkStatus.connected) ...[
          const Text(
            'Clinical writes are disabled while device network connectivity '
            'is disconnected or unconfirmed. No offline clinical write queue.',
            key: ValueKey('clinical-alert-detail-offline-write-blocked'),
          ),
          const SizedBox(height: 8),
        ],
        Wrap(
          spacing: 12,
          runSpacing: 8,
          children: [
            ElevatedButton(
              key: const ValueKey('clinical-alert-acknowledge-submit'),
              onPressed:
                  _sending ||
                      _confirmingResolution ||
                      _deviceNetworkStatus != DeviceNetworkStatus.connected
                  ? null
                  : () => unawaited(
                      _submitAcknowledgement(
                        ClinicalAlertAcknowledgementType.acknowledged,
                      ),
                    ),
              child: const Text('Acknowledge'),
            ),
            OutlinedButton(
              key: const ValueKey('clinical-alert-override-submit'),
              onPressed:
                  _sending ||
                      _confirmingResolution ||
                      _deviceNetworkStatus != DeviceNetworkStatus.connected
                  ? null
                  : () => unawaited(
                      _submitAcknowledgement(
                        ClinicalAlertAcknowledgementType.overridden,
                      ),
                    ),
              child: const Text('Request override'),
            ),
          ],
        ),
        if (_sending) ...[
          const SizedBox(height: 8),
          const LinearProgressIndicator(
            key: ValueKey('clinical-alert-detail-write-pending'),
          ),
        ],
      ],
    );
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
            onPressed: _loading || _sending ? null : () => unawaited(_reload()),
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
      'This critical alert was previously unresolved. Its current state is '
      'UNCONFIRMED. Verify online; no clinical action is authorized by stale data.',
      key: ValueKey('clinical-alert-detail-prior-critical-unconfirmed'),
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
              key: ValueKey('clinical-alert-detail-loading'),
            ),
            if (_previouslyObservedCritical) _priorCriticalWarning(),
            const SizedBox(height: 12),
            const Text('Loading current clinical alert detail...'),
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
              if (!_writeBlocked)
                Text(errorMessage, textAlign: TextAlign.center),
              if (_previouslyObservedCritical) _priorCriticalWarning(),
              if (_writeBlocked) _writeNoticeWidget(),
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
    // Keep an unconfirmed clinical write visible after the user scrolls to
    // the action at the bottom. A lazy ListView child is not a safe warning.
    return Column(
      children: [
        if (_writeBlocked) _writeNoticeWidget(),
        Expanded(
          child: ListView(
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
                    border: Border.all(
                      color: Theme.of(context).colorScheme.error,
                    ),
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
                  'Backend rule action: Block. Acknowledgement or override does not grant permission to perform a blocked clinical operation.',
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
                'Server-reported clinical status is authoritative. '
                'Acknowledgement never grants permission for a blocked operation.',
              ),
              const SizedBox(height: 16),
              if (!_writeBlocked &&
                  alert.status != ClinicalAlertStatus.resolved &&
                  alert.status != ClinicalAlertStatus.expired) ...[
                _acknowledgementControls(),
                const SizedBox(height: 20),
                _resolutionControl(),
              ],
            ],
          ),
        ),
      ],
    );
  }
}
