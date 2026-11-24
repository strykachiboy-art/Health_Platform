import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/administration/data/wd13_clinic_management_remote_data_source.dart';

Map<String, Object?> _clinic({Object? mode, bool includeMode = true}) {
  final data = <String, Object?>{
    'id': 11,
    'name': 'Test Clinic',
    'clinic_type': 'general',
    'status': 'active',
    'is_headquarters': false,
    'parent_clinic_id': null,
    'timezone': 'Africa/Lagos',
    'opening_time': '20:00:00',
    'closing_time': '06:00:00',
  };
  if (includeMode) data['operating_mode'] = mode;
  return data;
}

Wd13RootClinicDraft _draft({
  String? mode,
  String opening = '',
  String closing = '',
}) => Wd13RootClinicDraft(
  name: 'Hours Example',
  type: 'general',
  headquarters: false,
  timezone: 'Africa/Lagos',
  operatingMode: mode,
  openingTime: opening,
  closingTime: closing,
);

void main() {
  test('old backend omits mode and legacy payload remains unchanged', () {
    final response = Wd13Clinic.parse(_clinic(includeMode: false));
    expect(response.operatingMode, isNull);
    final json = _draft(opening: '08:00', closing: '17:00').toBackendJson();
    expect(json.containsKey('operating_mode'), isFalse);
    expect(json['opening_time'], '08:00:00');
    expect(json['closing_time'], '17:00:00');
  });

  test('explicit always_open sends only mode, never invented times', () {
    final json = _draft(mode: 'always_open').toBackendJson();
    expect(json['operating_mode'], 'always_open');
    expect(json.containsKey('opening_time'), isFalse);
    expect(json.containsKey('closing_time'), isFalse);
    expect(json['parent_clinic_id'], isNull);
  });

  test('unspecified differs from always_open and sends no times', () {
    final json = _draft(mode: 'unspecified').toBackendJson();
    expect(json['operating_mode'], 'unspecified');
    expect(json.containsKey('opening_time'), isFalse);
    expect(json.containsKey('closing_time'), isFalse);
  });

  test('scheduled overnight sends existing two columns and explicit mode', () {
    final json = _draft(
      mode: 'scheduled',
      opening: '20:00',
      closing: '06:00',
    ).toBackendJson();
    expect(json['operating_mode'], 'scheduled');
    expect(json['opening_time'], '20:00:00');
    expect(json['closing_time'], '06:00:00');
  });

  test('legacy overnight remains rejected while extension is unselected', () {
    expect(
      () => _draft(opening: '20:00', closing: '06:00').toBackendJson(),
      throwsFormatException,
    );
  });

  test('explicit modes reject missing/equal/unexpected times', () {
    for (final draft in <Wd13RootClinicDraft>[
      _draft(mode: 'scheduled'),
      _draft(mode: 'scheduled', opening: '08:00', closing: '08:00'),
      _draft(mode: 'scheduled', opening: '08:00'),
      _draft(mode: 'always_open', opening: '08:00'),
      _draft(mode: 'unspecified', closing: '09:00'),
      _draft(mode: 'invalid'),
    ]) {
      expect(() => draft.toBackendJson(), throwsFormatException);
    }
  });

  test('backend response mode is strictly validated', () {
    expect(
      Wd13Clinic.parse(_clinic(mode: 'scheduled')).operatingMode,
      'scheduled',
    );
    expect(
      Wd13Clinic.parse(_clinic(mode: 'always_open')).operatingMode,
      'always_open',
    );
    expect(
      Wd13Clinic.parse(_clinic(mode: 'unspecified')).operatingMode,
      'unspecified',
    );
    expect(
      () => Wd13Clinic.parse(_clinic(mode: 'made_up')),
      throwsFormatException,
    );
    expect(() => Wd13Clinic.parse(_clinic(mode: 27)), throwsFormatException);
  });
}
