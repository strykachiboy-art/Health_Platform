import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/appointments/data/appointment_remote_data_source.dart';
import 'package:clinic_system_pro/features/appointments/data/models/appointment_record.dart';

String _appointmentStatusLabel(AppointmentRecordStatus status) =>
    switch (status) {
      AppointmentRecordStatus.scheduled => 'Scheduled',
      AppointmentRecordStatus.confirmed => 'Confirmed',
      AppointmentRecordStatus.cancelled => 'Cancelled',
      AppointmentRecordStatus.completed => 'Completed',
      AppointmentRecordStatus.noShow => 'No-show',
    };

/// Read-only patient appointment history or staff schedule.
/// Navigation carries no bearer tokens. No local PHI persistence or writes.
class AppointmentListScreen extends StatefulWidget {
  const AppointmentListScreen({
    super.key,
    required this.dataSource,
    this.onChangeRequested,
    this.patientId,
    this.staffId,
    this.pageSize = 20,
  }) : assert((patientId == null) != (staffId == null)),
       assert(patientId == null || patientId > 0),
       assert(staffId == null || staffId > 0),
       assert(pageSize > 0 && pageSize <= 500);

  final AppointmentRemoteDataSource dataSource;
  final ValueChanged<AppointmentRecord>? onChangeRequested;
  final int? patientId;
  final int? staffId;
  final int pageSize;

  @override
  State<AppointmentListScreen> createState() => _AppointmentListScreenState();
}

class _AppointmentListScreenState extends State<AppointmentListScreen> {
  final TextEditingController _dateController = TextEditingController();
  List<AppointmentRecord> _records = const [];
  int? _total;
  int _page = 0;
  int _generation = 0;
  int? _loadedSessionRevision;
  bool _loading = false;
  String? _error;
  String? _dateError;
  String? _staffDate;
  String _statusFilter = 'all';

  SessionNavigationGuard get _guard => widget.dataSource.sessionNavigationGuard;

  @override
  void initState() {
    super.initState();
    _guard.addListener(_sessionChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_load());
    });
  }

  @override
  void didUpdateWidget(covariant AppointmentListScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (identical(oldWidget.dataSource, widget.dataSource) &&
        oldWidget.patientId == widget.patientId &&
        oldWidget.staffId == widget.staffId &&
        oldWidget.pageSize == widget.pageSize) {
      return;
    }
    oldWidget.dataSource.sessionNavigationGuard.removeListener(_sessionChanged);
    _guard.addListener(_sessionChanged);
    _generation++;
    _clear();
    _loading = false;
    _error = null;
    _dateError = null;
    _staffDate = null;
    _statusFilter = 'all';
    _dateController.clear();
    final expected = _generation;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && expected == _generation) unawaited(_load());
    });
  }

  @override
  void dispose() {
    _generation++;
    _guard.removeListener(_sessionChanged);
    _dateController.dispose();
    super.dispose();
  }

  void _clear() {
    _records = const [];
    _total = null;
    _page = 0;
    _loadedSessionRevision = null;
  }

  void _sessionChanged() {
    if (!mounted) return;
    _generation++;
    final allowed = _guard.canAccessProtectedRoutes;
    setState(() {
      _clear();
      _loading = false;
      _error = allowed
          ? null
          : 'Session unavailable. Appointment records cleared.';
    });
    if (allowed) unawaited(_load());
  }

  Future<void> _load({bool more = false}) async {
    if (_loading || (more && (_total == null || _records.length >= _total!))) {
      return;
    }

    final source = widget.dataSource;
    final guard = source.sessionNavigationGuard;
    if (!guard.canAccessProtectedRoutes) {
      setState(() {
        _generation++;
        _clear();
        _loading = false;
        _error = 'Session unavailable. Appointment records cleared.';
      });
      return;
    }

    final patientId = widget.patientId;
    final staffId = widget.staffId;
    final pageSize = widget.pageSize;
    final date = _staffDate;
    final revision = guard.revision;
    final generation = ++_generation;
    final requestedPage = more ? _page + 1 : 1;

    setState(() {
      if (!more) _clear();
      _loading = true;
      _error = null;
    });

    bool current() =>
        mounted &&
        generation == _generation &&
        identical(source, widget.dataSource) &&
        patientId == widget.patientId &&
        staffId == widget.staffId &&
        pageSize == widget.pageSize &&
        date == _staffDate &&
        guard.revision == revision &&
        guard.canAccessProtectedRoutes;

    try {
      final AppointmentPage result;
      if (patientId != null) {
        result = await source.fetchForPatient(
          patientId: patientId,
          page: requestedPage,
          perPage: pageSize,
        );
      } else {
        result = await source.fetchForStaff(
          staffId: staffId!,
          date: date,
          page: requestedPage,
          perPage: pageSize,
        );
      }
      if (!current()) return;

      final all = more
          ? <AppointmentRecord>[..._records, ...result.items]
          : <AppointmentRecord>[...result.items];
      if ((more && result.total != _total) ||
          all.length > result.total ||
          all.map((record) => record.id).toSet().length != all.length ||
          (result.hasNext && result.items.isEmpty) ||
          (more && result.items.isEmpty && all.length < result.total)) {
        throw const FormatException('Inconsistent appointment pages.');
      }
      setState(() {
        _records = List<AppointmentRecord>.unmodifiable(all);
        _total = result.total;
        _page = result.page;
        _loadedSessionRevision = revision;
        _loading = false;
      });
    } catch (error) {
      if (!current()) return;
      setState(() {
        _clear();
        _loading = false;
        _error = _errorFor(error);
      });
    }
  }

  String _errorFor(Object error) {
    if (error is AppointmentReadSessionMismatchException) {
      return 'Session unavailable. Appointment records cleared.';
    }
    if (error is ApiException &&
        const <int>{401, 403, 404, 422}.contains(error.statusCode)) {
      return 'Appointment access denied or records unavailable.';
    }
    return 'Appointment records could not be verified. Retry online.';
  }

  String _type(AppointmentRecordType type) => switch (type) {
    AppointmentRecordType.inPerson => 'In person',
    AppointmentRecordType.telemedicine => 'Telemedicine',
    AppointmentRecordType.followUp => 'Follow-up',
    AppointmentRecordType.emergency => 'Emergency',
  };

  bool _validDate(String date) {
    if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(date)) return false;
    final parsed = DateTime.tryParse(date);
    if (parsed == null) return false;
    return parsed.year.toString().padLeft(4, '0') == date.substring(0, 4) &&
        parsed.month.toString().padLeft(2, '0') == date.substring(5, 7) &&
        parsed.day.toString().padLeft(2, '0') == date.substring(8, 10);
  }

  void _applyDate() {
    final entered = _dateController.text.trim();
    if (entered.isNotEmpty && !_validDate(entered)) {
      setState(() => _dateError = 'Enter a valid date (YYYY-MM-DD).');
      return;
    }
    setState(() {
      _dateError = null;
      _staffDate = entered.isEmpty ? null : entered;
      _generation++;
      _clear();
      _loading = false;
    });
    unawaited(_load());
  }

  // Act only on records held in the currently verified list snapshot.
  void _requestChange(AppointmentRecord record) {
    if (!_guard.canAccessProtectedRoutes ||
        _loadedSessionRevision != _guard.revision ||
        !_records.any((row) => identical(row, record))) {
      return;
    }
    widget.onChangeRequested?.call(record);
  }

  void _openDetails(AppointmentRecord record) {
    if (!_guard.canAccessProtectedRoutes ||
        _loadedSessionRevision != _guard.revision) {
      return;
    }
    unawaited(
      Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => AppointmentDetailScreen(
            record: record,
            sessionNavigationGuard: _guard,
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final authorized = _guard.canAccessProtectedRoutes;
    final validSnapshot =
        authorized &&
        _loadedSessionRevision != null &&
        _loadedSessionRevision == _guard.revision;
    final visible = validSnapshot
        ? _records
              .where(
                (record) =>
                    _statusFilter == 'all' ||
                    record.status.name == _statusFilter,
              )
              .toList(growable: false)
        : <AppointmentRecord>[];
    final canLoadMore =
        !_loading &&
        validSnapshot &&
        _total != null &&
        _records.length < _total!;

    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.patientId != null
              ? 'Patient appointments'
              : 'Staff appointment schedule',
        ),
        actions: [
          if (validSnapshot && !_loading)
            IconButton(
              key: const ValueKey('appointments-refresh'),
              tooltip: 'Refresh appointments',
              onPressed: () => unawaited(_load()),
              icon: const Icon(Icons.refresh),
            ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            'Backend-recorded appointments only. This view does not create, change or confirm bookings.',
          ),
          const SizedBox(height: 12),
          if (widget.staffId != null) ...[
            TextField(
              key: const ValueKey('appointments-staff-date'),
              controller: _dateController,
              decoration: const InputDecoration(
                labelText: 'Staff schedule date (YYYY-MM-DD)',
                hintText: 'Leave blank for all dates',
              ),
              keyboardType: TextInputType.datetime,
            ),
            TextButton(
              key: const ValueKey('appointments-apply-date'),
              onPressed: authorized ? _applyDate : null,
              child: const Text('Apply date'),
            ),
            if (_dateError != null)
              Text(_dateError!, key: const ValueKey('appointments-date-error')),
          ],
          Row(
            children: [
              const Text('Filter loaded statuses: '),
              Expanded(
                child: DropdownButton<String>(
                  key: const ValueKey('appointments-status-filter'),
                  isExpanded: true,
                  value: _statusFilter,
                  onChanged: validSnapshot
                      ? (value) {
                          if (value != null) {
                            setState(() => _statusFilter = value);
                          }
                        }
                      : null,
                  items: const [
                    DropdownMenuItem(value: 'all', child: Text('All loaded')),
                    DropdownMenuItem(
                      value: 'scheduled',
                      child: Text('Scheduled'),
                    ),
                    DropdownMenuItem(
                      value: 'confirmed',
                      child: Text('Confirmed'),
                    ),
                    DropdownMenuItem(
                      value: 'cancelled',
                      child: Text('Cancelled'),
                    ),
                    DropdownMenuItem(
                      value: 'completed',
                      child: Text('Completed'),
                    ),
                    DropdownMenuItem(value: 'noShow', child: Text('No-show')),
                  ],
                ),
              ),
            ],
          ),
          const Text(
            'Status filtering applies only to records loaded on this device, not the full backend schedule.',
            key: ValueKey('appointments-filter-scope-warning'),
          ),
          const SizedBox(height: 12),
          if (!authorized)
            const Text(
              'Session unavailable. Appointment records cleared.',
              key: ValueKey('appointments-session-error'),
            )
          else if (_error != null) ...[
            Text(_error!, key: const ValueKey('appointments-error')),
            TextButton(
              key: const ValueKey('appointments-retry'),
              onPressed: () => unawaited(_load()),
              child: const Text('Retry appointments'),
            ),
          ] else if (_loading && _total == null)
            const Center(
              child: CircularProgressIndicator(
                key: ValueKey('appointments-loading'),
              ),
            )
          else if (_total == null)
            const Text('Preparing appointment records...')
          else ...[
            Text(
              'Loaded ${_records.length} of $_total backend records',
              key: const ValueKey('appointments-count'),
            ),
            if (_records.isEmpty)
              const Text(
                'No appointments returned in this request. This does not prove there are no other bookings.',
                key: ValueKey('appointments-empty'),
              ),
            if (_records.isNotEmpty && visible.isEmpty)
              const Text(
                'No matching status among loaded records.',
                key: ValueKey('appointments-filter-empty'),
              ),
            for (final record in visible)
              ListTile(
                key: ValueKey('appointment-row-${record.id}'),
                title: Text('Appointment #${record.id}'),
                subtitle: Text(
                  'Status: ${_appointmentStatusLabel(record.status)} | ${_type(record.appointmentType)}\nStart (backend): ${record.scheduledStart}',
                ),
                isThreeLine: true,
                trailing:
                    widget.onChangeRequested != null &&
                        (record.status == AppointmentRecordStatus.scheduled ||
                            record.status == AppointmentRecordStatus.confirmed)
                    ? IconButton(
                        key: ValueKey('appointment-change-${record.id}'),
                        tooltip: 'Change appointment',
                        icon: const Icon(Icons.edit_calendar_outlined),
                        onPressed: () => _requestChange(record),
                      )
                    : null,
                onTap: () => _openDetails(record),
              ),
            if (_loading)
              const Center(child: CircularProgressIndicator())
            else if (canLoadMore)
              TextButton(
                key: const ValueKey('appointments-load-more'),
                onPressed: () => unawaited(_load(more: true)),
                child: const Text('Load more appointments'),
              ),
          ],
        ],
      ),
    );
  }
}

/// Displays a verified list record; it is not a new detail GET endpoint.
class AppointmentDetailScreen extends StatefulWidget {
  const AppointmentDetailScreen({
    super.key,
    required this.record,
    required this.sessionNavigationGuard,
  });

  final AppointmentRecord record;
  final SessionNavigationGuard sessionNavigationGuard;

  @override
  State<AppointmentDetailScreen> createState() =>
      _AppointmentDetailScreenState();
}

class _AppointmentDetailScreenState extends State<AppointmentDetailScreen> {
  late int _revision;

  @override
  void initState() {
    super.initState();
    _revision = widget.sessionNavigationGuard.revision;
    widget.sessionNavigationGuard.addListener(_sessionChanged);
  }

  @override
  void didUpdateWidget(covariant AppointmentDetailScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
          oldWidget.sessionNavigationGuard,
          widget.sessionNavigationGuard,
        ) ||
        !identical(oldWidget.record, widget.record)) {
      oldWidget.sessionNavigationGuard.removeListener(_sessionChanged);
      _revision = widget.sessionNavigationGuard.revision;
      widget.sessionNavigationGuard.addListener(_sessionChanged);
    }
  }

  @override
  void dispose() {
    widget.sessionNavigationGuard.removeListener(_sessionChanged);
    super.dispose();
  }

  void _sessionChanged() {
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final available =
        widget.sessionNavigationGuard.canAccessProtectedRoutes &&
        widget.sessionNavigationGuard.revision == _revision;
    final record = widget.record;

    return Scaffold(
      appBar: AppBar(title: const Text('Appointment detail')),
      body: !available
          ? const Center(
              child: Text(
                'Session changed. Appointment detail cleared.',
                key: ValueKey('appointment-detail-session-error'),
              ),
            )
          : ListView(
              key: ValueKey('appointment-detail-${record.id}'),
              padding: const EdgeInsets.all(16),
              children: [
                Text('Appointment #${record.id}'),
                Text('Patient ID: ${record.patientId}'),
                Text('Staff ID: ${record.staffId}'),
                Text('Clinic ID: ${record.clinicId}'),
                Text('Status: ${_appointmentStatusLabel(record.status)}'),
                Text('Scheduled start (backend): ${record.scheduledStart}'),
                Text('Scheduled end (backend): ${record.scheduledEnd}'),
                if (record.reason != null) Text('Reason: ${record.reason}'),
                if (record.notes != null) Text('Notes: ${record.notes}'),
                if (record.cancellationReason != null)
                  Text('Cancellation reason: ${record.cancellationReason}'),
                const SizedBox(height: 12),
                const Text(
                  'Read-only backend snapshot. Refresh the schedule before making clinical or scheduling decisions.',
                ),
              ],
            ),
    );
  }
}
