import 'package:flutter/material.dart';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/patients/data/models/patient.dart';
import 'package:clinic_system_pro/features/patients/data/patient_remote_data_source.dart';

class PatientRegistrationScreen extends StatefulWidget {
  const PatientRegistrationScreen({
    super.key,
    required this.dataSource,
    required this.bearerToken,
  });

  final PatientRemoteDataSource dataSource;
  final String bearerToken;

  @override
  State<PatientRegistrationScreen> createState() =>
      _PatientRegistrationScreenState();
}

class _PatientRegistrationScreenState extends State<PatientRegistrationScreen> {
  final _formKey = GlobalKey<FormState>();

  final _firstName = TextEditingController();
  final _lastName = TextEditingController();
  final _phone = TextEditingController();
  final _email = TextEditingController();
  final _address = TextEditingController();
  final _allergies = TextEditingController();
  final _chronicConditions = TextEditingController();
  final _emiratesId = TextEditingController();
  final _umrn = TextEditingController();

  DateTime? _dateOfBirth;
  String? _gender;
  String? _bloodType;

  bool _submitting = false;
  bool _outcomeUnknown = false;
  Patient? _createdPatient;
  String? _formMessage;
  Map<String, String> _serverErrors = <String, String>{};

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

  String? _validateText(
    String? value, {
    required String label,
    bool required = false,
    int? maxLength,
  }) {
    final normalized = value?.trim() ?? '';

    if (required && normalized.isEmpty) {
      return '$label is required.';
    }

    if (maxLength != null && normalized.length > maxLength) {
      return '$label must be at most $maxLength characters.';
    }

    return null;
  }

  void _clearServerError(String field) {
    if (!_serverErrors.containsKey(field)) return;

    setState(() {
      _serverErrors.remove(field);
    });
  }

  List<Widget> _serverError(String field) {
    final message = _serverErrors[field];
    if (message == null) return const <Widget>[];

    return [
      Padding(
        padding: const EdgeInsets.only(left: 12, top: 4, bottom: 8),
        child: Text(
          message,
          key: ValueKey('patient-registration-server-error-$field'),
          style: TextStyle(color: Theme.of(context).colorScheme.error),
        ),
      ),
    ];
  }

  Widget _textField({
    required String keyName,
    required String serverField,
    required String label,
    required TextEditingController controller,
    bool required = false,
    int? maxLength,
    int maxLines = 1,
    TextInputType? keyboardType,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextFormField(
          key: ValueKey('patient-registration-$keyName'),
          controller: controller,
          keyboardType: keyboardType,
          maxLines: maxLines,
          maxLength: maxLength,
          textCapitalization:
              keyName == 'email' ||
                  keyName == 'phone' ||
                  keyName == 'umrn' ||
                  keyName == 'emirates-id'
              ? TextCapitalization.none
              : TextCapitalization.words,
          decoration: InputDecoration(
            labelText: label,
            border: const OutlineInputBorder(),
          ),
          validator: (value) => _validateText(
            value,
            label: label,
            required: required,
            maxLength: maxLength,
          ),
          onChanged: (_) => _clearServerError(serverField),
        ),
        ..._serverError(serverField),
      ],
    );
  }

  Future<void> _selectDateOfBirth() async {
    final today = DateTime.now();
    final initialDate = _dateOfBirth ?? DateTime(today.year - 18, 1, 1);

    final picked = await showDatePicker(
      context: context,
      initialDate: initialDate,
      firstDate: DateTime(1900),
      lastDate: DateTime(today.year, today.month, today.day),
      helpText: 'Select date of birth',
    );

    if (!mounted || picked == null) return;

    setState(() {
      _dateOfBirth = DateTime(picked.year, picked.month, picked.day);
      _serverErrors.remove('date_of_birth');
    });
  }

  Widget _dateOfBirthField() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        OutlinedButton.icon(
          key: const ValueKey('patient-registration-date-of-birth'),
          onPressed: _selectDateOfBirth,
          icon: const Icon(Icons.calendar_today),
          label: Text(
            _dateOfBirth == null
                ? 'Select date of birth (optional)'
                : 'Date of birth: ${_dateOnly(_dateOfBirth!)}',
          ),
        ),
        if (_dateOfBirth != null)
          Align(
            alignment: Alignment.centerRight,
            child: TextButton(
              key: const ValueKey('patient-registration-clear-date-of-birth'),
              onPressed: () {
                setState(() {
                  _dateOfBirth = null;
                  _serverErrors.remove('date_of_birth');
                });
              },
              child: const Text('Clear date'),
            ),
          ),
        ..._serverError('date_of_birth'),
      ],
    );
  }

  Widget _genderField() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        DropdownButtonFormField<String>(
          key: const ValueKey('patient-registration-gender'),
          initialValue: _gender,
          decoration: const InputDecoration(
            labelText: 'Gender (optional)',
            border: OutlineInputBorder(),
          ),
          items: const [
            DropdownMenuItem(value: 'male', child: Text('Male')),
            DropdownMenuItem(value: 'female', child: Text('Female')),
            DropdownMenuItem(value: 'other', child: Text('Other')),
          ],
          onChanged: (value) {
            setState(() => _gender = value);
            _clearServerError('gender');
          },
        ),
        ..._serverError('gender'),
      ],
    );
  }

  Widget _bloodTypeField() {
    const types = <String>[
      'A+',
      'A-',
      'B+',
      'B-',
      'AB+',
      'AB-',
      'O+',
      'O-',
      'unknown',
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        DropdownButtonFormField<String>(
          key: const ValueKey('patient-registration-blood-type'),
          initialValue: _bloodType,
          decoration: const InputDecoration(
            labelText: 'Blood type (optional)',
            border: OutlineInputBorder(),
          ),
          items: types
              .map(
                (type) => DropdownMenuItem(
                  value: type,
                  child: Text(type == 'unknown' ? 'Unknown' : type),
                ),
              )
              .toList(growable: false),
          onChanged: (value) {
            setState(() => _bloodType = value);
            _clearServerError('blood_type');
          },
        ),
        ..._serverError('blood_type'),
      ],
    );
  }

  String _dateOnly(DateTime value) {
    final year = value.year.toString().padLeft(4, '0');
    final month = value.month.toString().padLeft(2, '0');
    final day = value.day.toString().padLeft(2, '0');
    return '$year-$month-$day';
  }

  Future<void> _submit() async {
    if (_submitting || _outcomeUnknown) return;

    final form = _formKey.currentState;
    if (form == null || !form.validate()) return;

    setState(() {
      _submitting = true;
      _formMessage = null;
      _serverErrors = <String, String>{};
    });

    try {
      final patient = await widget.dataSource.createPatient(
        bearerToken: widget.bearerToken,
        firstName: _firstName.text,
        lastName: _lastName.text,
        dateOfBirth: _dateOfBirth,
        gender: _gender,
        bloodType: _bloodType,
        phone: _phone.text,
        email: _email.text,
        address: _address.text,
        allergies: _allergies.text,
        chronicConditions: _chronicConditions.text,
        emiratesId: _emiratesId.text,
        umrn: _umrn.text,
      );

      if (!mounted) return;

      setState(() {
        _createdPatient = patient;
        _formMessage = null;
      });
    } on ApiException catch (error) {
      if (!mounted) return;

      final status = error.statusCode;
      if (status == 422) {
        setState(() {
          _serverErrors = Map<String, String>.from(error.validationErrors);
          _formMessage = _serverErrors.isEmpty
              ? 'The backend rejected the submitted data. Review the form and try again.'
              : 'Please correct the highlighted fields and try again.';
        });
      } else if (status == 401) {
        setState(() {
          _formMessage = 'Your session is no longer accepted. Sign in again before registering a patient.';
        });
      } else if (status == 403) {
        setState(() {
          _formMessage = 'Your account is not permitted to register patients.';
        });
      } else if (error.kind == ApiFailureKind.timeout ||
          error.kind == ApiFailureKind.connection ||
          error.kind == ApiFailureKind.invalidResponse ||
          (status != null && status >= 500)) {
        setState(() {
          _outcomeUnknown = true;
          _formMessage = 'We could not confirm whether registration completed. Check the patient directory before trying again to avoid duplicates.';
        });
      } else {
        setState(() {
          _formMessage = status == null
              ? 'The registration request could not be completed.'
              : 'The backend rejected the registration (HTTP $status).';
        });
      }
    } on FormatException {
      if (!mounted) return;
      setState(() {
        _outcomeUnknown = true;
        _formMessage = 'The server response was incomplete, so registration could not be confirmed. Check the patient directory before trying again to avoid duplicates.';
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _outcomeUnknown = true;
        _formMessage = 'We could not confirm whether registration completed. Check the patient directory before trying again to avoid duplicates.';
      });
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final patient = _createdPatient;

    return Scaffold(
      appBar: AppBar(
        title: Text(
          patient == null ? 'Register patient' : 'Patient registration',
        ),
      ),
      body: patient == null ? _buildForm() : _buildSuccess(patient),
    );
  }

  Widget _buildForm() {
    return SafeArea(
      child: Form(
        key: _formKey,
        child: SingleChildScrollView(
          key: const ValueKey('patient-registration-scroll-list'),
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'Register a new patient',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 8),
              const Text(
                'First name and last name are required. Other fields are optional.',
              ),
              const SizedBox(height: 20),
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
              _dateOfBirthField(),
              const SizedBox(height: 12),
              _genderField(),
              const SizedBox(height: 12),
              _bloodTypeField(),
              const SizedBox(height: 12),
              _textField(
                keyName: 'phone',
                serverField: 'phone',
                label: 'Phone',
                controller: _phone,
                maxLength: 30,
                keyboardType: TextInputType.phone,
              ),
              const SizedBox(height: 12),
              _textField(
                keyName: 'email',
                serverField: 'email',
                label: 'Email',
                controller: _email,
                keyboardType: TextInputType.emailAddress,
              ),
              const SizedBox(height: 12),
              _textField(
                keyName: 'address',
                serverField: 'address',
                label: 'Address',
                controller: _address,
                maxLength: 255,
              ),
              const SizedBox(height: 12),
              _textField(
                keyName: 'allergies',
                serverField: 'allergies',
                label: 'Allergies',
                controller: _allergies,
                maxLines: 2,
              ),
              const SizedBox(height: 12),
              _textField(
                keyName: 'chronic-conditions',
                serverField: 'chronic_conditions',
                label: 'Chronic conditions',
                controller: _chronicConditions,
                maxLines: 2,
              ),
              const SizedBox(height: 12),
              _textField(
                keyName: 'emirates-id',
                serverField: 'emirates_id',
                label: 'Emirates ID',
                controller: _emiratesId,
                maxLength: 50,
              ),
              const SizedBox(height: 12),
              _textField(
                keyName: 'umrn',
                serverField: 'umrn',
                label: 'UMRN',
                controller: _umrn,
                maxLength: 50,
              ),
              if (_formMessage != null) ...[
                const SizedBox(height: 12),
                Text(
                  _formMessage!,
                  key: const ValueKey('patient-registration-form-error'),
                  textAlign: TextAlign.center,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ],
              const SizedBox(height: 20),
              if (_outcomeUnknown)
                OutlinedButton(
                  key: const ValueKey('patient-registration-return-button'),
                  onPressed: () => Navigator.of(context).maybePop(),
                  child: const Text('Return to patient directory'),
                )
              else
                ElevatedButton.icon(
                  key: const ValueKey('patient-registration-submit-button'),
                  onPressed: _submitting ? null : _submit,
                  icon: _submitting
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.person_add_alt_1),
                  label: Text(
                    _submitting ? 'Registering...' : 'Register patient',
                  ),
                ),
              const SizedBox(height: 32),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildSuccess(Patient patient) {
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.check_circle_outline, size: 56),
            const SizedBox(height: 16),
            Text(
              'Patient registered',
              key: const ValueKey('patient-registration-success'),
              style: Theme.of(context).textTheme.titleLarge,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text('Patient no. ${patient.patientNumber}'),
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton.icon(
                key: const ValueKey(
                  'patient-registration-view-created-patient-button',
                ),
                onPressed: () => Navigator.of(context).pop<int>(patient.id),
                icon: const Icon(Icons.person_outline),
                label: const Text('View patient details'),
              ),
            ),
            TextButton(
              key: const ValueKey('patient-registration-success-back-button'),
              onPressed: () => Navigator.of(context).maybePop(),
              child: const Text('Back to directory'),
            ),
          ],
        ),
      ),
    );
  }
}
