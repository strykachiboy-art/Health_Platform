import 'blood_bank_contract.dart';

// Field declarations are the pinned request contracts, not clinical protocols.
// No clinical observation, consent, screening result or approval is preselected.
class BbField {
  const BbField(
    this.key, {
    this.label,
    this.kind = 'text',
    this.required = true,
    this.options = const [],
    this.max = 5000,
    this.children = const [],
  });
  final String key, kind;
  final String? label;
  final bool required;
  final List<String> options;
  final int max;
  final List<BbField> children;
  String get title => label ?? key.replaceAll('_', ' ');
}

const product = BbField(
  'product_type',
  kind: 'choice',
  options: bbProductTypes,
);
const group = BbField('blood_type', kind: 'choice', options: bbBloodTypes);
const reason = BbField('reason');
const notes = BbField('notes', required: false);
const ref = BbField('verification_reference', max: 255);
const observations = [
  BbField('systolic_bp', kind: 'int'),
  BbField('diastolic_bp', kind: 'int'),
  BbField('pulse_bpm', kind: 'int'),
  BbField('temperature_c', kind: 'decimal'),
  BbField('oxygen_saturation', kind: 'decimal', required: false),
];
const postVitals = BbField(
  'post_vitals',
  kind: 'object',
  children: observations,
);
const componentFields = [
  BbField('product_identifier', max: 80),
  BbField('division_identifier', max: 40),
  BbField('unit_barcode', max: 128),
  product,
  BbField('volume_ml', kind: 'int', max: 2000),
  BbField('expires_at', kind: 'time'),
];
const panelFields = [
  BbField('marker', max: 80),
  BbField('result', kind: 'choice', options: bbScreenResults),
  BbField('evidence_reference', max: 255),
];
const bbPanel = BbField(
  'screening_results',
  kind: 'panel',
  children: panelFields,
);
const reserveFields = [
  BbField('blood_unit_id', kind: 'int'),
  BbField('expected_version', kind: 'int'),
  BbField('evidence_reference', max: 255),
  BbField('special_requirements_reference', max: 255),
];

List<BbField> bbFields(BbAction action) => switch (action) {
  BbAction.createSupplier => const [
    BbField('supplier_code', max: 80),
    BbField('name', max: 200),
    BbField('approval_reference', max: 255),
  ],
  BbAction.deactivateSupplier ||
  BbAction.discard ||
  BbAction.cancel ||
  BbAction.closePartial => const [reason],
  BbAction.receive => const [
    BbField('external_supplier_id', kind: 'int'),
    BbField('external_supplier_name', max: 200),
    BbField('external_reference', required: false, max: 128),
    BbField('din', max: 64),
    ...componentFields,
    group,
    BbField('collected_at', kind: 'time'),
    notes,
  ],
  BbAction.returnUnit => const [
    BbField('inspection_reference', max: 255),
    reason,
  ],
  BbAction.threshold => const [
    product,
    group,
    BbField('minimum_units', kind: 'count', max: 1000),
    BbField('critical_units', kind: 'count', max: 1000),
  ],
  BbAction.registerDonor => const [
    BbField('donor_code', max: 64),
    BbField('full_name', max: 200),
    BbField('phone', max: 40),
    BbField('email', required: false, max: 255),
    BbField('date_of_birth', kind: 'date', required: false),
    BbField(
      'blood_type',
      kind: 'choice',
      options: [...bbBloodTypes, 'unknown'],
    ),
    BbField('cooldown_days', kind: 'int', max: 365),
    BbField('consent_to_contact', kind: 'bool'),
    BbField('consent_reference', required: false, max: 255),
    notes,
  ],
  BbAction.deferral => const [
    BbField(
      'deferral_status',
      kind: 'choice',
      options: [
        'eligible',
        'temporary_deferral',
        'permanent_deferral',
        'review_required',
      ],
    ),
    BbField('deferral_reason', required: false),
    BbField('deferred_until', kind: 'time', required: false),
    BbField('consent_to_contact', kind: 'optionalBool', required: false),
    BbField('consent_reference', required: false, max: 255),
  ],
  BbAction.donate => const [
    BbField('donor_id', kind: 'int'),
    BbField('donation_number', max: 64),
    BbField('volume_ml', kind: 'int', max: 1000),
    BbField('collected_at', kind: 'time'),
    BbField('hemoglobin_g_dl', kind: 'decimal'),
    BbField('systolic_bp', kind: 'int'),
    BbField('diastolic_bp', kind: 'int'),
    BbField('pulse_bpm', kind: 'int'),
    BbField('intake_passed', kind: 'bool'),
    BbField(
      'intake_notes',
      label: 'Documented eligibility assessment reference',
    ),
    BbField('replacement_for_order_id', kind: 'int', required: false),
    BbField('replacement_for_admission_id', kind: 'int', required: false),
    BbField('components', kind: 'rows', children: componentFields, max: 10),
  ],
  BbAction.recordArtifact => const [
    BbField('reference', max: 255),
    BbField(
      'kind',
      kind: 'choice',
      options: [
        'screening',
        'blood_group',
        'compatibility',
        'special_requirements',
        'confirmatory',
        'emergency_assessment',
      ],
    ),
    BbField('marker', max: 80),
    BbField('specimen_identifier', max: 128),
    BbField('test_code', max: 80),
    BbField('result', max: 80),
    BbField('source_reference', max: 255),
    BbField('observed_at', kind: 'time'),
    BbField('donation_id', kind: 'int', required: false),
    BbField('blood_unit_id', kind: 'int', required: false),
    BbField('order_id', kind: 'int', required: false),
    BbField('lab_order_item_id', kind: 'int', required: false),
  ],
  BbAction.verifyArtifact || BbAction.patientRelease => const [ref],
  BbAction.screen => const [
    BbField(
      'tti_screening_status',
      kind: 'choice',
      options: ['cleared', 'reactive', 'indeterminate'],
    ),
    BbField('tti_results', kind: 'panel', children: panelFields),
    BbField(
      'confirmed_blood_type',
      kind: 'choice',
      options: bbBloodTypes,
      required: false,
    ),
    BbField('blood_group_reference', required: false, max: 255),
    BbField('deferral_reason', required: false),
  ],
  BbAction.release => const [
    BbField('blood_group_reference', max: 255),
    bbPanel,
    BbField('release_reference', max: 255),
    BbField('label_check_reference', max: 255),
    BbField('storage_check_reference', max: 255),
  ],
  BbAction.createOrder => const [
    product,
    BbField('units_requested', kind: 'int', max: 100),
    BbField('indication', max: 10000),
    BbField('special_requirements', required: false, max: 10000),
    BbField('priority', kind: 'choice', options: ['routine', 'urgent', 'stat']),
    BbField('consultation_id', kind: 'int', required: false),
    BbField('scheduled_at', kind: 'time', required: false),
    BbField('emergency_release_requested', kind: 'bool'),
    BbField('emergency_request_reason', required: false, max: 10000),
    BbField('emergency_risk_acknowledged', kind: 'bool'),
  ],
  BbAction.compatibility => const [
    BbField('emergency_release', kind: 'bool'),
    BbField('crossmatch_reference', required: false, max: 128),
    BbField(
      'product_blood_type',
      kind: 'choice',
      options: bbBloodTypes,
      required: false,
    ),
    notes,
    BbField('emergency_release_reason', required: false, max: 10000),
  ],
  BbAction.reserve => const [
    BbField('units', kind: 'rows', children: reserveFields, max: 100),
  ],
  BbAction.issue => const [BbField('fulfillment_note', max: 10000)],
  BbAction.review => const [
    BbField('outcome', kind: 'choice', options: ['reviewed', 'escalated']),
    BbField('note', max: 10000),
  ],
  BbAction.followup => const [
    BbField('lab_order_item_id', kind: 'int'),
    BbField(
      'outcome',
      kind: 'choice',
      options: ['evidence_recorded', 'discrepancy_escalated'],
    ),
    BbField('evidence_note', max: 10000),
    BbField('escalation_note', required: false, max: 10000),
  ],
  BbAction.complete => const [postVitals, notes],
  BbAction.stop => const [
    postVitals,
    BbField('notes', label: 'Reason for stopping'),
  ],
  BbAction.reaction => const [
    BbField(
      'reaction_type',
      kind: 'choice',
      options: [
        'febrile_non_hemolytic',
        'allergic',
        'acute_hemolytic',
        'delayed_hemolytic',
        'trali',
        'taco',
        'sepsis_contamination',
        'other',
      ],
    ),
    BbField(
      'reaction_severity',
      kind: 'choice',
      options: ['mild', 'moderate', 'severe', 'life_threatening'],
    ),
    BbField('reaction_notes'),
    BbField(
      'post_vitals',
      kind: 'object',
      children: observations,
      required: false,
    ),
  ],
  BbAction.recall => const [
    reason,
    BbField('donation_id', kind: 'int', required: false),
    BbField('supplier_id', kind: 'int', required: false),
    BbField('din', required: false, max: 64),
  ],
  BbAction.investigation => const [
    BbField('operation_identity', max: 128),
    BbField(
      'outcome',
      kind: 'choice',
      options: ['evidence_recorded', 'escalated', 'reviewed'],
    ),
    BbField('notes'),
    BbField('donation_id', kind: 'int', required: false),
    BbField('recall_id', kind: 'int', required: false),
    BbField('artifact_id', kind: 'int', required: false),
  ],
  _ => const [],
};

Object? bbParse(BbField f, Object? raw) {
  if (f.kind == 'bool') {
    if (raw is! bool) {
      throw FormatException('${f.title}: select yes or no explicitly.');
    }
    return raw;
  }
  if (f.kind == 'optionalBool') {
    if (raw == null || raw == '') return null;
    if (raw == 'yes') return true;
    if (raw == 'no') return false;
    throw const FormatException('Invalid consent choice.');
  }
  if (f.kind == 'rows' || f.kind == 'panel') {
    if (raw is! List || raw.isEmpty || raw.length > f.max) {
      throw FormatException('${f.title}: add documented rows.');
    }
    final rows = [for (final r in raw) bbPayload(f.children, bbMap(r))];
    if (f.kind == 'panel') {
      final result = <String, dynamic>{};
      for (final r in rows) {
        final marker = bbText(r['marker'], max: 80);
        if (marker != marker.toLowerCase() || result.containsKey(marker)) {
          throw const FormatException(
            'Distinct lowercase marker names are required.',
          );
        }
        result[marker] = {
          'result': r['result'],
          'evidence_reference': r['evidence_reference'],
        };
      }
      if (!result.keys.toSet().containsAll({'hiv', 'hbv', 'hcv', 'syphilis'})) {
        throw const FormatException(
          'Complete approved screening panel required; include every required marker.',
        );
      }
      return result;
    }
    return rows;
  }
  if (f.kind == 'object') {
    final j = raw == null ? <String, dynamic>{} : bbMap(raw);
    if (!f.required && j.values.every((v) => v == null || v == '')) return null;
    return bbPayload(f.children, j);
  }
  final s = (raw as String?)?.trim() ?? '';
  if (s.isEmpty) {
    if (f.required) throw FormatException('${f.title} is required.');
    return null;
  }
  if (f.kind == 'choice') {
    if (!f.options.contains(s)) throw FormatException('Invalid ${f.title}.');
    return s;
  }
  if (f.kind == 'int' || f.kind == 'count') {
    final n = int.tryParse(s);
    final max = f.max == 5000 ? 2147483647 : f.max;
    if (n == null || n < (f.kind == 'count' ? 0 : 1) || n > max) {
      throw FormatException('Invalid ${f.title}.');
    }
    return n;
  }
  if (f.kind == 'decimal') {
    final n = double.tryParse(s);
    if (n == null || !n.isFinite || n <= 0) {
      throw FormatException('Invalid ${f.title}.');
    }
    return n;
  }
  if (f.kind == 'time') {
    if (!RegExp(
      r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$',
    ).hasMatch(s)) {
      throw FormatException('${f.title}: include UTC Z or timezone offset.');
    }
    return bbTime(s).toIso8601String();
  }
  if (f.kind == 'date') {
    final d = DateTime.tryParse(s);
    if (!RegExp(r'^\d{4}-\d\d-\d\d$').hasMatch(s) ||
        d == null ||
        d.toIso8601String().substring(0, 10) != s) {
      throw FormatException('Invalid ${f.title}.');
    }
    return s;
  }
  if (s.length > f.max) throw FormatException('${f.title} is too long.');
  return s;
}

BbJson bbPayload(List<BbField> fields, BbJson values) {
  final result = <String, dynamic>{};
  for (final f in fields) {
    final v = bbParse(f, values[f.key]);
    if (v != null) result[f.key] = v;
  }
  return result;
}

BbJson bbActionPayload(BbAction action, BbJson values) {
  final r = bbPayload(bbFields(action), values);
  if (action == BbAction.receive) r['tti_screening_cleared'] = false;
  if (action == BbAction.registerDonor || action == BbAction.deferral) {
    if (r['consent_to_contact'] == true && !r.containsKey('consent_reference')) {
      throw const FormatException('Document explicit contact consent.');
    }
  }
  if (action == BbAction.donate && r['intake_passed'] != true) {
    throw const FormatException(
      'Document actual intake assessment before collection.',
    );
  }
  if (action == BbAction.threshold &&
      bbCount(r['critical_units']) > bbCount(r['minimum_units'])) {
    throw const FormatException('Critical threshold exceeds minimum.');
  }
  if (action == BbAction.createOrder) {
    if (r['emergency_release_requested'] == true) {
      if (r['priority'] != 'stat' ||
          r['emergency_risk_acknowledged'] != true ||
          !r.containsKey('emergency_request_reason')) {
        throw const FormatException(
          'Explicit emergency request, stat priority and physician acknowledgment required.',
        );
      }
    } else if (r['emergency_risk_acknowledged'] == true ||
        r.containsKey('emergency_request_reason')) {
      throw const FormatException(
        'Emergency intent requires an explicit request.',
      ); }
  }
  if (action == BbAction.compatibility) {
    if (r['emergency_release'] == true) {
      if (!r.containsKey('product_blood_type') ||
          !r.containsKey('emergency_release_reason')) {
        throw const FormatException('Emergency evidence is required.');
      }
    } else if (!r.containsKey('crossmatch_reference')) {
      throw const FormatException('Verified crossmatch reference required.'); }
  }
  if (action == BbAction.recordArtifact) {
    if (r.containsKey('donation_id') == r.containsKey('blood_unit_id')) {
      throw const FormatException('Exactly one specimen subject is required.');
    }
  }
  if (action == BbAction.recall) {
    if (r.containsKey('donation_id')
        ? (r.containsKey('supplier_id') || r.containsKey('din'))
        : (!r.containsKey('supplier_id') || !r.containsKey('din'))) {
      throw const FormatException('Select a donation OR supplier and DIN.');
    }
  }
  if (action == BbAction.investigation &&
      r.containsKey('donation_id') == r.containsKey('recall_id')) {
    throw const FormatException('Select one investigation subject.');
  }
  return r;
}
