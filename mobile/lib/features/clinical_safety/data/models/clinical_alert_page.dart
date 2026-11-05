import 'clinical_alert.dart';

/// The flat pagination envelope returned by GET /clinical-safety/alerts.
class ClinicalAlertPage {
  const ClinicalAlertPage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
  });

  final List<ClinicalAlert> items;
  final int total;
  final int page;
  final int perPage;

  factory ClinicalAlertPage.fromJson(Map<String, dynamic> json) {
    final rawItems = json['items'];
    if (rawItems is! List) {
      throw const FormatException('Clinical alerts items must be an array.');
    }

    final items = <ClinicalAlert>[];
    for (final item in rawItems) {
      if (item is! Map<String, dynamic>) {
        throw const FormatException('Clinical alert item must be an object.');
      }
      items.add(ClinicalAlert.fromJson(item));
    }

    final total = _integerAtLeast(json['total'], 'total', 0);
    final page = _integerAtLeast(json['page'], 'page', 1);
    final perPage = _integerAtLeast(json['per_page'], 'per_page', 1);
    if (perPage > 500 || items.length > perPage) {
      throw const FormatException('Invalid clinical alerts page size.');
    }

    return ClinicalAlertPage(
      items: List<ClinicalAlert>.unmodifiable(items),
      total: total,
      page: page,
      perPage: perPage,
    );
  }

  static int _integerAtLeast(Object? value, String field, int minimum) {
    if (value is! int || value < minimum) {
      throw FormatException('Clinical alerts $field is invalid.');
    }
    return value;
  }
}
