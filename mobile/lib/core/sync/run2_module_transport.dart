import '../networking/api_client.dart';
import '../../features/auth/data/models/auth_token_response.dart';
import 'run2_module_catalog.dart';

/// A fixed GET-only transport. Cacheable projections and live-only target
/// verification are separate operations. No emergency-access header, raw
/// response persistence, or request replay is available through this adapter.
class Run2ModuleTransport {
  const Run2ModuleTransport(this.api);
  final ApiClient api;

  static Map<String, dynamic> object(Object? value) {
    if (value is! Map<String, dynamic>) {
      throw const FormatException('Invalid module response.');
    }
    return value;
  }

  static bool _valueValid(Object? value, String descriptor) {
    if (value == null) {
      return descriptor.endsWith('?');
    }
    return switch (descriptor.replaceAll('?', '')) {
      'i' => value is int && value > 0 && value <= 2147483647,
      'n' => value is int && value >= 0 && value <= 2147483647,
      'b' => value is bool,
      's' =>
        value is String &&
            value.trim().isNotEmpty &&
            value.length <= 12000 &&
            !value.contains('\u0000'),
      'd' =>
        value is String && RegExp(r'^\d{1,16}(?:\.\d{1,8})?$').hasMatch(value),
      _ => false,
    };
  }

  static void validateProjection(
    ModuleReadSpec spec,
    List<Map<String, dynamic>> rows,
    int id,
  ) {
    spec.path(id);
    final single = const {
      'raw',
      'envelope',
      'aggregate',
      'reconciliation',
    }.contains(spec.shape);
    if (rows.length > 20 || (single && rows.length != 1)) {
      throw const FormatException('Invalid snapshot size.');
    }
    final ids = <int>{};
    for (final row in rows) {
      if (row.length != spec.fields.length ||
          !row.keys.every(spec.fields.containsKey)) {
        throw const FormatException('Unexpected snapshot fields.');
      }
      for (final e in spec.fields.entries) {
        if (!_valueValid(row[e.key], e.value)) {
          throw const FormatException('Invalid snapshot field.');
        }
      }
      if (spec.identity.isNotEmpty && !ids.add(row[spec.identity] as int)) {
        throw const FormatException('Duplicate resource identity.');
      }
      if (spec.relation.isNotEmpty && row[spec.relation] != id) {
        throw const FormatException('Resource relation mismatch.');
      }
      if (single &&
          spec.requiresId &&
          spec.relation.isEmpty &&
          row[spec.identity] != id) {
        throw const FormatException('Resource identity mismatch.');
      }
    }
  }

  static List<Map<String, dynamic>> project(
    ModuleReadSpec spec,
    Map<String, dynamic> raw,
    int id,
    int clinic, {
    int? ownerId,
  }) {
    Object? value = raw;
    if (spec.shape == 'envelope' || spec.shape == 'page') {
      if (raw['success'] != true) {
        throw const FormatException('Read not confirmed.');
      }
      value = object(raw['data']);
    }
    if (spec.shape == 'reconciliation') value = object(raw['reconciliation']);
    final isPage = spec.shape == 'page' || spec.shape == 'rawPage';
    if (isPage) value = object(value)['items'];
    final rows = isPage ? value : [value];
    if (rows is! List || rows.length > 20) {
      throw const FormatException('Read exceeds the bounded snapshot page.');
    }
    final projected = <Map<String, dynamic>>[];
    for (final item in rows) {
      final row = object(item);
      if (spec.clinicField.isNotEmpty && row[spec.clinicField] != clinic) {
        throw const FormatException('Foreign or unbound clinic record.');
      }
      if (spec.key == 'notifications' &&
          (ownerId == null || row['user_id'] != ownerId)) {
        throw const FormatException('Notification owner mismatch.');
      }
      projected.add(
        Map<String, dynamic>.unmodifiable({
          for (final e in spec.fields.entries) e.key: row[e.key],
        }),
      );
    }
    validateProjection(spec, projected, id);
    return List.unmodifiable(projected);
  }

  Future<List<Map<String, dynamic>>> capture(
    AuthTokenResponse session,
    ModuleReadSpec spec,
    int id,
  ) async {
    if (!identical(Run2ModuleCatalog.reads[spec.key], spec) ||
        !spec.roles.contains(session.role.toLowerCase())) {
      throw const FormatException('Unapproved snapshot endpoint.');
    }
    final raw = await api.getJsonForOfflineSnapshot(
      spec.path(id),
      bearerToken: session.accessToken,
    );
    return project(
      spec,
      raw,
      id,
      session.clinicContextId!,
      ownerId: session.userId,
    );
  }

  /// Fresh live record authorization. The returned map contains identity and
  /// version/status facts only; callers must not persist raw no-store data.
  Future<Map<String, dynamic>> verifyTarget(
    AuthTokenResponse session,
    ModuleReadSpec spec,
    int id,
  ) async {
    if (!identical(Run2ModuleCatalog.anchor(spec.key), spec) ||
        !spec.roles.contains(session.role.toLowerCase()) ||
        !spec.requiresId) {
      throw const FormatException('Unapproved draft target.');
    }
    final raw = await api.getJson(
      spec.path(id),
      bearerToken: session.accessToken,
    );
    // Draft anchors are single-record routes only. Projection verifies the
    // server-derived clinic and target identity before any draft is enabled.
    final rows = project(
      spec,
      raw,
      id,
      session.clinicContextId!,
      ownerId: session.userId,
    );
    if (rows.length != 1) {
      throw const FormatException('Draft target unavailable.');
    }
    final row = rows.single;
    final anchor = <String, dynamic>{
      'record_id': row[spec.identity],
      for (final name in [
        'patient_id',
        'surgical_case_id',
        'imaging_order_id',
        'version',
        'record_version',
        'status',
        'record_status',
      ])
        if (row[name] != null) name: row[name],
    };
    if (spec.key == 'lab_order') {
      final items = object(raw['data'])['items'];
      if (items is! List || items.length > 100) {
        throw const FormatException('Lab item list unavailable.');
      }
      final ids = <int>{};
      for (final item in items) {
        final m = object(item);
        final itemId = m['id'];
        if (m['order_id'] != id ||
            itemId is! int ||
            itemId <= 0 ||
            !ids.add(itemId)) {
          throw const FormatException('Lab order item identity mismatch.');
        }
      }
      anchor['item_ids'] = ids.toList()..sort();
    }
    return Map.unmodifiable(anchor);
  }
}
