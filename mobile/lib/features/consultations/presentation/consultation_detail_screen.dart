import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient_consultation.dart';

/// Read-only detail. No edits or clinical write actions are inferred from GET.
class ConsultationDetailScreen extends StatefulWidget {
  const ConsultationDetailScreen({
    super.key,
    required this.dataSource,
    required this.patientId,
    required this.consultationId,
    this.onClinicalActionsRequested,
  });

  final ConsultationRemoteDataSource dataSource;
  final int patientId;
  final int consultationId;
  final VoidCallback? onClinicalActionsRequested;

  @override
  State<ConsultationDetailScreen> createState() =>
      _ConsultationDetailScreenState();
}

class _ConsultationDetailScreenState extends State<ConsultationDetailScreen> {
  PatientConsultation? _record;
  bool _loading = true;
  String? _message;
  int _generation = 0;
  SessionNavigationGuard get _guard => widget.dataSource.sessionNavigationGuard;

  @override
  void initState() {
    super.initState();
    _guard.addListener(_onSessionChanged);
    unawaited(_load());
  }

  @override
  void dispose() {
    _generation++;
    _guard.removeListener(_onSessionChanged);
    super.dispose();
  }

  void _onSessionChanged() {
    _generation++;
    _record = null;
    _message = null;
    _loading = false;
    if (!mounted) {
      return;
    }
    setState(() {});
    if (_guard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  Future<void> _load() async {
    final generation = ++_generation;
    final revision = _guard.revision;
    if (!mounted) {
      return;
    }
    setState(() {
      _loading = true;
      _record = null;
      _message = null;
    });
    try {
      final record = await widget.dataSource.getForPatient(
        patientId: widget.patientId,
        consultationId: widget.consultationId,
      );
      if (!mounted ||
          generation != _generation ||
          _guard.revision != revision ||
          !_guard.canAccessProtectedRoutes) {
        return;
      }
      setState(() {
        _record = record;
        _loading = false;
      });
    } on Object {
      if (!mounted ||
          generation != _generation ||
          _guard.revision != revision) {
        return;
      }
      setState(() {
        _loading = false;
        _message = 'Consultation record unavailable.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final record = _record;
    return Scaffold(
      appBar: AppBar(title: const Text('Consultation record')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : record == null
          ? Center(
              child: Text(
                _message ?? 'Session unavailable.',
                key: const ValueKey('p23c-detail-unavailable'),
              ),
            )
          : ListView(
              key: const ValueKey('p23c-detail-loaded'),
              padding: const EdgeInsets.all(16),
              children: [
                if (widget.onClinicalActionsRequested != null)
                  ElevatedButton(
                    key: const ValueKey('p23c-open-clinical-note'),
                    onPressed: widget.onClinicalActionsRequested,
                    child: const Text('Open clinical note workflow'),
                  ),
                Text('Consultation #${record.id}'),
                Text('Patient #${record.patientId} · Staff #${record.staffId}'),
                Text('Status: ${record.status.name}'),
                Text(
                  'Record: ${record.recordStatus.name} · v${record.recordVersion}',
                ),
                if (record.appointmentId != null)
                  Text('Appointment #${record.appointmentId}'),
                if (record.chiefComplaint != null)
                  Text('Complaint: ${record.chiefComplaint}'),
                if (record.symptoms != null)
                  Text('Symptoms: ${record.symptoms}'),
                if (record.diagnosis != null)
                  Text('Diagnosis: ${record.diagnosis}'),
                if (record.treatmentPlan != null)
                  Text('Plan: ${record.treatmentPlan}'),
                const Text(
                  'Read-only record. Clinical note mutations require a separate guarded workflow.',
                ),
              ],
            ),
    );
  }
}
