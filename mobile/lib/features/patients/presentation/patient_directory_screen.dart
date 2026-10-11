import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';

class PatientDirectoryScreen extends StatefulWidget {
  const PatientDirectoryScreen({
    super.key,
    required this.dataSource,
    required this.bearerToken,
  });

  final PatientRemoteDataSource dataSource;
  final String bearerToken;

  @override
  State<PatientDirectoryScreen> createState() => _PatientDirectoryScreenState();
}

class _PatientDirectoryScreenState extends State<PatientDirectoryScreen> {
  static const int _pageSize = 25;

  final TextEditingController _searchController = TextEditingController();
  Timer? _searchDebounce;
  List<Patient> _patients = <Patient>[];
  int _page = 0;
  int? _total;
  int _requestGeneration = 0;
  bool _loadingInitial = true;
  bool _loadingMore = false;
  String? _errorMessage;
  Object? _lastError;

  @override
  void initState() {
    super.initState();
    unawaited(_loadPage(reset: true));
  }

  @override
  void dispose() {
    _searchDebounce?.cancel();
    _searchController.dispose();
    super.dispose();
  }

  void _onSearchChanged(String _) {
    setState(() {});
    _searchDebounce?.cancel();
    _searchDebounce = Timer(
      const Duration(milliseconds: 350),
      () => unawaited(_loadPage(reset: true)),
    );
  }

  Future<void> _loadPage({required bool reset}) async {
    if (!reset && (_loadingInitial || _loadingMore)) return;

    final generation = ++_requestGeneration;
    final requestedPage = reset ? 1 : _page + 1;
    setState(() {
      _errorMessage = null;
      _lastError = null;
      if (reset) {
        _loadingInitial = true;
        _loadingMore = false;
        _patients = <Patient>[];
        _page = 0;
        _total = null;
      } else {
        _loadingMore = true;
      }
    });

    try {
      final result = await widget.dataSource.fetchPatients(
        bearerToken: widget.bearerToken,
        search: _searchController.text,
        page: requestedPage,
        perPage: _pageSize,
        activeOnly: true,
      );

      if (!mounted || generation != _requestGeneration) return;
      setState(() {
        _patients = reset
            ? result.items
            : <Patient>[..._patients, ...result.items];
        _page = result.page;
        _total = result.total;
        _loadingInitial = false;
        _loadingMore = false;
      });
    } catch (error) {
      if (!mounted || generation != _requestGeneration) return;
      final rejected = error is ApiException && error.statusCode == 401;
      setState(() {
        _lastError = error;
        _errorMessage = _messageFor(error);
        _loadingInitial = false;
        _loadingMore = false;
        if (rejected) {
          _patients = <Patient>[];
          _total = 0;
        }
      });
    }
  }

  String _messageFor(Object error) {
    if (widget.bearerToken.trim().isEmpty) {
      return 'No saved access token was provided. Return to sign in and establish a session.';
    }
    if (error is ApiException) {
      if (error.statusCode == 401) {
        return 'The backend rejected this session (HTTP 401). Return to sign in, sign out, and authenticate again.';
      }
      if (error.statusCode == 403) {
        return 'Your account is not permitted to view the patient directory.';
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
    return 'Unable to load patients. Check the connection and retry.';
  }

  bool get _needsSignIn =>
      widget.bearerToken.trim().isEmpty ||
      (_lastError is ApiException &&
          (_lastError as ApiException).statusCode == 401);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Patient directory')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: TextField(
              key: const ValueKey('patient-search-field'),
              controller: _searchController,
              onChanged: _onSearchChanged,
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                labelText: 'Search patients',
                hintText: 'Name or patient number',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: _searchController.text.isEmpty
                    ? null
                    : IconButton(
                        tooltip: 'Clear search',
                        icon: const Icon(Icons.clear),
                        onPressed: () {
                          _searchController.clear();
                          _onSearchChanged('');
                          setState(() {});
                        },
                      ),
                border: const OutlineInputBorder(),
              ),
            ),
          ),
          if (_total != null && _errorMessage == null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text('$_total active patient${_total == 1 ? '' : 's'}'),
              ),
            ),
          Expanded(child: _buildResults()),
          if (_patients.isNotEmpty && _errorMessage != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Column(
                children: [
                  Text(_errorMessage!, textAlign: TextAlign.center),
                  TextButton(
                    onPressed: () => unawaited(_loadPage(reset: !_needsSignIn)),
                    child: Text(_needsSignIn ? 'Return to sign in' : 'Retry'),
                  ),
                ],
              ),
            ),
          if (_patients.isNotEmpty &&
              _total != null &&
              _patients.length < _total! &&
              _errorMessage == null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
              child: SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  key: const ValueKey('patient-load-more-button'),
                  onPressed: _loadingMore
                      ? null
                      : () => unawaited(_loadPage(reset: false)),
                  icon: _loadingMore
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.expand_more),
                  label: Text(_loadingMore ? 'Loading...' : 'Load more'),
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildResults() {
    if (_loadingInitial) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircularProgressIndicator(
              key: ValueKey('patient-directory-loading'),
            ),
            SizedBox(height: 12),
            Text('Loading patients...'),
          ],
        ),
      );
    }

    if (_patients.isEmpty && _errorMessage != null) {
      return Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.warning_amber_rounded, size: 42),
              const SizedBox(height: 12),
              Text(_errorMessage!, textAlign: TextAlign.center),
              const SizedBox(height: 12),
              if (_needsSignIn)
                ElevatedButton(
                  onPressed: () => context.go('/login'),
                  child: const Text('Return to sign in'),
                )
              else
                ElevatedButton.icon(
                  onPressed: () => unawaited(_loadPage(reset: true)),
                  icon: const Icon(Icons.refresh),
                  label: const Text('Retry'),
                ),
            ],
          ),
        ),
      );
    }

    if (_patients.isEmpty) {
      final hasSearch = _searchController.text.trim().isNotEmpty;
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                hasSearch ? Icons.search_off : Icons.people_outline,
                size: 48,
              ),
              const SizedBox(height: 12),
              Text(
                hasSearch ? 'No matching patients' : 'No active patients found',
              ),
              if (hasSearch)
                TextButton(
                  onPressed: () {
                    _searchController.clear();
                    _onSearchChanged('');
                    setState(() {});
                  },
                  child: const Text('Clear search'),
                ),
            ],
          ),
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: () => _loadPage(reset: true),
      child: ListView.separated(
        key: const ValueKey('patient-directory-list'),
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
        itemCount: _patients.length,
        separatorBuilder: (_, _) => const Divider(height: 1),
        itemBuilder: (context, index) {
          final patient = _patients[index];
          return ListTile(
            key: ValueKey('patient-row-${patient.id}'),
            leading: CircleAvatar(
              child: Text(patient.firstName[0].toUpperCase()),
            ),
            title: Text(patient.fullName),
            subtitle: Text('Patient no. ${patient.patientNumber}'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => unawaited(
              context.push<void>(
                '/patients/${patient.id}',
                extra: widget.bearerToken,
              ),
            ),
          );
        },
      ),
    );
  }
}
