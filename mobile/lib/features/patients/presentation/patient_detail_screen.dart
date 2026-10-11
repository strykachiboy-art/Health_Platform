import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';

class PatientDetailScreen extends StatefulWidget {
  const PatientDetailScreen({
    super.key,
    required this.dataSource,
    required this.bearerToken,
    required this.patientId,
  });

  final PatientRemoteDataSource dataSource;
  final String bearerToken;
  final int patientId;

  @override
  State<PatientDetailScreen> createState() => _PatientDetailScreenState();
}

class _PatientDetailScreenState extends State<PatientDetailScreen> {
  Patient? _patient;
  Object? _lastError;
  String? _errorMessage;
  bool _loading = true;
  int _requestGeneration = 0;

  @override
  void initState() {
    super.initState();
    unawaited(_loadPatient());
  }

  Future<void> _loadPatient() async {
    final generation = ++_requestGeneration;
    setState(() {
      _loading = true;
      _lastError = null;
      _errorMessage = null;
    });

    try {
      final patient = await widget.dataSource.fetchPatient(
        bearerToken: widget.bearerToken,
        patientId: widget.patientId,
      );
      if (!mounted || generation != _requestGeneration) return;
      setState(() {
        _patient = patient;
        _loading = false;
      });
    } catch (error) {
      if (!mounted || generation != _requestGeneration) return;
      setState(() {
        _lastError = error;
        _errorMessage = _messageFor(error);
        _loading = false;
        if (error is ApiException && error.statusCode == 401) {
          _patient = null;
        }
      });
    }
  }

  String _messageFor(Object error) {
    if (widget.bearerToken.trim().isEmpty) {
      return 'No saved access token was provided. Return to sign in and establish a session.';
    }
    if (widget.patientId <= 0) {
      return 'This patient address is invalid.';
    }
    if (error is ApiException) {
      if (error.statusCode == 401) {
        return 'The backend rejected this session (HTTP 401). Return to sign in, sign out, and authenticate again.';
      }
      if (error.statusCode == 403) {
        return 'Your account is not permitted to view this patient.';
      }
      if (error.statusCode == 404) {
        return 'This patient could not be found.';
      }
      return switch (error.kind) {
        ApiFailureKind.timeout =>
          'The patient request timed out. Check the connection and retry.',
        ApiFailureKind.connection =>
          'Cannot connect to the backend. Check the API address and network.',
        ApiFailureKind.httpStatus =>
          'The backend returned HTTP ${error.statusCode}. Retry or contact support.',
        ApiFailureKind.invalidResponse =>
          'The backend returned an invalid response. Retry or contact support.',
      };
    }
    if (error is FormatException) {
      return 'The backend returned incomplete or malformed patient data.';
    }
    return 'Unable to load this patient. Check the connection and retry.';
  }

  bool get _needsSignIn =>
      widget.bearerToken.trim().isEmpty ||
      (_lastError is ApiException &&
          (_lastError as ApiException).statusCode == 401);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Patient details')),
      body: _buildBody(),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircularProgressIndicator(key: ValueKey('patient-detail-loading')),
            SizedBox(height: 12),
            Text('Loading patient details...'),
          ],
        ),
      );
    }

    final patient = _patient;
    if (patient == null) {
      return Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.warning_amber_rounded, size: 42),
              const SizedBox(height: 12),
              Text(
                _errorMessage ?? 'Patient details are unavailable.',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 12),
              if (_needsSignIn)
                ElevatedButton(
                  onPressed: () => context.go('/login'),
                  child: const Text('Return to sign in'),
                )
              else
                ElevatedButton.icon(
                  onPressed: () => unawaited(_loadPatient()),
                  icon: const Icon(Icons.refresh),
                  label: const Text('Retry'),
                ),
            ],
          ),
        ),
      );
    }

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    CircleAvatar(
                      radius: 28,
                      child: Text(patient.firstName[0].toUpperCase()),
                    ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            patient.fullName,
                            key: const ValueKey('patient-detail-name'),
                            style: Theme.of(context).textTheme.titleLarge,
                          ),
                          const SizedBox(height: 4),
                          Text('Patient no. ${patient.patientNumber}'),
                          const SizedBox(height: 8),
                          Chip(
                            label: Text(
                              patient.isActive ? 'Active' : 'Inactive',
                            ),
                            avatar: Icon(
                              patient.isActive
                                  ? Icons.check_circle_outline
                                  : Icons.pause_circle_outline,
                              size: 18,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
        _section('Patient information', [
          _detail('Date of birth', _dateOnly(patient.dateOfBirth)),
          _detail('Gender', patient.gender),
          _detail('Blood type', patient.bloodType),
          _detail('Phone', patient.phone),
          _detail('Email', patient.email),
          _detail('Address', patient.address),
        ]),
        _section('Clinical information', [
          _detail('Allergies', patient.allergies),
          _detail('Chronic conditions', patient.chronicConditions),
        ]),
        _section('Identifiers', [
          _detail('Emirates ID', patient.emiratesId),
          _detail('UMRN', patient.umrn),
        ]),
      ],
    );
  }

  Widget _section(String title, List<Widget?> details) {
    final visibleDetails = details.whereType<Widget>().toList(growable: false);
    if (visibleDetails.isEmpty) return const SizedBox.shrink();
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
              child: Text(
                title,
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ),
            ...visibleDetails,
          ],
        ),
      ),
    );
  }

  Widget? _detail(String label, String? value) {
    if (value == null || value.trim().isEmpty) return null;
    return ListTile(dense: true, title: Text(label), subtitle: Text(value));
  }

  String? _dateOnly(DateTime? value) {
    if (value == null) return null;
    return value.toIso8601String().split('T').first;
  }
}
