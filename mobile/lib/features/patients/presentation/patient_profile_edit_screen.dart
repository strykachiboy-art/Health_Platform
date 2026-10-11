import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';

class PatientProfileEditScreen extends StatefulWidget {
  const PatientProfileEditScreen({
    super.key,
    required this.dataSource,
    required this.bearerToken,
    required this.patient,
  });

  final PatientRemoteDataSource dataSource;
  final String bearerToken;
  final Patient patient;

  @override
  State<PatientProfileEditScreen> createState() =>
      _PatientProfileEditScreenState();
}

class _PatientProfileEditScreenState extends State<PatientProfileEditScreen> {
  static const _editableFields = <String>{
    'first_name',
    'last_name',
    'date_of_birth',
    'gender',
    'blood_type',
    'phone',
    'email',
    'address',
    'allergies',
    'chronic_conditions',
    'emirates_id',
    'umrn',
  };

  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _firstName;
  late final TextEditingController _lastName;
  late final TextEditingController _phone;
  late final TextEditingController _email;
  late final TextEditingController _address;
  late final TextEditingController _allergies;
  late final TextEditingController _chronicConditions;
  late final TextEditingController _emiratesId;
  late final TextEditingController _umrn;

  DateTime? _dateOfBirth;
  String? _gender;
  String? _bloodType;
  bool _saving = false;
  bool _outcomeUnknown = false;
  bool _checkingOutcome = false;
  String? _formMessage;
  bool _formMessageIsError = true;
  Map<String, String> _serverErrors = <String, String>{};

  @override
  void initState() {
    super.initState();
    final patient = widget.patient;
    _firstName = TextEditingController(text: patient.firstName);
    _lastName = TextEditingController(text: patient.lastName);
    _phone = TextEditingController(text: patient.phone ?? '');
    _email = TextEditingController(text: patient.email ?? '');
    _address = TextEditingController(text: patient.address ?? '');
    _allergies = TextEditingController(text: patient.allergies ?? '');
    _chronicConditions = TextEditingController(
      text: patient.chronicConditions ?? '',
    );
    _emiratesId = TextEditingController(text: patient.emiratesId ?? '');
    _umrn = TextEditingController(text: patient.umrn ?? '');
    _dateOfBirth = patient.dateOfBirth == null
        ? null
        : DateTime(
            patient.dateOfBirth!.year,
            patient.dateOfBirth!.month,
            patient.dateOfBirth!.day,
          );
    _gender = _normalizeGender(patient.gender);
    _bloodType = _normalizeBloodType(patient.bloodType);
  }

  @override
  void dispose() {
    _firstName.dispose();
    _lastName.dispose();
    _phone.dispose();
    _email.dispose();
    _address.dispose();
    _allergies.dispose();
    _chronicConditions.dispose();
    _emiratesId.dispose();
    _umrn.dispose();
    super.dispose();
  }

  String? _normalizeText(String? value) {
    final normalized = value?.trim() ?? '';
    return normalized.isEmpty ? null : normalized;
  }

  String? _normalizeGender(String? value) {
    final normalized = _normalizeText(value)?.toLowerCase();
    return const <String>{'male', 'female', 'other'}.contains(normalized)
        ? normalized
        : null;
  }

  String? _normalizeBloodType(String? value) {
    if (value == null) return null;
    return switch (value.trim().toUpperCase()) {
      'A+' || 'A_POS' || 'A_POSITIVE' => 'A+',
      'A-' || 'A_NEG' || 'A_NEGATIVE' => 'A-',
      'B+' || 'B_POS' || 'B_POSITIVE' => 'B+',
      'B-' || 'B_NEG' || 'B_NEGATIVE' => 'B-',
      'AB+' || 'AB_POS' || 'AB_POSITIVE' => 'AB+',
      'AB-' || 'AB_NEG' || 'AB_NEGATIVE' => 'AB-',
      'O+' || 'O_POS' || 'O_POSITIVE' => 'O+',
      'O-' || 'O_NEG' || 'O_NEGATIVE' => 'O-',
      'UNKNOWN' => 'unknown',
      _ => null,
    };
  }

  String? _validateText(
    String? value, {
    required String label,
    bool required = false,
    int? maxLength,
    bool email = false,
  }) {
    final normalized = value?.trim() ?? '';
    if (required && normalized.isEmpty) return '$label is required.';
    if (maxLength != null && normalized.length > maxLength) {
      return '$label must be at most $maxLength characters.';
    }
    if (email &&
        normalized.isNotEmpty &&
        !RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(normalized)) {
      return 'Enter a valid email address.';
    }
    return null;
  }

  List<Widget> _serverError(String field) {
    final message = _serverErrors[field];
    if (message == null) return const <Widget>[];
    return [
      Padding(
        padding: const EdgeInsets.only(left: 12, top: 4, bottom: 8),
        child: Text(
          message,
          key: ValueKey('patient-profile-edit-server-error-$field'),
          style: TextStyle(color: Theme.of(context).colorScheme.error),
        ),
      ),
    ];
  }

  void _clearServerError(String field) {
    if (!_serverErrors.containsKey(field)) return;
    setState(() => _serverErrors.remove(field));
  }

  Widget _textField({
    required String keyName,
    required String serverField,
    required String label,
    required TextEditingController controller,
    bool required = false,
    int? maxLength,
    bool email = false,
    TextInputType? keyboardType,
    int maxLines = 1,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextFormField(
          key: ValueKey('patient-profile-edit-$keyName'),
          controller: controller,
          enabled: !_outcomeUnknown,
          keyboardType: keyboardType,
          maxLines: maxLines,
          maxLength: maxLength,
          textCapitalization:
              keyName == 'email' ||
                  keyName == 'phone' ||
                  keyName == 'umrn' ||
                  keyName == 'emirates-id'
              ? TextCapitalization.none
              : TextCapitalization.sentences,
          decoration: InputDecoration(
            labelText: label,
            border: const OutlineInputBorder(),
          ),
          validator: (value) => _validateText(
            value,
            label: label,
            required: required,
            maxLength: maxLength,
            email: email,
          ),
          onChanged: (_) => _clearServerError(serverField),
        ),
        ..._serverError(serverField),
      ],
    );
  }

  Future<void> _selectDateOfBirth() async {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final initial = _dateOfBirth ?? DateTime(now.year - 18, 1, 1);
    final picked = await showDatePicker(
      context: context,
      initialDate: initial.isAfter(today) ? today : initial,
      firstDate: DateTime(1800),
      lastDate: today,
      helpText: 'Select date of birth',
    );
    if (!mounted || picked == null) return;
    setState(() {
      _dateOfBirth = DateTime(picked.year, picked.month, picked.day);
      _serverErrors.remove('date_of_birth');
    });
  }

  Map<String, dynamic> _buildUpdates() {
    final patient = widget.patient;
    final updates = <String, dynamic>{};

    void addStringChange(String field, String? value, String? original) {
      final current = _normalizeText(value);
      final previous = _normalizeText(original);
      if (current != previous) updates[field] = current;
    }

    addStringChange('first_name', _firstName.text, patient.firstName);
    addStringChange('last_name', _lastName.text, patient.lastName);
    addStringChange('phone', _phone.text, patient.phone);
    addStringChange('email', _email.text, patient.email);
    addStringChange('address', _address.text, patient.address);
    addStringChange('allergies', _allergies.text, patient.allergies);
    addStringChange(
      'chronic_conditions',
      _chronicConditions.text,
      patient.chronicConditions,
    );
    addStringChange('emirates_id', _emiratesId.text, patient.emiratesId);
    addStringChange('umrn', _umrn.text, patient.umrn);

    final originalDob = patient.dateOfBirth == null
        ? null
        : DateTime(
            patient.dateOfBirth!.year,
            patient.dateOfBirth!.month,
            patient.dateOfBirth!.day,
          );
    if (_dateOfBirth != originalDob) {
      updates['date_of_birth'] = _dateOfBirth;
    }

    if (_normalizeGender(_gender) != _normalizeGender(patient.gender)) {
      updates['gender'] = _normalizeGender(_gender);
    }
    if (_normalizeBloodType(_bloodType) !=
        _normalizeBloodType(patient.bloodType)) {
      updates['blood_type'] = _normalizeBloodType(_bloodType);
    }

    return updates;
  }

  bool _isUnknownOutcome(Object error) {
    if (error is FormatException) return true;
    if (error is! ApiException) return error is! ArgumentError;
    return error.kind == ApiFailureKind.timeout ||
        error.kind == ApiFailureKind.connection ||
        error.kind == ApiFailureKind.invalidResponse ||
        (error.kind == ApiFailureKind.httpStatus &&
            (error.statusCode ?? 0) >= 500);
  }

  String _messageForRejectedUpdate(Object error) {
    if (error is ApiException) {
      if (error.statusCode == 401) {
        return 'Your session may have expired. Sign in again before editing this patient.';
      }
      if (error.statusCode == 403) {
        return 'Your account is not authorized to edit patient profiles.';
      }
      if (error.statusCode == 404) {
        return 'This patient could not be found. Return to the patient details and reload.';
      }
      if (error.statusCode == 409) {
        return 'The change conflicts with an existing record. Check the identifiers and try again.';
      }
      if (error.statusCode == 400 || error.statusCode == 422) {
        return 'The backend rejected these changes. Review the highlighted fields.';
      }
      if (error.kind == ApiFailureKind.httpStatus) {
        return 'The backend rejected the update (HTTP ${error.statusCode}).';
      }
    }
    if (error is ArgumentError) {
      return 'One or more values are invalid. Review the fields and try again.';
    }
    return 'The patient could not be updated. Review the fields and try again.';
  }

  Future<void> _submit() async {
    if (_saving || _outcomeUnknown || !_formKey.currentState!.validate()) {
      return;
    }

    final updates = _buildUpdates();
    if (updates.isEmpty) {
      setState(() {
        _formMessage = 'No changes to save.';
        _formMessageIsError = false;
      });
      return;
    }

    setState(() {
      _saving = true;
      _formMessage = null;
      _serverErrors = <String, String>{};
    });

    try {
      final updated = await widget.dataSource.updatePatient(
        bearerToken: widget.bearerToken,
        patientId: widget.patient.id,
        updates: updates,
      );
      if (!mounted) return;
      Navigator.of(context).pop<Patient>(updated);
    } catch (error) {
      if (!mounted) return;
      if (_isUnknownOutcome(error)) {
        setState(() {
          _outcomeUnknown = true;
          _formMessage = 'We could not confirm whether the update was saved. Do not submit it again yet. Check the server version below.';
          _formMessageIsError = true;
        });
      } else {
        final fieldErrors = error is ApiException
            ? error.validationErrors
            : const <String, String>{};
        setState(() {
          _serverErrors = Map<String, String>.fromEntries(
            fieldErrors.entries.where(
              (entry) => _editableFields.contains(entry.key),
            ),
          );
          _formMessage = _messageForRejectedUpdate(error);
          _formMessageIsError = true;
        });
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _checkServerState() async {
    if (_checkingOutcome) return;
    setState(() {
      _checkingOutcome = true;
      _formMessage = null;
    });
    try {
      final current = await widget.dataSource.fetchPatient(
        bearerToken: widget.bearerToken,
        patientId: widget.patient.id,
      );
      if (!mounted) return;
      Navigator.of(context).pop<Patient>(current);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _formMessage = error is ApiException && error.statusCode == 401
            ? 'The server version could not be read because the session was rejected. Return to sign in.'
            : 'The current server version could not be loaded. Retry checking the server state before submitting another update.';
        _formMessageIsError = true;
      });
    } finally {
      if (mounted) setState(() => _checkingOutcome = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Edit patient')),
      body: SafeArea(
        child: Form(
          key: _formKey,
          child: SingleChildScrollView(
            key: const ValueKey('patient-profile-edit-scroll'),
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  'Update authorized patient profile fields. Patient number and status cannot be changed here.',
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
                const SizedBox(height: 16),
                if (_formMessage != null) ...[
                  Semantics(
                    liveRegion: true,
                    child: Text(
                      _formMessage!,
                      key: const ValueKey('patient-profile-edit-message'),
                      style: TextStyle(
                        color: _formMessageIsError
                            ? colors.error
                            : colors.primary,
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),
                ],
                if (_outcomeUnknown) ...[
                  FilledButton.tonalIcon(
                    key: const ValueKey(
                      'patient-profile-edit-check-server-state',
                    ),
                    onPressed: _checkingOutcome ? null : _checkServerState,
                    icon: _checkingOutcome
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.sync),
                    label: Text(
                      _checkingOutcome
                          ? 'Checking server...'
                          : 'Check server state',
                    ),
                  ),
                  const SizedBox(height: 12),
                ],
                _textField(
                  keyName: 'first-name',
                  serverField: 'first_name',
                  label: 'First name',
                  controller: _firstName,
                  required: true,
                  maxLength: 80,
                ),
                const SizedBox(height: 12),
                _textField(
                  keyName: 'last-name',
                  serverField: 'last_name',
                  label: 'Last name',
                  controller: _lastName,
                  required: true,
                  maxLength: 80,
                ),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  key: const ValueKey('patient-profile-edit-date-of-birth'),
                  onPressed: _outcomeUnknown ? null : _selectDateOfBirth,
                  icon: const Icon(Icons.calendar_today),
                  label: Text(
                    _dateOfBirth == null
                        ? 'Date of birth (optional)'
                        : 'Date of birth: ${_dateOnly(_dateOfBirth!)}',
                  ),
                ),
                if (_dateOfBirth != null)
                  Align(
                    alignment: Alignment.centerRight,
                    child: TextButton(
                      key: const ValueKey(
                        'patient-profile-edit-clear-date-of-birth',
                      ),
                      onPressed: _outcomeUnknown
                          ? null
                          : () => setState(() {
                              _dateOfBirth = null;
                              _serverErrors.remove('date_of_birth');
                            }),
                      child: const Text('Clear date of birth'),
                    ),
                  ),
                ..._serverError('date_of_birth'),
                const SizedBox(height: 12),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    DropdownButtonFormField<String>(
                      key: const ValueKey('patient-profile-edit-gender'),
                      initialValue: _gender,
                      decoration: const InputDecoration(
                        labelText: 'Gender (optional)',
                        border: OutlineInputBorder(),
                      ),
                      items: const [
                        DropdownMenuItem(value: 'male', child: Text('Male')),
                        DropdownMenuItem(
                          value: 'female',
                          child: Text('Female'),
                        ),
                        DropdownMenuItem(value: 'other', child: Text('Other')),
                      ],
                      onChanged: _outcomeUnknown
                          ? null
                          : (value) => setState(() {
                              _gender = value;
                              _serverErrors.remove('gender');
                            }),
                    ),
                    if (_gender != null)
                      Align(
                        alignment: Alignment.centerRight,
                        child: TextButton(
                          key: const ValueKey(
                            'patient-profile-edit-clear-gender',
                          ),
                          onPressed: _outcomeUnknown
                              ? null
                              : () => setState(() {
                                  _gender = null;
                                  _serverErrors.remove('gender');
                                }),
                          child: const Text('Clear gender'),
                        ),
                      ),
                    ..._serverError('gender'),
                  ],
                ),
                const SizedBox(height: 12),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    DropdownButtonFormField<String>(
                      key: const ValueKey('patient-profile-edit-blood-type'),
                      initialValue: _bloodType,
                      decoration: const InputDecoration(
                        labelText: 'Blood type (optional)',
                        border: OutlineInputBorder(),
                      ),
                      items: const [
                        DropdownMenuItem(value: 'A+', child: Text('A+')),
                        DropdownMenuItem(value: 'A-', child: Text('A-')),
                        DropdownMenuItem(value: 'B+', child: Text('B+')),
                        DropdownMenuItem(value: 'B-', child: Text('B-')),
                        DropdownMenuItem(value: 'AB+', child: Text('AB+')),
                        DropdownMenuItem(value: 'AB-', child: Text('AB-')),
                        DropdownMenuItem(value: 'O+', child: Text('O+')),
                        DropdownMenuItem(value: 'O-', child: Text('O-')),
                        DropdownMenuItem(
                          value: 'unknown',
                          child: Text('Unknown'),
                        ),
                      ],
                      onChanged: _outcomeUnknown
                          ? null
                          : (value) => setState(() {
                              _bloodType = value;
                              _serverErrors.remove('blood_type');
                            }),
                    ),
                    if (_bloodType != null)
                      Align(
                        alignment: Alignment.centerRight,
                        child: TextButton(
                          key: const ValueKey(
                            'patient-profile-edit-clear-blood-type',
                          ),
                          onPressed: _outcomeUnknown
                              ? null
                              : () => setState(() {
                                  _bloodType = null;
                                  _serverErrors.remove('blood_type');
                                }),
                          child: const Text('Clear blood type'),
                        ),
                      ),
                    ..._serverError('blood_type'),
                  ],
                ),
                const SizedBox(height: 12),
                _textField(
                  keyName: 'phone',
                  serverField: 'phone',
                  label: 'Phone (optional)',
                  controller: _phone,
                  maxLength: 30,
                  keyboardType: TextInputType.phone,
                ),
                const SizedBox(height: 12),
                _textField(
                  keyName: 'email',
                  serverField: 'email',
                  label: 'Email (optional)',
                  controller: _email,
                  email: true,
                  keyboardType: TextInputType.emailAddress,
                ),
                const SizedBox(height: 12),
                _textField(
                  keyName: 'address',
                  serverField: 'address',
                  label: 'Address (optional)',
                  controller: _address,
                  maxLength: 255,
                  maxLines: 2,
                ),
                const SizedBox(height: 12),
                _textField(
                  keyName: 'allergies',
                  serverField: 'allergies',
                  label: 'Allergies (optional)',
                  controller: _allergies,
                  maxLines: 2,
                ),
                const SizedBox(height: 12),
                _textField(
                  keyName: 'chronic-conditions',
                  serverField: 'chronic_conditions',
                  label: 'Chronic conditions (optional)',
                  controller: _chronicConditions,
                  maxLines: 2,
                ),
                const SizedBox(height: 12),
                _textField(
                  keyName: 'emirates-id',
                  serverField: 'emirates_id',
                  label: 'Emirates ID (optional)',
                  controller: _emiratesId,
                  maxLength: 50,
                ),
                const SizedBox(height: 12),
                _textField(
                  keyName: 'umrn',
                  serverField: 'umrn',
                  label: 'UMRN (optional)',
                  controller: _umrn,
                  maxLength: 50,
                ),
                const SizedBox(height: 20),
                FilledButton.icon(
                  key: const ValueKey('patient-profile-edit-save'),
                  onPressed: _saving || _outcomeUnknown ? null : _submit,
                  icon: _saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.save_outlined),
                  label: Text(_saving ? 'Saving changes...' : 'Save changes'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  String _dateOnly(DateTime value) {
    final year = value.year.toString().padLeft(4, '0');
    final month = value.month.toString().padLeft(2, '0');
    final day = value.day.toString().padLeft(2, '0');
    return '$year-$month-$day';
  }
}
