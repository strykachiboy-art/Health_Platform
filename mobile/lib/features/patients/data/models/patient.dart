/// Validated read model returned by the existing patient API.
///
/// Sensitive fields are available for the authorized detail view; the directory
/// intentionally renders only the patient's name and patient number.
class Patient {
  const Patient({
    required this.id,
    required this.clinicId,
    required this.patientNumber,
    required this.firstName,
    required this.lastName,
    required this.dateOfBirth,
    required this.gender,
    required this.bloodType,
    required this.phone,
    required this.email,
    required this.address,
    required this.allergies,
    required this.chronicConditions,
    required this.emiratesId,
    required this.umrn,
    required this.isActive,
    required this.createdAt,
    required this.updatedAt,
  });

  final int id;
  final int clinicId;
  final String patientNumber;
  final String firstName;
  final String lastName;
  final DateTime? dateOfBirth;
  final String? gender;
  final String? bloodType;
  final String? phone;
  final String? email;
  final String? address;
  final String? allergies;
  final String? chronicConditions;
  final String? emiratesId;
  final String? umrn;
  final bool isActive;
  final DateTime createdAt;
  final DateTime updatedAt;

  String get fullName => '$firstName $lastName';

  factory Patient.fromJson(Map<String, dynamic> json) {
    return Patient(
      id: _positiveInt(json, 'id'),
      clinicId: _positiveInt(json, 'clinic_id'),
      patientNumber: _requiredString(json, 'patient_number'),
      firstName: _requiredString(json, 'first_name'),
      lastName: _requiredString(json, 'last_name'),
      dateOfBirth: _optionalDate(json, 'date_of_birth'),
      gender: _optionalString(json, 'gender'),
      bloodType: _optionalString(json, 'blood_type'),
      phone: _optionalString(json, 'phone'),
      email: _optionalString(json, 'email'),
      address: _optionalString(json, 'address'),
      allergies: _optionalString(json, 'allergies'),
      chronicConditions: _optionalString(json, 'chronic_conditions'),
      emiratesId: _optionalString(json, 'emirates_id'),
      umrn: _optionalString(json, 'umrn'),
      isActive: _requiredBool(json, 'is_active'),
      createdAt: _requiredDateTime(json, 'created_at'),
      updatedAt: _requiredDateTime(json, 'updated_at'),
    );
  }

  static int _positiveInt(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is! int || value <= 0) {
      throw FormatException(
        'Patient field "$field" must be a positive integer.',
      );
    }
    return value;
  }

  static String _requiredString(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is! String || value.trim().isEmpty || value.trim() != value) {
      throw FormatException(
        'Patient field "$field" must be a non-empty string.',
      );
    }
    return value;
  }

  static String? _optionalString(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value == null) return null;
    if (value is! String) {
      throw FormatException('Patient field "$field" must be a string or null.');
    }
    return value;
  }

  static bool _requiredBool(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is! bool) {
      throw FormatException('Patient field "$field" must be a boolean.');
    }
    return value;
  }

  static DateTime? _optionalDate(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value == null) return null;
    if (value is! String) {
      throw FormatException(
        'Patient field "$field" must be a date string or null.',
      );
    }
    try {
      return DateTime.parse(value);
    } on FormatException {
      throw FormatException('Patient field "$field" must be a valid date.');
    }
  }

  static DateTime _requiredDateTime(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is! String || value.isEmpty) {
      throw FormatException(
        'Patient field "$field" must be a date-time string.',
      );
    }
    try {
      return DateTime.parse(value);
    } on FormatException {
      throw FormatException(
        'Patient field "$field" must be a valid date-time.',
      );
    }
  }
}
