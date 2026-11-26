import 'dart:convert';

import 'package:clinic_system_pro/features/administration/data/wd09cf_operations_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';

class TemplateUnavailable implements Exception {
  const TemplateUnavailable();
}

class TemplateUnconfirmed implements Exception {
  const TemplateUnconfirmed();
}

class ConsultationTemplateRecord {
  const ConsultationTemplateRecord(this.id, this.clinicId, this.name,
      this.specialty, this.structure);
  final int id;
  final int? clinicId;
  final String name;
  final String? specialty;
  final Map<String, dynamic> structure;

  factory ConsultationTemplateRecord.parse(Object? value, int clinic) {
    if (value is! Map<String, dynamic>) throw const FormatException('Template object required.');
    final id = value['id'];
    final owner = value['clinic_id'];
    final name = value['name'];
    final specialty = value['specialty'];
    final structure = value['structure'];
    if (id is! int || id <= 0 || !value.containsKey('clinic_id') ||
        (owner != null && (owner is! int || owner != clinic)) ||
        name is! String || name.trim().isEmpty || name.length > 150 ||
        (specialty != null && (specialty is! String || specialty.trim().isEmpty || specialty.length > 100)) ||
        value['is_active'] != true || structure is! Map<String, dynamic> ||
        structure.isEmpty || jsonEncode(structure).length > 24000) {
      throw const FormatException('Invalid or foreign template.');
    }
    return ConsultationTemplateRecord(id, owner as int?, name,
        specialty as String?, Map<String, dynamic>.unmodifiable(structure));
  }
}

class ConsultationTemplatePage {
  const ConsultationTemplatePage(this.items, this.page, this.hasNext);
  final List<ConsultationTemplateRecord> items;
  final int page;
  final bool hasNext;
}

/// A fixed backend contract, online only. Templates are reusable documentation
/// structures, not patient records or permission to make clinical decisions.
class ConsultationTemplateSource {
  const ConsultationTemplateSource(this.consultations, this.gate);
  final ConsultationRemoteDataSource consultations;
  final Wd09cfOneShotGate gate;
  static const path = 'api/v1/consultations/templates';

  bool current(int revision) => consultations.sessionNavigationGuard.canAccessProtectedRoutes &&
      consultations.sessionNavigationGuard.revision == revision;

  Future<AuthTokenResponse> _actor(int revision) async {
    if (!current(revision)) throw const TemplateUnavailable();
    final state = await consultations.loadSession();
    final s = state.session;
    if (state.disposition != AuthSessionDisposition.ready || s == null ||
        s.role.trim().toLowerCase() != 'doctor' || s.userId <= 0 ||
        (s.clinicContextId ?? 0) <= 0 || s.accessToken.isEmpty ||
        s.accessToken != s.accessToken.trim() || s.refreshToken.isEmpty ||
        !RegExp(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$').hasMatch(s.tenantId ?? '') ||
        !current(revision)) {
      throw const TemplateUnavailable();
    }
    return s;
  }

  Future<void> _check(AuthTokenResponse a, int revision) async {
    final b = await _actor(revision);
    if (a.userId != b.userId || a.role != b.role || a.tenantId != b.tenantId ||
        a.clinicContextId != b.clinicContextId || a.accessToken != b.accessToken ||
        a.refreshToken != b.refreshToken || !current(revision)) {
      throw const TemplateUnavailable();
    }
  }

  Future<ConsultationTemplatePage> list({int page = 1}) async {
    if (page < 1 || page > 1000000) throw const FormatException('Invalid page.');
    final revision = consultations.sessionNavigationGuard.revision;
    final a = await _actor(revision);
    final raw = await consultations.apiClient.getJson('$path?page=$page&per_page=20', bearerToken: a.accessToken);
    await _check(a, revision);
    final data = raw['data'];
    final p = raw['pagination'];
    if (raw['success'] != true || data is! List || p is! Map<String, dynamic> ||
        p['page'] != page || p['per_page'] != 20 || p['total'] is! int ||
        (p['total'] as int) < data.length || data.length > 20 || p['has_next'] is! bool) {
      throw const FormatException('Invalid template pagination.');
    }
    final items = data.map((r) => ConsultationTemplateRecord.parse(r, a.clinicContextId!)).toList();
    if (items.map((x) => x.id).toSet().length != items.length) throw const FormatException('Duplicate template.');
    return ConsultationTemplatePage(List.unmodifiable(items), page, p['has_next'] as bool);
  }

  /// A bounded JSON object is the exact server schema. No arbitrary endpoints,
  /// patient identity, scope or actor fields are sent at the envelope level.
  static Map<String, dynamic> draft(String name, String specialty, String json) {
    final n = name.trim();
    final s = specialty.trim();
    if (n.isEmpty || n.length > 150 || s.length > 100 || json.length > 12000) {
      throw const FormatException('Invalid template input.');
    }
    final structure = jsonDecode(json);
    if (structure is! Map<String, dynamic> || structure.isEmpty) throw const FormatException('Nonempty template object required.');
    return {'name': n, if (s.isNotEmpty) 'specialty': s, 'structure': structure};
  }

  Future<ConsultationTemplateRecord> create(String name, String specialty, String json, {required int expectedRevision}) async {
    final body = draft(name, specialty, json);
    final a = await _actor(expectedRevision);
    final key = 'consultation-template:${a.userId}:${a.tenantId}:${a.clinicContextId}:${body['name']}';
    await _check(a, expectedRevision);
    if (!gate.claim(key)) throw const TemplateUnconfirmed();
    try {
      final raw = await consultations.apiClient.postJson(path, body: body, bearerToken: a.accessToken);
      await _check(a, expectedRevision);
      if (raw['success'] != true) throw const FormatException('Template not confirmed.');
      final row = ConsultationTemplateRecord.parse(raw['data'], a.clinicContextId!);
      // Compare JSON structurally without relying on dictionary key ordering.
      if (row.clinicId != a.clinicContextId || row.name != body['name'] ||
          row.specialty != body['specialty'] || !_equal(row.structure, body['structure'])) {
        throw const FormatException('Template receipt mismatch.');
      }
      return row;
    } on Object {
      throw const TemplateUnconfirmed();
    }
  }

  static bool _equal(Object? a, Object? b) {
    if (a is Map && b is Map) {
      return a.length == b.length && a.keys.every((k) => b.containsKey(k) && _equal(a[k], b[k]));
    }
    if (a is List && b is List) {
      if (a.length != b.length) return false;
      for (var i = 0; i < a.length; i++) { if (!_equal(a[i], b[i])) return false; }
      return true;
    }
    return a == b;
  }
}
