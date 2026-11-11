import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';

/// Feedback actions are online-only. A write that might have reached the
/// server is never automatically repeated, even after a route revisit.
class FeedbackSessionUnavailable implements Exception {
  const FeedbackSessionUnavailable();
}

class FeedbackOutcomeUnconfirmed implements Exception {
  const FeedbackOutcomeUnconfirmed();
}

class FeedbackWriteLocked implements Exception {
  const FeedbackWriteLocked();
}

class FeedbackPreflightChanged implements Exception {
  const FeedbackPreflightChanged();
}

class FeedbackDecode {
  const FeedbackDecode._();

  static Map<String, dynamic> object(Object? raw) {
    if (raw is! Map<String, dynamic>) {
      throw const FormatException('Expected feedback object.');
    }
    return raw;
  }

  static int id(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is! int || value <= 0 || value > 2147483647) {
      throw FormatException('Invalid feedback $field.');
    }
    return value;
  }

  static String text(Map<String, dynamic> json, String field) {
    final value = json[field];
    if (value is! String) {
      throw FormatException('Invalid feedback $field.');
    }
    return value;
  }

  static String? optionalText(Map<String, dynamic> json, String field) {
    if (json[field] == null) {
      return null;
    }
    return text(json, field);
  }

  static DateTime date(Map<String, dynamic> json, String field) {
    final value = text(json, field);
    if (!RegExp(r'(Z|[+-][0-9]{2}:[0-9]{2})$').hasMatch(value)) {
      throw FormatException('Feedback $field lacks timezone.');
    }
    final parsed = DateTime.tryParse(value);
    if (parsed == null) {
      throw FormatException('Invalid feedback $field.');
    }
    return parsed.toUtc();
  }
}

const feedbackTypes = <String>[
  'product_feedback',
  'bug_report',
  'feature_request',
  'service_complaint',
  'compliment',
];

const feedbackCategories = <String>[
  'usability',
  'performance',
  'accessibility',
  'billing',
  'appointment',
  'pharmacy',
  'laboratory',
  'clinical_workflow',
  'communication',
  'security',
  'other',
];

const feedbackStatuses = <String>[
  'open',
  'triaged',
  'in_progress',
  'resolved',
  'reopened',
  'closed',
  'rejected',
];

const feedbackPriorityValues = <String>['low', 'normal', 'high', 'urgent'];

class FeedbackRecord {
  const FeedbackRecord({
    required this.id,
    required this.clinicId,
    required this.submittedByUserId,
    required this.type,
    required this.category,
    required this.subject,
    required this.message,
    required this.status,
    required this.priority,
    required this.source,
    required this.resolutionNote,
    required this.createdAt,
  });

  final int id;
  final int clinicId;
  final int submittedByUserId;
  final String type;
  final String category;
  final String subject;
  final String message;
  final String status;
  final String priority;
  final String source;
  final String? resolutionNote;
  final DateTime createdAt;

  factory FeedbackRecord.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
    int? expectedId,
  }) {
    final id = FeedbackDecode.id(json, 'id');
    final clinicId = FeedbackDecode.id(json, 'clinic_id');
    if (clinicId != expectedClinicId ||
        expectedId != null && expectedId != id) {
      throw const FormatException('Feedback scope mismatch.');
    }
    final type = FeedbackDecode.text(json, 'feedback_type');
    final category = FeedbackDecode.text(json, 'category');
    final status = FeedbackDecode.text(json, 'status');
    final priority = FeedbackDecode.text(json, 'priority');
    final source = FeedbackDecode.text(json, 'source');
    if (!feedbackTypes.contains(type) ||
        !feedbackCategories.contains(category) ||
        !feedbackStatuses.contains(status) ||
        !feedbackPriorityValues.contains(priority) ||
        !const <String>['mobile', 'web', 'api', 'system'].contains(source)) {
      throw const FormatException('Unsupported feedback enum value.');
    }
    final subject = FeedbackDecode.text(json, 'subject');
    final message = FeedbackDecode.text(json, 'message');
    if (subject.trim().isEmpty ||
        subject.length > 200 ||
        message.trim().isEmpty ||
        message.length > 10000) {
      throw const FormatException('Invalid feedback text.');
    }
    return FeedbackRecord(
      id: id,
      clinicId: clinicId,
      submittedByUserId: FeedbackDecode.id(json, 'submitted_by_user_id'),
      type: type,
      category: category,
      subject: subject,
      message: message,
      status: status,
      priority: priority,
      source: source,
      resolutionNote: FeedbackDecode.optionalText(json, 'resolution_note'),
      createdAt: FeedbackDecode.date(json, 'created_at'),
    );
  }
}

class FeedbackComment {
  const FeedbackComment({
    required this.id,
    required this.feedbackId,
    required this.authorId,
    required this.body,
    required this.createdAt,
  });

  final int id;
  final int feedbackId;
  final int authorId;
  final String body;
  final DateTime createdAt;

  factory FeedbackComment.fromJson(
    Map<String, dynamic> json, {
    required int feedbackId,
  }) {
    if (FeedbackDecode.id(json, 'feedback_id') != feedbackId) {
      throw const FormatException('Comment feedback scope mismatch.');
    }
    final body = FeedbackDecode.text(json, 'body');
    if (body.trim().isEmpty || body.length > 10000) {
      throw const FormatException('Invalid feedback comment.');
    }
    return FeedbackComment(
      id: FeedbackDecode.id(json, 'id'),
      feedbackId: feedbackId,
      authorId: FeedbackDecode.id(json, 'author_user_id'),
      body: body,
      createdAt: FeedbackDecode.date(json, 'created_at'),
    );
  }
}

class FeedbackPage<T> {
  const FeedbackPage({
    required this.items,
    required this.page,
    required this.perPage,
    required this.total,
    required this.pages,
    required this.hasNext,
    required this.hasPrevious,
  });

  final List<T> items;
  final int page;
  final int perPage;
  final int total;
  final int pages;
  final bool hasNext;
  final bool hasPrevious;

  static FeedbackPage<E> parse<E>(
    Map<String, dynamic> envelope,
    E Function(Map<String, dynamic>) decode, {
    required int expectedPage,
    required int expectedPerPage,
  }) {
    if (envelope['success'] != true) {
      throw const FormatException('Feedback list not confirmed.');
    }
    final data = FeedbackDecode.object(envelope['data']);
    final raw = data['items'];
    final total = data['total'];
    final pages = data['pages'];
    final next = data['has_next'];
    final previous = data['has_previous'];
    if (raw is! List ||
        total is! int ||
        total < 0 ||
        pages is! int ||
        pages != (total + expectedPerPage - 1) ~/ expectedPerPage ||
        data['page'] != expectedPage ||
        data['per_page'] != expectedPerPage ||
        next is! bool ||
        next != (expectedPage < pages) ||
        previous is! bool ||
        previous != (expectedPage > 1 && total > 0) ||
        raw.length > expectedPerPage ||
        raw.length > total) {
      throw const FormatException('Inconsistent feedback pagination.');
    }
    final items = <E>[];
    for (final row in raw) {
      items.add(decode(FeedbackDecode.object(row)));
    }
    return FeedbackPage<E>(
      items: List<E>.unmodifiable(items),
      page: expectedPage,
      perPage: expectedPerPage,
      total: total,
      pages: pages,
      hasNext: next,
      hasPrevious: previous,
    );
  }
}

/// Authenticated backend client with strict clinic and session revision checks.
/// The read reader is safe to recreate; a single instance must own all writes
/// for one router lifetime to retain uncertain-outcome locks across revisits.
class FeedbackRemoteDataSource {
  FeedbackRemoteDataSource({
    required this.apiClient,
    required this.loadSession,
    required this.sessionNavigationGuard,
  });

  final ApiClient apiClient;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard sessionNavigationGuard;
  bool _inFlight = false;
  bool _uncertain = false;

  bool get writeLocked => _inFlight || _uncertain;
  bool get outcomeUnconfirmed => _uncertain;

  static void _id(int value) {
    if (value <= 0 || value > 2147483647) {
      throw ArgumentError.value(value, 'id');
    }
  }

  static void _page(int page, int perPage) {
    _id(page);
    if (perPage <= 0 || perPage > 500) {
      throw ArgumentError.value(perPage, 'perPage');
    }
  }

  static String _body(String value, int max, String field) {
    final text = value.trim();
    if (text.isEmpty || text.length > max) {
      throw ArgumentError.value(value, field, 'Invalid length.');
    }
    return text;
  }

  Future<AuthTokenResponse> _session(int revision) async {
    if (!sessionNavigationGuard.canAccessProtectedRoutes ||
        sessionNavigationGuard.revision != revision) {
      throw const FeedbackSessionUnavailable();
    }
    try {
      final state = await loadSession();
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0 ||
          session.clinicContextId == null ||
          session.clinicContextId! <= 0 ||
          session.accessToken.trim().isEmpty ||
          session.accessToken != session.accessToken.trim() ||
          session.refreshToken.isEmpty ||
          session.role.trim().isEmpty ||
          !sessionNavigationGuard.canAccessProtectedRoutes ||
          sessionNavigationGuard.revision != revision) {
        throw const FeedbackSessionUnavailable();
      }
      return session;
    } on FeedbackSessionUnavailable {
      rethrow;
    } on Object {
      throw const FeedbackSessionUnavailable();
    }
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role == b.role &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  static bool _admin(AuthTokenResponse session) => const <String>[
    'admin',
    'super_admin',
  ].contains(session.role.toLowerCase());

  /// Super admins require an explicit selected clinic in the query; ordinary
  /// clinic members must never pass a client-selected clinic_id.
  String _path(
    AuthTokenResponse actor,
    String suffix, {
    Map<String, String>? query,
  }) {
    final params = <String, String>{...?query};
    if (actor.role.toLowerCase() == 'super_admin') {
      params['clinic_id'] = '${actor.clinicContextId!}';
    }
    return Uri(
      path: 'api/v1/feedback$suffix',
      queryParameters: params.isEmpty ? null : params,
    ).toString();
  }

  Future<(Map<String, dynamic>, AuthTokenResponse)> _get(
    String suffix, {
    Map<String, String>? query,
  }) async {
    final revision = sessionNavigationGuard.revision;
    final before = await _session(revision);
    final json = await apiClient.getJson(
      _path(before, suffix, query: query),
      bearerToken: before.accessToken,
    );
    final after = await _session(revision);
    if (!_same(before, after)) {
      throw const FeedbackSessionUnavailable();
    }
    return (json, after);
  }

  Future<FeedbackPage<FeedbackRecord>> list({
    int page = 1,
    int perPage = 20,
  }) async {
    _page(page, perPage);
    final (json, session) = await _get(
      '',
      query: {'page': '$page', 'per_page': '$perPage'},
    );
    final result = FeedbackPage.parse<FeedbackRecord>(
      json,
      (row) => FeedbackRecord.fromJson(
        row,
        expectedClinicId: session.clinicContextId!,
      ),
      expectedPage: page,
      expectedPerPage: perPage,
    );
    final seen = <int>{};
    for (final record in result.items) {
      if (!seen.add(record.id)) {
        throw const FormatException('Duplicate feedback IDs.');
      }
    }
    return result;
  }

  Future<FeedbackRecord> detail(int feedbackId) async {
    _id(feedbackId);
    final (json, session) = await _get('/$feedbackId');
    if (json['success'] != true) {
      throw const FormatException('Feedback detail not confirmed.');
    }
    return FeedbackRecord.fromJson(
      FeedbackDecode.object(json['data']),
      expectedClinicId: session.clinicContextId!,
      expectedId: feedbackId,
    );
  }

  Future<FeedbackPage<FeedbackComment>> comments(
    int feedbackId, {
    int page = 1,
    int perPage = 20,
  }) async {
    _id(feedbackId);
    _page(page, perPage);
    // Always recheck feedback visibility before exposing comments.
    await detail(feedbackId);
    final (json, _) = await _get(
      '/$feedbackId/comments',
      query: {'page': '$page', 'per_page': '$perPage'},
    );
    return FeedbackPage.parse<FeedbackComment>(
      json,
      (row) => FeedbackComment.fromJson(row, feedbackId: feedbackId),
      expectedPage: page,
      expectedPerPage: perPage,
    );
  }

  Future<T> _write<T>(
    Future<T> Function(AuthTokenResponse actor, int revision) operation,
  ) async {
    if (writeLocked) {
      throw const FeedbackWriteLocked();
    }
    _inFlight = true;
    var sent = false;
    try {
      final revision = sessionNavigationGuard.revision;
      final actor = await _session(revision);
      // Each operation performs any additional preflight before invoking the
      // HTTP write. Once the callback is entered, assume the write was sent.
      sent = true;
      final result = await operation(actor, revision);
      return result;
    } on FeedbackPreflightChanged {
      rethrow;
    } on FeedbackSessionUnavailable {
      if (sent) {
        _uncertain = true;
        throw const FeedbackOutcomeUnconfirmed();
      }
      rethrow;
    } on Object {
      if (sent) {
        _uncertain = true;
        throw const FeedbackOutcomeUnconfirmed();
      }
      rethrow;
    } finally {
      _inFlight = false;
    }
  }

  Future<FeedbackRecord> create({
    required String type,
    required String category,
    required String subject,
    required String message,
  }) async {
    if (!feedbackTypes.contains(type) ||
        !feedbackCategories.contains(category)) {
      throw ArgumentError('Unsupported feedback type or category.');
    }
    final title = _body(subject, 200, 'subject');
    final content = _body(message, 10000, 'message');
    // The existing POST route does not accept a selected clinic_id for
    // super_admin; fail closed rather than guess an implicit global scope.
    if ((await _session(sessionNavigationGuard.revision)).role.toLowerCase() ==
        'super_admin') {
      throw const FeedbackPreflightChanged();
    }
    return _write((actor, revision) async {
      final raw = await apiClient.postJson(
        _path(actor, ''),
        body: {
          'feedback_type': type,
          'category': category,
          'subject': title,
          'message': content,
        },
        bearerToken: actor.accessToken,
      );
      if (raw['success'] != true) {
        throw const FormatException('Feedback create unconfirmed.');
      }
      final record = FeedbackRecord.fromJson(
        FeedbackDecode.object(raw['data']),
        expectedClinicId: actor.clinicContextId!,
      );
      final after = await _session(revision);
      if (!_same(actor, after)) {
        throw const FeedbackSessionUnavailable();
      }
      final confirmed = await detail(record.id);
      if (confirmed.id != record.id ||
          confirmed.submittedByUserId != actor.userId ||
          confirmed.type != type ||
          confirmed.category != category ||
          confirmed.subject != title ||
          confirmed.message != content) {
        throw const FormatException('Feedback create readback mismatch.');
      }
      return confirmed;
    });
  }

  Future<FeedbackComment> addComment(int feedbackId, String body) async {
    _id(feedbackId);
    final content = _body(body, 10000, 'body');
    await detail(feedbackId);
    return _write((actor, revision) async {
      final json = await apiClient.postJson(
        _path(actor, '/$feedbackId/comments'),
        body: {'body': content},
        bearerToken: actor.accessToken,
      );
      if (json['success'] != true) {
        throw const FormatException('Feedback comment unconfirmed.');
      }
      final comment = FeedbackComment.fromJson(
        FeedbackDecode.object(json['data']),
        feedbackId: feedbackId,
      );
      if (comment.authorId != actor.userId || comment.body != content) {
        throw const FormatException('Comment author mismatch.');
      }
      final (readback, after) = await _get(
        '/$feedbackId/comments/${comment.id}',
      );
      if (!_same(actor, after) ||
          sessionNavigationGuard.revision != revision ||
          readback['success'] != true) {
        throw const FeedbackSessionUnavailable();
      }
      final confirmed = FeedbackComment.fromJson(
        FeedbackDecode.object(readback['data']),
        feedbackId: feedbackId,
      );
      if (confirmed.id != comment.id ||
          confirmed.authorId != actor.userId ||
          confirmed.body != content) {
        throw const FormatException('Comment readback mismatch.');
      }
      return confirmed;
    });
  }

  Future<FeedbackRecord> changeStatus(
    int feedbackId, {
    required String status,
    String? resolutionNote,
  }) async {
    _id(feedbackId);
    final before = await detail(feedbackId);
    final transitions = <String, Set<String>>{
      'open': {'triaged', 'rejected'},
      'triaged': {'in_progress', 'rejected'},
      'in_progress': {'resolved', 'rejected'},
      'resolved': {'closed', 'reopened'},
      'closed': {'reopened'},
      'reopened': {'triaged', 'in_progress'},
      'rejected': {'reopened'},
    };
    if (!(transitions[before.status]?.contains(status) ?? false)) {
      throw const FeedbackPreflightChanged();
    }
    final note = status == 'resolved'
        ? _body(resolutionNote ?? '', 10000, 'resolutionNote')
        : null;
    final actor = await _session(sessionNavigationGuard.revision);
    if (!_admin(actor) || actor.clinicContextId != before.clinicId) {
      throw const FeedbackPreflightChanged();
    }
    return _write((approved, revision) async {
      final fresh = await detail(feedbackId);
      if (fresh.status != before.status || fresh.clinicId != before.clinicId) {
        throw const FeedbackPreflightChanged();
      }
      final response = await apiClient.patchJson(
        _path(approved, '/$feedbackId'),
        body: {'status': status, 'resolution_note': ?note},
        bearerToken: approved.accessToken,
      );
      if (response['success'] != true) {
        throw const FormatException('Feedback status unconfirmed.');
      }
      final updated = FeedbackRecord.fromJson(
        FeedbackDecode.object(response['data']),
        expectedClinicId: approved.clinicContextId!,
        expectedId: feedbackId,
      );
      final after = await _session(revision);
      if (!_same(approved, after)) {
        throw const FeedbackSessionUnavailable();
      }
      final confirmed = await detail(feedbackId);
      if (updated.status != status ||
          confirmed.status != status ||
          (note != null && confirmed.resolutionNote != note)) {
        throw const FormatException('Feedback status readback mismatch.');
      }
      return confirmed;
    });
  }
}
