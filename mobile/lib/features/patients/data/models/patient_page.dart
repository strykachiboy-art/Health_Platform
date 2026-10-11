import 'patient.dart';

/// One page of the backend's patient directory response.
class PatientPage {
  const PatientPage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
  });

  final List<Patient> items;
  final int total;
  final int page;
  final int perPage;

  factory PatientPage.fromJson(Map<String, dynamic> json) {
    final rawItems = json['items'];
    if (rawItems is! List) {
      throw const FormatException(
        'Patient list field "items" must be an array.',
      );
    }

    final items = <Patient>[];
    for (final item in rawItems) {
      if (item is! Map<String, dynamic>) {
        throw const FormatException(
          'Each patient list item must be a JSON object.',
        );
      }
      items.add(Patient.fromJson(item));
    }

    final total = _intAtLeast(json, 'total', 0);
    final page = _intAtLeast(json, 'page', 1);
    final perPage = _intAtLeast(json, 'per_page', 1);
    if (items.length > perPage) {
      throw const FormatException(
        'Patient list contains more rows than per_page.',
      );
    }

    return PatientPage(
      items: List.unmodifiable(items),
      total: total,
      page: page,
      perPage: perPage,
    );
  }

  static int _intAtLeast(Map<String, dynamic> json, String field, int minimum) {
    final value = json[field];
    if (value is! int || value < minimum) {
      throw FormatException('Patient list field "$field" is invalid.');
    }
    return value;
  }
}
