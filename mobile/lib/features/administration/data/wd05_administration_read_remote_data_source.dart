import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/administration/data/wd09cf_operation_contract.dart';

/// No new backend permission: every GET is checked against live session and
/// the Flask route's own role + clinic enforcement.
enum Wd05AdministrationSection {
  staff,
  departments,
  inventory,
  assets,
  settings,
}

extension Wd05AdministrationSectionDetails on Wd05AdministrationSection {
  String get title => switch (this) {
    Wd05AdministrationSection.staff => 'Staff directory',
    Wd05AdministrationSection.departments => 'Departments',
    Wd05AdministrationSection.inventory => 'Hospital inventory',
    Wd05AdministrationSection.assets => 'Hospital asset register',
    Wd05AdministrationSection.settings => 'Clinic settings',
  };

  String get path => switch (this) {
    Wd05AdministrationSection.staff => 'api/v1/staff',
    Wd05AdministrationSection.departments => 'api/v1/departments',
    Wd05AdministrationSection.inventory => 'api/v1/inventory/items',
    Wd05AdministrationSection.assets => 'api/v1/assets',
    Wd05AdministrationSection.settings => 'api/v1/settings/clinic',
  };

  bool allowedFor(String role) => switch (this) {
    Wd05AdministrationSection.staff ||
    Wd05AdministrationSection.departments => wd09cfStaffRoles.contains(role),
    Wd05AdministrationSection.inventory =>
      role == 'admin' || role == 'pharmacist',
    Wd05AdministrationSection.assets => role == 'admin' || role == 'accountant',
    Wd05AdministrationSection.settings => const <String>{
      'admin',
      'doctor',
      'nurse',
      'receptionist',
      'accountant',
      'pharmacist',
      'lab_technician',
    }.contains(role),
  };

  bool get paged => switch (this) {
    Wd05AdministrationSection.departments ||
    Wd05AdministrationSection.settings => false,
    _ => true,
  };
}

class Wd05AdministrationReadUnavailable implements Exception {
  const Wd05AdministrationReadUnavailable();
}

class Wd05AdministrationItem {
  const Wd05AdministrationItem({
    required this.id,
    required this.title,
    required this.description,
  });
  final int id;
  final String title;
  final String description;
}

class Wd05AdministrationReadPage {
  const Wd05AdministrationReadPage({
    required this.items,
    required this.page,
    required this.perPage,
    required this.total,
    required this.paged,
  });
  final List<Wd05AdministrationItem> items;
  final int page;
  final int perPage;
  final int total;
  final bool paged;

  bool get hasNext => paged && page * perPage < total;
}

/// Read-only, no cache, no hidden role escalation, no export/download, and no
/// auto replay. Only whitelisted display fields escape the parser.
class Wd05AdministrationReadRemoteDataSource {
  const Wd05AdministrationReadRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;

  bool _current(int revision) =>
      sessionNavigationGuard.canAccessProtectedRoutes &&
      sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _authorized(
    Wd05AdministrationSection section,
    int revision,
  ) async {
    if (!_current(revision)) throw const Wd05AdministrationReadUnavailable();
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.isEmpty ||
          session.accessToken.trim() != session.accessToken ||
          !section.allowedFor(session.role.trim().toLowerCase()) ||
          !_current(revision)) {
        throw const Wd05AdministrationReadUnavailable();
      }
      return session;
    } on Wd05AdministrationReadUnavailable {
      rethrow;
    } on Object {
      throw const Wd05AdministrationReadUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.tenantId == b.tenantId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  static int _id(Object? value) {
    if (value is! int || value <= 0 || value > 2147483647) {
      throw const FormatException('Invalid administration record ID.');
    }
    return value;
  }

  static String _text(Object? value) {
    if (value is! String || value.trim().isEmpty || value.length > 300) {
      throw const FormatException('Invalid administration display field.');
    }
    return value;
  }

  static Map<String, dynamic> _object(Object? raw) {
    if (raw is! Map<String, dynamic>) {
      throw const FormatException('Invalid administration record.');
    }
    return raw;
  }

  static Wd05AdministrationItem _parseItem(
    Wd05AdministrationSection section,
    Object? raw,
    int clinicId,
  ) {
    final json = _object(raw);
    if (json['clinic_id'] != clinicId) {
      throw const FormatException(
        'Administration record outside current clinic.',
      );
    }
    final id = _id(json['id']);
    final title = switch (section) {
      Wd05AdministrationSection.staff =>
        '${_text(json['first_name'])} ${_text(json['last_name'])}',
      Wd05AdministrationSection.settings => 'Clinic settings',
      _ => _text(json['name']),
    };
    final description = switch (section) {
      Wd05AdministrationSection.staff =>
        'Staff ID $id · Status: ${_text(json['status'])}',
      Wd05AdministrationSection.departments =>
        'Code: ${_text(json['code'])} · Status: ${_text(json['status'])}',
      Wd05AdministrationSection.assets =>
        'Asset tag: ${_text(json['asset_tag'])} · Status: ${_text(json['status'])}',
      Wd05AdministrationSection.inventory =>
        'On hand: ${_quantity(json['quantity_on_hand'])} · Reorder level: ${_quantity(json['reorder_level'])}',
      Wd05AdministrationSection.settings =>
        'Language: ${_text(json['language'])} · Date: ${_text(json['date_format'])} · Time: ${_text(json['time_format'])}',
    };
    return Wd05AdministrationItem(
      id: id,
      title: title,
      description: description,
    );
  }

  static int _quantity(Object? value) {
    if (value is! int || value < 0) {
      throw const FormatException('Invalid server inventory quantity.');
    }
    return value;
  }

  static Wd05AdministrationReadPage _parse(
    Wd05AdministrationSection section,
    Map<String, dynamic> envelope,
    int clinicId,
    int page,
    int perPage,
  ) {
    final Object? raw;
    if (section == Wd05AdministrationSection.assets) {
      if (envelope['success'] != true) {
        throw const FormatException('Invalid asset list envelope.');
      }
      raw = envelope;
    } else if (section == Wd05AdministrationSection.inventory ||
        section == Wd05AdministrationSection.settings) {
      if (envelope['success'] != true) {
        throw const FormatException('Invalid administration response.');
      }
      raw = envelope['data'];
    } else {
      raw = envelope['data'];
    }

    if (section == Wd05AdministrationSection.settings) {
      final item = _parseItem(section, raw, clinicId);
      return Wd05AdministrationReadPage(
        items: List<Wd05AdministrationItem>.unmodifiable([item]),
        page: 1,
        perPage: 1,
        total: 1,
        paged: false,
      );
    }

    if (section == Wd05AdministrationSection.departments) {
      if (raw is! List) {
        throw const FormatException('Invalid department list.');
      }
      return _list(section, raw, clinicId, 1, raw.length, raw.length, false);
    }

    final container = _object(raw);
    final items = container['items'];
    final returnedPage = container['page'];
    final returnedPerPage = container['per_page'];
    final total = container['total'];
    if (items is! List ||
        returnedPage != page ||
        returnedPerPage != perPage ||
        total is! int ||
        total < 0 ||
        items.length > perPage ||
        items.length > total ||
        (page > 1 && total <= (page - 1) * perPage && items.isNotEmpty)) {
      throw const FormatException('Invalid administration pagination.');
    }
    return _list(section, items, clinicId, page, perPage, total, true);
  }

  static Wd05AdministrationReadPage _list(
    Wd05AdministrationSection section,
    List<dynamic> raw,
    int clinicId,
    int page,
    int perPage,
    int total,
    bool paged,
  ) {
    if (raw.length > 500) {
      throw const FormatException('Administration response exceeds limit.');
    }
    final seen = <int>{};
    final items = <Wd05AdministrationItem>[];
    for (final value in raw) {
      final item = _parseItem(section, value, clinicId);
      if (!seen.add(item.id)) {
        throw const FormatException('Duplicate administration record.');
      }
      items.add(item);
    }
    return Wd05AdministrationReadPage(
      items: List<Wd05AdministrationItem>.unmodifiable(items),
      page: page,
      perPage: perPage,
      total: total,
      paged: paged,
    );
  }

  Future<Wd05AdministrationItem> detail(Wd05AdministrationSection section, int id) async {
    _id(id);
    if (section != Wd05AdministrationSection.staff && section != Wd05AdministrationSection.departments) {
      throw const Wd05AdministrationReadUnavailable();
    }
    final revision = sessionNavigationGuard.revision;
    final before = await _authorized(section, revision);
    final raw = await apiClient.getJson('${section.path}/$id', bearerToken: before.accessToken);
    if (!_same(before, await _authorized(section, revision)) || !_current(revision)) {
      throw const Wd05AdministrationReadUnavailable();
    }
    final row = _parseItem(section, raw['data'], before.clinicContextId!);
    if (row.id != id) {
      throw const FormatException('Administration detail identity mismatch.');
    }
    return row;
  }

  Future<Wd05AdministrationReadPage> load({
    required Wd05AdministrationSection section,
    int page = 1,
    int perPage = 20,
  }) async {
    if (page < 1 ||
        perPage < 1 ||
        perPage > 100 ||
        (!section.paged && page != 1)) {
      throw ArgumentError('Invalid administration pagination.');
    }
    final revision = sessionNavigationGuard.revision;
    final before = await _authorized(section, revision);
    final query = section.paged ? '?page=$page&per_page=$perPage' : '';
    final response = await apiClient.getJson(
      '${section.path}$query',
      bearerToken: before.accessToken,
    );
    final after = await _authorized(section, revision);
    if (!_current(revision) || !_same(before, after)) {
      throw const Wd05AdministrationReadUnavailable();
    }
    return _parse(section, response, before.clinicContextId!, page, perPage);
  }
}
