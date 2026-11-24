import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';

import 'package:clinic_system_pro/features/patient_portal/data/patient_portal_remote_data_source.dart';

import 'patient_portal_service_screens.dart';

/// Patient-specific, owned-data portal. Server authorization is authoritative.
/// Neither queued mutations nor arbitrary patient IDs are supported here.
class PatientPortalScreen extends StatefulWidget {
  const PatientPortalScreen({super.key, required this.source, this.onOpenLabResults, this.onOpenRadiologyCopies});

  final PatientPortalDataSource source;
  final Future<void> Function()? onOpenLabResults;
  final Future<void> Function()? onOpenRadiologyCopies;

  @override
  State<PatientPortalScreen> createState() => _PatientPortalScreenState();
}

class _PatientPortalScreenState extends State<PatientPortalScreen> {
  PatientPortalView? _view;
  bool _loading = true;
  bool _saving = false;
  bool _writeAttempted = false;
  String? _message;
  int _generation = 0;
  int? _staffId;
  DateTime? _date;
  TimeOfDay? _time;
  String _type = 'in_person';

  @override
  void initState() {
    super.initState();
    unawaited(_refresh());
  }

  @override
  void dispose() {
    _generation++;
    _view = null;
    super.dispose();
  }

  Future<void> _refresh() async {
    final generation = ++_generation;
    if (!mounted) {
      return;
    }
    setState(() {
      _loading = true;
      _message = null;
      _view = null;
    });
    try {
      final view = await widget.source.load();
      if (!mounted || generation != _generation) {
        return;
      }
      setState(() {
        _view = view;
        _loading = false;
      });
    } on ApiException catch (error) {
      if (!mounted || generation != _generation) {
        return;
      }
      setState(() {
        _loading = false;
        _view = null;
        _message =
            error.statusCode == 409 &&
                error.serverCode == 'PATIENT_ENROLLMENT_REQUIRED'
            ? 'Clinical enrollment is pending. Please contact your clinic to link your patient profile.'
            : 'Personal records unavailable. Check enrollment and session, then refresh.';
      });
    } on Object {
      if (!mounted || generation != _generation) {
        return;
      }
      setState(() {
        _loading = false;
        _view = null;
        _message = 'Personal records unavailable. Check enrollment and session, then refresh.';
      });
    }
  }

  String _newKey() {
    final random = Random.secure();
    final bytes = List<int>.generate(24, (_) => random.nextInt(256));
    return base64UrlEncode(bytes).replaceAll('=', '');
  }

  Future<void> _chooseDate() async {
    final today = DateTime.now();
    final date = await showDatePicker(
      context: context,
      initialDate: today.add(const Duration(days: 1)),
      firstDate: today,
      lastDate: today.add(const Duration(days: 90)),
    );
    if (date == null || !mounted) {
      return;
    }
    setState(() {
      _date = date;
    });
  }

  Future<void> _chooseTime() async {
    final time = await showTimePicker(
      context: context,
      initialTime: const TimeOfDay(hour: 10, minute: 0),
    );
    if (time == null || !mounted) {
      return;
    }
    setState(() {
      _time = time;
    });
  }

  Future<void> _book() async {
    if (_saving ||
        _writeAttempted ||
        _staffId == null ||
        _date == null ||
        _time == null) {
      return;
    }
    final start = DateTime(
      _date!.year,
      _date!.month,
      _date!.day,
      _time!.hour,
      _time!.minute,
    ).toUtc();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Submit appointment request?'),
        content: const Text(
          'This is a requested time, not a verified available slot. The clinic must confirm your appointment.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Back'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Submit request'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) {
      return;
    }
    setState(() {
      _saving = true;
      _writeAttempted = true;
      _message = null;
    });
    try {
      await widget.source.book(
        staffId: _staffId!,
        startUtc: start,
        appointmentType: _type,
        idempotencyKey: _newKey(),
      );
      if (!mounted) {
        return;
      }
      // Server returned an appointment ID with scheduled status.
      // Clinic confirmation remains distinct from appointment creation.
      await _refresh();
      if (mounted) {
        setState(() {
          _saving = false;
          _writeAttempted = false;
          _message =
              'Request recorded by the server. Await clinic confirmation.';
        });
      }
    } on Object {
      if (!mounted) {
        return;
      }
      setState(() {
        _saving = false;
        _message = 'Booking outcome is unconfirmed. Refresh My appointments before starting a NEW request. Do not retry this submission blindly.';
      });
    }
  }

  Future<void> _cancel(int id) async {
    if (_saving || _writeAttempted) {
      return;
    }
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Cancel this request?'),
        content: const Text(
          'Only eligible unconfirmed requests can be cancelled here. Confirmed appointments require contacting the clinic.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Keep appointment'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Cancel request'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) {
      return;
    }
    setState(() {
      _saving = true;
      _writeAttempted = true;
      _message = null;
    });
    try {
      await widget.source.cancel(id);
      if (!mounted) {
        return;
      }
      await _refresh();
      if (mounted) {
        setState(() {
          _saving = false;
          _writeAttempted = false;
          _message = 'Cancellation confirmed by the server.';
        });
      }
    } on Object {
      if (!mounted) {
        return;
      }
      setState(() {
        _saving = false;
        _message = 'Cancellation outcome is uncertain or not permitted. Refresh records or contact the clinic. Do not retry blindly.';
      });
    }
  }

  String _display(Object? value) =>
      value is String && value.isNotEmpty ? value : 'Not specified';

  Widget _group(
    String title,
    List<Map<String, dynamic>> rows,
    Widget Function(Map<String, dynamic>) tile,
  ) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 8),
            if (rows.isEmpty)
              const Text('No records returned for this patient.'),
            for (final item in rows) tile(item),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final view = _view;
    return Scaffold(
      appBar: AppBar(
        title: const Text('My care'),
        actions: [
          IconButton(
            tooltip: 'Refresh my records',
            onPressed: _saving ? null : () => unawaited(_refresh()),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : view == null
          ? Center(child: Text(_message ?? 'Unable to load patient portal.'))
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Welcome, ${view.name}',
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                const Text(
                  'Only your own clinic-authorized records are available. Booking requests are not confirmed visits. Lists show at most 30 recent records each.',
                ),
                if (_message != null)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    child: Text(_message!),
                  ),
                const SizedBox(height: 12),
                if (widget.onOpenRadiologyCopies != null)
                  OutlinedButton.icon(
                    key: const ValueKey('patient-portal-radc-copies'),
                    onPressed: _saving ? null : () => unawaited(widget.onOpenRadiologyCopies!()),
                    icon: const Icon(Icons.description_outlined),
                    label: const Text('My staged radiology copies'),
                  ),
                if (widget.onOpenLabResults != null)
                  OutlinedButton.icon(
                    key: const ValueKey('patient-portal-lab-results'),
                    onPressed: _saving ? null : () => unawaited(widget.onOpenLabResults!()),
                    icon: const Icon(Icons.science_outlined),
                    label: const Text('My Lab Results'),
                  ),
                _group(
                  'My appointments',
                  view.appointments,
                  (item) => ListTile(
                    title: Text(
                      'Appointment #${item['id']} · ${_display(item['status'])}',
                    ),
                    subtitle: Text(
                      '${_display(item['scheduled_start'])}\n${_display(item['appointment_type'])}',
                    ),
                    isThreeLine: true,
                    trailing:
                        item['status'] == 'scheduled' &&
                            !_saving &&
                            !_writeAttempted &&
                            item['id'] is int
                        ? TextButton(
                            onPressed: () =>
                                unawaited(_cancel(item['id'] as int)),
                            child: const Text('Cancel request'),
                          )
                        : null,
                  ),
                ),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Request an appointment',
                          style: Theme.of(context).textTheme.titleLarge,
                        ),
                        const Text(
                          'Available times have not been independently confirmed. Your request is checked by the clinic backend.',
                        ),
                        const SizedBox(height: 10),
                        if (view.clinicians.isEmpty)
                          const Text(
                            'No available clinician is listed for your clinic.',
                          ),
                        if (view.clinicians.isNotEmpty)
                          DropdownButtonFormField<int>(
                            key: const ValueKey('s12-clinician'),
                            initialValue: _staffId,
                            decoration: const InputDecoration(
                              labelText: 'Choose clinician',
                            ),
                            items: [
                              for (final c in view.clinicians)
                                if (c['id'] is int && c['name'] is String)
                                  DropdownMenuItem(
                                    value: c['id'] as int,
                                    child: Text(c['name'] as String),
                                  ),
                            ],
                            onChanged: _saving
                                ? null
                                : (value) => setState(() {
                                    _staffId = value;
                                  }),
                          ),
                        const SizedBox(height: 8),
                        DropdownButtonFormField<String>(
                          initialValue: _type,
                          decoration: const InputDecoration(
                            labelText: 'Visit type',
                          ),
                          items: const [
                            DropdownMenuItem(
                              value: 'in_person',
                              child: Text('In person'),
                            ),
                            DropdownMenuItem(
                              value: 'telemedicine',
                              child: Text('Telemedicine'),
                            ),
                            DropdownMenuItem(
                              value: 'follow_up',
                              child: Text('Follow-up'),
                            ),
                          ],
                          onChanged: _saving
                              ? null
                              : (v) => setState(() {
                                  _type = v ?? 'in_person';
                                }),
                        ),
                        Wrap(
                          spacing: 10,
                          children: [
                            OutlinedButton(
                              onPressed: _saving ? null : _chooseDate,
                              child: Text(
                                _date == null
                                    ? 'Choose date'
                                    : '${_date!.year}-${_date!.month}-${_date!.day}',
                              ),
                            ),
                            OutlinedButton(
                              onPressed: _saving ? null : _chooseTime,
                              child: Text(
                                _time == null
                                    ? 'Choose time'
                                    : _time!.format(context),
                              ),
                            ),
                          ],
                        ),
                        FilledButton(
                          key: const ValueKey('s12-book'),
                          onPressed:
                              view.clinicians.isEmpty ||
                                  _saving ||
                                  _writeAttempted ||
                                  _staffId == null ||
                                  _date == null ||
                                  _time == null
                              ? null
                              : () => unawaited(_book()),
                          child: Text(
                            _saving
                                ? 'Submitting...'
                                : 'Submit appointment request',
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
                if (widget.source is PatientPortalServiceActions)
                  OutlinedButton.icon(
                    key: const ValueKey('s13-care-messages'),
                    onPressed: () => Navigator.of(context).push<void>(
                      MaterialPageRoute(
                        builder: (_) => PatientCareMessagesPage(
                          actions: widget.source as PatientPortalServiceActions,
                        ),
                      ),
                    ),
                    icon: const Icon(Icons.forum_outlined),
                    label: const Text('My care messages'),
                  ),
                _group(
                  'My prescriptions',
                  view.prescriptions,
                  (item) => ListTile(
                    leading: const Icon(Icons.medication_outlined),
                    title: Text('Prescription #${item['id']}'),
                    trailing:
                        item['id'] is int &&
                            widget.source is PatientPortalServiceActions
                        ? const Icon(Icons.chevron_right)
                        : null,
                    onTap:
                        item['id'] is int &&
                            widget.source is PatientPortalServiceActions
                        ? () => Navigator.of(context).push<void>(
                            MaterialPageRoute(
                              builder: (_) => PatientPrescriptionDetailPage(
                                actions:
                                    widget.source
                                        as PatientPortalServiceActions,
                                recordId: item['id'] as int,
                              ),
                            ),
                          )
                        : null,
                    subtitle: Text(
                      'Status: ${_display(item['status'])} · Issued: ${_display(item['issued_at'])}',
                    ),
                  ),
                ),
                _group(
                  'My invoices',
                  view.invoices,
                  (item) => ListTile(
                    leading: const Icon(Icons.receipt_long_outlined),
                    title: Text(_display(item['invoice_number'])),
                    trailing:
                        item['id'] is int &&
                            widget.source is PatientPortalServiceActions
                        ? const Icon(Icons.chevron_right)
                        : null,
                    onTap:
                        item['id'] is int &&
                            widget.source is PatientPortalServiceActions
                        ? () => Navigator.of(context).push<void>(
                            MaterialPageRoute(
                              builder: (_) => PatientInvoiceDetailPage(
                                actions:
                                    widget.source
                                        as PatientPortalServiceActions,
                                recordId: item['id'] as int,
                              ),
                            ),
                          )
                        : null,
                    subtitle: Text(
                      'Status: ${_display(item['status'])} · Due: ${_display(item['due_date'])}\n'
                      'Total: ${_display(item['total_amount'])} ${_display(item['currency'])} · '
                      'Paid: ${_display(item['amount_paid'])}',
                    ),
                    isThreeLine: true,
                  ),
                ),
                const Text(
                  'Invoice details, test-only payment checkout and approved care messages are available where authorized by your clinic. Clinical record release is separate.',
                ),
              ],
            ),
    );
  }
}
