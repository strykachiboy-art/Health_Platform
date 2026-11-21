import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

class Wd05ReportSessionUnavailable implements Exception {
  const Wd05ReportSessionUnavailable();
}

/// These values are copied from backend ReportType/ReportFormat enums at the
/// pinned WD04 backend contract. Do not offer speculative report types.
const wd05ReportTypes = <String>[
  'overview',
  'patients',
  'staff',
  'appointments',
  'billing',
  'inventory',
  'lab',
  'pharmacy',
  'ward',
];
const wd05ReportFormats = <String>['pdf', 'csv', 'xlsx'];

class Wd05GeneratedReport {
  const Wd05GeneratedReport({
    required this.id,
    required this.clinicId,
    required this.type,
    required this.format,
    required this.createdUtc,
  });

  final int id;
  final int clinicId;
  final String type;
  final String format;
  final DateTime createdUtc;

  factory Wd05GeneratedReport.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
  }) {
    final id = json['id'];
    final clinic = json['clinic_id'];
    final type = json['report_type'];
    final format = json['report_format'];
    final created = json['created_at'];
    if (id is! int ||
        id <= 0 ||
        id > 2147483647 ||
        clinic is! int ||
        clinic != expectedClinicId ||
        type is! String ||
        !wd05ReportTypes.contains(type) ||
        format is! String ||
        !wd05ReportFormats.contains(format) ||
        created is! String ||
        created.isEmpty) {
      throw const FormatException(
        'Report record has an invalid scope or contract.',
      );
    }
    final when = DateTime.tryParse(created);
    // Require an explicit timezone to avoid silently relabeling local time UTC.
    if (when == null || !RegExp(r'(Z|[+-]\d{2}:\d{2})$').hasMatch(created)) {
      throw const FormatException('Report creation timestamp lacks timezone.');
    }
    // file_url is deliberately ignored: no unapproved download/export action.
    return Wd05GeneratedReport(
      id: id,
      clinicId: clinic,
      type: type,
      format: format,
      createdUtc: when.toUtc(),
    );
  }
}

class Wd05ReportPage {
  const Wd05ReportPage({
    required this.items,
    required this.page,
    required this.perPage,
    required this.total,
  });

  final List<Wd05GeneratedReport> items;
  final int page;
  final int perPage;
  final int total;
  bool get hasNext => page * perPage < total;

  factory Wd05ReportPage.fromEnvelope(
    Map<String, dynamic> envelope, {
    required int clinicId,
    required int requestedPage,
    required int requestedPerPage,
    String? type,
    String? format,
  }) {
    final data = envelope['data'];
    if (envelope['success'] != true || data is! Map<String, dynamic>) {
      throw const FormatException('Invalid generated reports response.');
    }
    final items = data['items'];
    final page = data['page'];
    final perPage = data['per_page'];
    final total = data['total'];
    if (items is! List ||
        page != requestedPage ||
        perPage != requestedPerPage ||
        total is! int ||
        total < 0 ||
        items.length > requestedPerPage ||
        items.length > total ||
        (total > 0 &&
            (requestedPage - 1) * requestedPerPage >= total &&
            items.isNotEmpty)) {
      throw const FormatException('Invalid generated reports pagination.');
    }
    final parsed = <Wd05GeneratedReport>[];
    final ids = <int>{};
    for (final raw in items) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Invalid generated reports item.');
      }
      final entry = Wd05GeneratedReport.fromJson(
        raw,
        expectedClinicId: clinicId,
      );
      if (!ids.add(entry.id) ||
          (type != null && entry.type != type) ||
          (format != null && entry.format != format)) {
        throw const FormatException('Conflicting generated reports response.');
      }
      parsed.add(entry);
    }
    return Wd05ReportPage(
      items: List<Wd05GeneratedReport>.unmodifiable(parsed),
      page: page as int,
      perPage: perPage as int,
      total: total,
    );
  }
}

/// GET-only report metadata. Does not generate, download, export, persist, or
/// construct an external file URL. The server is the sole reporting authority.
class Wd05ReportReadRemoteDataSource {
  const Wd05ReportReadRemoteDataSource({
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

  Future<AuthTokenResponse> _authorized(int revision) async {
    if (!_current(revision)) throw const Wd05ReportSessionUnavailable();
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
          !const <String>{
            'admin',
            'accountant',
          }.contains(session.role.trim().toLowerCase()) ||
          !_current(revision)) {
        throw const Wd05ReportSessionUnavailable();
      }
      return session;
    } on Wd05ReportSessionUnavailable {
      rethrow;
    } on Object {
      throw const Wd05ReportSessionUnavailable();
    }
  }

  static bool _same(AuthTokenResponse before, AuthTokenResponse after) =>
      before.userId == after.userId &&
      before.clinicContextId == after.clinicContextId &&
      before.tenantId == after.tenantId &&
      before.role.trim().toLowerCase() == after.role.trim().toLowerCase() &&
      before.accessToken == after.accessToken &&
      before.refreshToken == after.refreshToken;

  static String? _day(String? value) {
    if (value == null || value.isEmpty) return null;
    if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(value)) {
      throw ArgumentError.value(value, 'date', 'Expected YYYY-MM-DD.');
    }
    final year = int.parse(value.substring(0, 4));
    final month = int.parse(value.substring(5, 7));
    final day = int.parse(value.substring(8, 10));
    final date = DateTime.utc(year, month, day);
    if (date.year != year || date.month != month || date.day != day) {
      throw ArgumentError.value(value, 'date', 'Invalid calendar day.');
    }
    return value;
  }

  Future<Wd05ReportPage> listReports({
    int page = 1,
    int perPage = 20,
    String? reportType,
    String? reportFormat,
    String? dateFrom,
    String? dateTo,
  }) async {
    if (page < 1 || perPage < 1 || perPage > 100) {
      throw ArgumentError('Invalid report pagination.');
    }
    if ((reportType != null && !wd05ReportTypes.contains(reportType)) ||
        (reportFormat != null && !wd05ReportFormats.contains(reportFormat))) {
      throw ArgumentError('Unsupported report filter.');
    }
    final from = _day(dateFrom);
    final to = _day(dateTo);
    if (from != null && to != null && from.compareTo(to) > 0) {
      throw ArgumentError('Report date_to precedes date_from.');
    }
    final revision = sessionNavigationGuard.revision;
    final before = await _authorized(revision);
    final query = <String, String>{
      'page': '$page',
      'per_page': '$perPage',
      'report_type': ?reportType,
      'report_format': ?reportFormat,
      'date_from': ?from,
      'date_to': ?to,
    };
    final uri = Uri(path: 'api/v1/reports', queryParameters: query);
    final envelope = await apiClient.getJson(
      uri.toString(),
      bearerToken: before.accessToken,
    );
    final after = await _authorized(revision);
    if (!_same(before, after) || !_current(revision)) {
      throw const Wd05ReportSessionUnavailable();
    }
    return Wd05ReportPage.fromEnvelope(
      envelope,
      clinicId: before.clinicContextId!,
      requestedPage: page,
      requestedPerPage: perPage,
      type: reportType,
      format: reportFormat,
    );
  }
}
