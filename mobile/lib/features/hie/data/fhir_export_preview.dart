/// Bounded preview for the staged FHIR-R02 local export contract. No raw Bundle,
/// external identity inference, persistence, clipboard, or mutation support.
const fhirExportSections = <String>{
  'all',
  'patient',
  'encounters',
  'observations',
  'allergies',
  'medications',
  'conditions',
  'diagnostics',
};
const _sectionTypes = <String, Set<String>>{
  'all': {
    'Patient',
    'Encounter',
    'Observation',
    'AllergyIntolerance',
    'MedicationRequest',
    'Condition',
    'DiagnosticReport',
  },
  'patient': {'Patient'},
  'encounters': {'Patient', 'Encounter'},
  'observations': {'Patient', 'Observation'},
  'allergies': {'Patient', 'AllergyIntolerance'},
  'medications': {'Patient', 'MedicationRequest'},
  'conditions': {'Patient', 'Condition'},
  'diagnostics': {'Patient', 'DiagnosticReport', 'Observation'},
};
Never _bad() => throw const FormatException('Invalid staged FHIR export.');
Map<String, dynamic> _object(Object? value) =>
    value is Map<String, dynamic> ? value : _bad();
String _text(Object? value, {int max = 512}) =>
    value is String &&
        value.isNotEmpty &&
        value.length <= max &&
        value.trim().isNotEmpty
    ? value
    : _bad();
String _id(Object? value) {
  final id = _text(value, max: 64);
  if (!RegExp(r'^[A-Za-z0-9.-]{1,64}$').hasMatch(id)) {
    _bad();
  }
  return id;
}

String? _concept(Object? value) {
  if (value == null) {
    return null;
  }
  final map = _object(value);
  if (map['text'] != null) {
    return _text(map['text'], max: 4096);
  }
  final coding = map['coding'];
  if (coding is List && coding.isNotEmpty) {
    final first = _object(coding.first);
    return _text(first['display'] ?? first['code'], max: 512);
  }
  return null;
}

String? _quantity(Object? value) {
  if (value == null) {
    return null;
  }
  final quantity = _object(value);
  final number = quantity['value'];
  if (number is! num || !number.isFinite) {
    _bad();
  }
  final unit = _text(quantity['unit'], max: 128);
  return '$number $unit';
}

class FhirPreviewRow {
  const FhirPreviewRow(this.resourceType, this.id, this.description);
  final String resourceType, id, description;
}

class FhirExportPreview {
  const FhirExportPreview._(this.patientId, this.rows);
  final int patientId;
  final List<FhirPreviewRow> rows;

  factory FhirExportPreview.parse(
    Map<String, dynamic> json, {
    required int patientId,
    required int clinicId,
    required String tenantId,
    required String section,
  }) {
    final types = _sectionTypes[section];
    if (types == null ||
        patientId <= 0 ||
        clinicId <= 0 ||
        !RegExp(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')
            .hasMatch(tenantId) ||
        json['resourceType'] != 'Bundle' ||
        json['type'] != 'collection' ||
        json.containsKey('total') ||
        json['id'] != 'patient-$patientId-$section') {
      _bad();
    }
    final issuer =
        'https://synthetic.example.invalid/fhir/${tenantId.toLowerCase()}/$clinicId';
    final entries = json['entry'];
    if (entries is! List || entries.isEmpty || entries.length > 1000) {
      _bad();
    }
    final rows = <FhirPreviewRow>[];
    final seen = <String>{};
    final referencedResults = <String>[];
    var patients = 0;
    for (final raw in entries) {
      final entry = _object(raw);
      final resource = _object(entry['resource']);
      final kind = _text(resource['resourceType'], max: 40);
      final id = _id(resource['id']);
      final path = '$kind/$id';
      if (!types.contains(kind) ||
          !seen.add(path) ||
          entry['fullUrl'] != '$issuer/$path' ||
          entry.containsKey('request') ||
          entry.containsKey('response') ||
          entry.containsKey('search')) {
        _bad();
      }
      if (kind == 'Patient') {
        patients++;
        final identifiers = resource['identifier'];
        if (id != '$patientId' ||
            identifiers is! List ||
            !identifiers.any(
              (item) =>
                  item is Map &&
                  item['system'] == '$issuer/identifier/patient' &&
                  item['value'] == id,
            )) {
          _bad();
        }
      } else {
        final reference = _object(
          resource[kind == 'AllergyIntolerance' ? 'patient' : 'subject'],
        );
        if (reference['reference'] != 'Patient/$patientId') {
          _bad();
        }
      }
      final description = <String>[];
      final code = _concept(
        resource['code'] ?? resource['medicationCodeableConcept'],
      );
      if (code != null) {
        description.add(code);
      }
      final status = resource['status'];
      if (status != null) {
        description.add(_text(status, max: 64));
      }
      for (final key in ['clinicalStatus', 'verificationStatus']) {
        final text = _concept(resource[key]);
        if (text != null) {
          description.add(text);
        }
      }
      final quantity = _quantity(resource['valueQuantity']);
      if (quantity != null) {
        description.add(quantity);
      }
      if (resource['valueString'] != null) {
        description.add(_text(resource['valueString'], max: 4096));
      }
      final components = resource['component'];
      if (components != null) {
        if (components is! List || components.length > 20) {
          _bad();
        }
        for (final rawComponent in components) {
          final component = _object(rawComponent);
          final name = _concept(component['code']) ?? 'Measurement';
          final measurement = _quantity(component['valueQuantity']);
          description.add('$name: ${measurement ?? 'not recorded'}');
        }
      }
      if (kind == 'DiagnosticReport') {
        final results = resource['result'];
        if (results is! List || results.isEmpty || results.length > 1000) {
          _bad();
        }
        for (final item in results) {
          final ref = _text(_object(item)['reference'], max: 80);
          if (!ref.startsWith('Observation/')) {
            _bad();
          }
          referencedResults.add(ref);
        }
      }
      rows.add(FhirPreviewRow(kind, id, description.join(' · ')));
    }
    if (patients != 1 || !seen.containsAll(referencedResults)) {
      _bad();
    }
    return FhirExportPreview._(patientId, List.unmodifiable(rows));
  }
}
