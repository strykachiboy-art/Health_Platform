// Surgery C: bounded, documentation-only forms over the pinned Surgery B API.
// No generic JSON editor, operative transition, or signed-document upload.

enum SurgeryFieldType { positiveId, boundedText, utcTime, affirmative, choice }

class SurgeryField {
  const SurgeryField(
    this.name,
    this.label,
    this.type, {
    this.maxLength = 500,
    this.choices = const <String>[],
    this.optional = false,
  });

  final String name;
  final String label;
  final SurgeryFieldType type;
  final int maxLength;
  final List<String> choices;
  final bool optional;
}

const _surgeon = SurgeryField(
  'surgeon_staff_id',
  'Surgeon staff ID',
  SurgeryFieldType.positiveId,
);
const _anesthetist = SurgeryField(
  'anesthetist_staff_id',
  'Anesthetist staff ID',
  SurgeryFieldType.positiveId,
);
const _version = SurgeryField(
  'expected_version',
  'Reviewed record version',
  SurgeryFieldType.positiveId,
);
const _bookingId = SurgeryField(
  'booking_id',
  'Booking ID',
  SurgeryFieldType.positiveId,
);
const _assessmentId = SurgeryField(
  'assessment_id',
  'Pre-anesthesia assessment ID',
  SurgeryFieldType.positiveId,
);
const _reportId = SurgeryField(
  'report_id',
  'Operative report ID',
  SurgeryFieldType.positiveId,
);
const _reason = SurgeryField('reason', 'Reason', SurgeryFieldType.boundedText);
const _start = SurgeryField(
  'starts_at',
  'Theatre start (ISO timezone)',
  SurgeryFieldType.utcTime,
);
const _end = SurgeryField(
  'ends_at',
  'Theatre end (ISO timezone)',
  SurgeryFieldType.utcTime,
);
const _yes = SurgeryFieldType.affirmative;
const _text = SurgeryFieldType.boundedText;
const _id = SurgeryFieldType.positiveId;
const _choice = SurgeryFieldType.choice;
const _time = SurgeryFieldType.utcTime;

const _signInChecks = <SurgeryField>[
  SurgeryField(
    'patient_identity_confirmed',
    'Patient identity confirmed',
    _yes,
  ),
  SurgeryField('procedure_site_confirmed', 'Procedure/site confirmed', _yes),
  SurgeryField(
    'signed_consent_evidence_confirmed',
    'Signed consent evidence reviewed',
    _yes,
  ),
  SurgeryField(
    'anesthesia_safety_reviewed',
    'Anesthesia safety reviewed',
    _yes,
  ),
  SurgeryField(
    'monitoring_equipment_ready',
    'Monitoring equipment ready',
    _yes,
  ),
];
const _timeOutChecks = <SurgeryField>[
  SurgeryField('whole_team_introduced', 'Entire team introduced', _yes),
  SurgeryField(
    'patient_procedure_site_reconfirmed',
    'Patient/procedure/site reconfirmed',
    _yes,
  ),
  SurgeryField('critical_events_discussed', 'Critical events discussed', _yes),
  SurgeryField(
    'infection_prevention_reviewed',
    'Infection prevention reviewed',
    _yes,
  ),
  SurgeryField('required_imaging_reviewed', 'Required imaging reviewed', _yes),
];
const _reportFields = <SurgeryField>[
  SurgeryField(
    'complication_status',
    'Complication status',
    _choice,
    choices: ['none_reported', 'documented'],
  ),
  SurgeryField('estimated_blood_loss_ml', 'Estimated blood loss (mL)', _id),
  SurgeryField(
    'preoperative_diagnosis',
    'Preoperative diagnosis',
    _text,
    maxLength: 1200,
  ),
  SurgeryField(
    'postoperative_diagnosis',
    'Postoperative diagnosis',
    _text,
    maxLength: 1200,
  ),
  SurgeryField(
    'procedure_narrative',
    'Procedure narrative',
    _text,
    maxLength: 6000,
  ),
  SurgeryField(
    'operative_findings',
    'Operative findings',
    _text,
    maxLength: 4000,
  ),
  SurgeryField('operative_course', 'Operative course', _text, maxLength: 6000),
  SurgeryField(
    'complication_notes',
    'Complication notes',
    _text,
    maxLength: 2000,
  ),
  SurgeryField('specimen_notes', 'Specimen notes', _text, maxLength: 2000),
  SurgeryField('implant_notes', 'Implant notes', _text, maxLength: 2000),
  SurgeryField(
    'postoperative_plan',
    'Postoperative plan',
    _text,
    maxLength: 3000,
  ),
];

enum SurgeryActionKind {
  book,
  reschedule,
  cancelBooking,
  withdrawConsent,
  signIn,
  timeOut,
  preanesthesia,
  baseline,
  attestPreanesthesia,
  reportRevise,
  reportSign;

  String get label => switch (this) {
    book => 'Schedule theatre',
    reschedule => 'Reschedule theatre',
    cancelBooking => 'Cancel theatre reservation',
    withdrawConsent => 'Withdraw consent evidence',
    signIn => 'Safety checklist: Sign-In',
    timeOut => 'Safety checklist: Time-Out',
    preanesthesia => 'Record pre-anesthesia assessment',
    baseline => 'Append pre-anesthesia baseline',
    attestPreanesthesia => 'Attest pre-anesthesia review',
    reportRevise => 'Revise unsigned operative report',
    reportSign => 'Sign operative documentation',
  };

  String get role => this == SurgeryActionKind.signIn ? 'nurse' : 'doctor';

  List<SurgeryField> get fields => switch (this) {
    book => const <SurgeryField>[
      SurgeryField('theatre_id', 'Operating theatre ID', _id),
      _start,
      _end,
    ],
    reschedule => const <SurgeryField>[_bookingId, _version, _start, _end],
    cancelBooking => const <SurgeryField>[_bookingId, _version, _reason],
    withdrawConsent => const <SurgeryField>[
      SurgeryField('consent_id', 'Consent evidence ID', _id),
      _version,
      _reason,
    ],
    signIn => const <SurgeryField>[_surgeon, _anesthetist, ..._signInChecks],
    timeOut => const <SurgeryField>[_surgeon, _anesthetist, ..._timeOutChecks],
    preanesthesia => const <SurgeryField>[
      _surgeon,
      SurgeryField('asa_class', 'ASA class (1-6)', _id),
      SurgeryField(
        'technique',
        'Technique',
        _choice,
        choices: ['general', 'regional', 'local', 'sedation'],
      ),
      SurgeryField(
        'airway_assessment',
        'Airway assessment',
        _choice,
        choices: ['routine', 'anticipated_difficult', 'unknown'],
      ),
      SurgeryField('airway_plan', 'Airway plan', _text, maxLength: 1000),
      SurgeryField(
        'allergy_status',
        'Allergy status',
        _choice,
        choices: ['known', 'none_known', 'unknown'],
      ),
      SurgeryField(
        'allergy_notes',
        'Allergy notes',
        _text,
        maxLength: 1000,
        optional: true,
      ),
      SurgeryField('fasting_confirmed', 'Fasting confirmed', _yes),
      SurgeryField('fasting_notes', 'Fasting notes', _text, maxLength: 1000),
      SurgeryField(
        'risk_and_comorbidity_notes',
        'Risks/comorbidities',
        _text,
        maxLength: 4000,
      ),
      SurgeryField('plan_summary', 'Plan summary', _text, maxLength: 4000),
      SurgeryField(
        'monitoring_plan',
        'Monitoring plan',
        _text,
        maxLength: 2000,
      ),
      SurgeryField(
        'contingency_plan',
        'Contingency plan',
        _text,
        maxLength: 2000,
      ),
    ],
    baseline => const <SurgeryField>[
      _assessmentId,
      _version,
      SurgeryField('observed_at', 'Observed at (ISO timezone)', _time),
      SurgeryField('heart_rate_bpm', 'Heart rate (bpm)', _id),
      SurgeryField('systolic_bp', 'Systolic BP', _id),
      SurgeryField('diastolic_bp', 'Diastolic BP', _id),
      SurgeryField('spo2_percent', 'SpO2 percent', _id),
      SurgeryField('respiratory_rate', 'Respiratory rate', _id),
      SurgeryField('temperature_tenths_c', 'Temperature (tenths °C)', _id),
      SurgeryField(
        'oxygen_delivery',
        'Oxygen delivery',
        _choice,
        choices: ['ambient_air', 'supplemental'],
      ),
      SurgeryField(
        'observation_notes',
        'Observation notes',
        _text,
        maxLength: 1000,
      ),
    ],
    attestPreanesthesia => const <SurgeryField>[_assessmentId, _version],
    reportRevise => const <SurgeryField>[_reportId, _version, ..._reportFields],
    reportSign => const <SurgeryField>[
      _reportId,
      _version,
      SurgeryField(
        'signer_kind',
        'Signing role',
        _choice,
        choices: ['surgeon', 'anesthetist'],
      ),
      SurgeryField(
        'attestation_note',
        'Signature attestation note',
        _text,
        maxLength: 800,
      ),
    ],
  };

  String path(int caseId, int? resourceId) => switch (this) {
    book => 'api/v1/surgery/bookings',
    reschedule => 'api/v1/surgery/bookings/$resourceId',
    cancelBooking => 'api/v1/surgery/bookings/$resourceId/cancel',
    withdrawConsent => 'api/v1/surgery/consents/$resourceId/withdraw',
    signIn => 'api/v1/surgery/cases/$caseId/checklists/sign_in',
    timeOut => 'api/v1/surgery/cases/$caseId/checklists/time_out',
    preanesthesia => 'api/v1/surgery/cases/$caseId/preanesthesia',
    baseline => 'api/v1/surgery/anesthesia/$resourceId/baselines',
    attestPreanesthesia => 'api/v1/surgery/anesthesia/$resourceId/attest',
    reportRevise => 'api/v1/surgery/reports/$resourceId',
    reportSign => 'api/v1/surgery/reports/$resourceId/sign',
  };

  bool get usePatch =>
      this == SurgeryActionKind.reschedule ||
      this == SurgeryActionKind.reportRevise;
  String? get resourceField => switch (this) {
    reschedule || cancelBooking => 'booking_id',
    withdrawConsent => 'consent_id',
    baseline || attestPreanesthesia => 'assessment_id',
    reportRevise || reportSign => 'report_id',
    _ => null,
  };
}

class SurgeryActionInput {
  const SurgeryActionInput({
    required this.caseId,
    required this.kind,
    required this.raw,
  });
  final int caseId;
  final SurgeryActionKind kind;
  final Map<String, String> raw;

  Map<String, dynamic> encode() {
    if (caseId <= 0 ||
        caseId > 2147483647 ||
        raw.keys
            .toSet()
            .difference(kind.fields.map((f) => f.name).toSet())
            .isNotEmpty) {
      throw const FormatException(
        'Unrecognized surgery action identity or fields.',
      );
    }
    final payload = <String, dynamic>{};
    final answers = <String, bool>{};
    for (final field in kind.fields) {
      final value = raw[field.name];
      if ((value == null || value.isEmpty) && field.optional) {
        if (field.name == 'allergy_notes') payload[field.name] = null;
        continue;
      }
      if (value == null || value.isEmpty || value != value.trim()) {
        throw const FormatException(
          'Required Surgery documentation is incomplete.',
        );
      }
      Object parsed;
      switch (field.type) {
        case SurgeryFieldType.positiveId:
          final integer = int.tryParse(value);
          final min =
              field.name == 'spo2_percent' ||
                  field.name == 'estimated_blood_loss_ml'
              ? 0
              : 1;
          if (integer == null ||
              integer < min ||
              integer > 2147483647 ||
              integer.toString() != value) {
            throw const FormatException('Invalid numerical Surgery evidence.');
          }
          parsed = integer;
        case SurgeryFieldType.boundedText:
          if (value.length > field.maxLength) {
            throw const FormatException(
              'Surgery documentation exceeds the permitted limit.',
            );
          }
          parsed = value;
        case SurgeryFieldType.utcTime:
          final time = DateTime.tryParse(value);
          if (time == null ||
              !RegExp(r'(Z|[+-][0-9]{2}:[0-9]{2})$').hasMatch(value)) {
            throw const FormatException(
              'A timezone-qualified timestamp is required.',
            );
          }
          parsed = value;
        case SurgeryFieldType.choice:
          if (!field.choices.contains(value)) {
            throw const FormatException('Unknown Surgery documentation value.');
          }
          parsed = value;
        case SurgeryFieldType.affirmative:
          if (value != 'true') {
            throw const FormatException(
              'Every required clinical confirmation must be affirmative.',
            );
          }
          parsed = true;
      }
      if (field.type == SurgeryFieldType.affirmative &&
          (kind == SurgeryActionKind.signIn ||
              kind == SurgeryActionKind.timeOut)) {
        answers[field.name] = true;
      } else {
        payload[field.name] = parsed;
      }
    }
    if (answers.isNotEmpty) payload['answers'] = answers;
    if (kind.resourceField case final String resource) payload.remove(resource);
    if (kind == SurgeryActionKind.book) payload['surgical_case_id'] = caseId;
    if (kind == SurgeryActionKind.preanesthesia) {
      if ((payload['asa_class'] as int) > 6) {
        throw const FormatException(
          'ASA class is outside the permitted range.',
        );
      }
    }
    if (kind == SurgeryActionKind.baseline) {
      final systolic = payload['systolic_bp'] as int;
      final diastolic = payload['diastolic_bp'] as int;
      if (systolic < diastolic ||
          (payload['spo2_percent'] as int) > 100 ||
          (payload['temperature_tenths_c'] as int) < 200 ||
          (payload['temperature_tenths_c'] as int) > 450) {
        throw const FormatException(
          'Pre-anesthesia baseline values are invalid.',
        );
      }
    }
    return Map<String, dynamic>.unmodifiable(payload);
  }

  int? get resourceId {
    final field = kind.resourceField;
    if (field == null) {
      return null;
    }
    final id = int.tryParse(raw[field] ?? '');
    if (id == null || id <= 0 || id.toString() != raw[field]) {
      throw const FormatException('Surgery resource ID is invalid.');
    }
    return id;
  }
}
