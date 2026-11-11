import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/feedback/data/feedback_remote_data_source.dart';

Map<String, dynamic> _record({
  int id = 11,
  int clinic = 3,
  int owner = 42,
  String status = 'open',
  String? resolutionNote,
}) => {
  'id': id,
  'clinic_id': clinic,
  'submitted_by_user_id': owner,
  'feedback_type': 'bug_report',
  'category': 'usability',
  'subject': 'Issue with chart',
  'message': 'The chart does not refresh.',
  'status': status,
  'priority': 'normal',
  'source': 'api',
  'resolution_note': resolutionNote,
  'created_at': '2026-11-11T12:00:00Z',
};

Map<String, dynamic> _comment({int id = 9, int feedback = 11}) => {
  'id': id,
  'feedback_id': feedback,
  'author_user_id': 42,
  'body': 'Additional details',
  'created_at': '2026-11-11T12:10:00Z',
};

http.Response _success(Object data, {int status = 200}) =>
    http.Response(jsonEncode({'success': true, 'data': data}), status);

http.Response _page(List<Object> items, {int page = 1, int perPage = 20}) =>
    _success({
      'items': items,
      'page': page,
      'per_page': perPage,
      'total': items.length,
      'pages': items.isEmpty ? 0 : 1,
      'has_next': false,
      'has_previous': false,
    });

FeedbackRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'patient',
  int clinic = 3,
}) => FeedbackRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => AuthSessionState.ready(
    AuthTokenResponse(
      accessToken: 'feedback-token',
      refreshToken: 'refresh-token',
      userId: 42,
      role: role,
      clinicContextId: clinic,
    ),
  ),
  sessionNavigationGuard: guard,
);

void main() {
  test('server-authorized feedback list, bearer and pagination', () async {
    final client = MockClient((req) async {
      expect(req.method, 'GET');
      expect(req.url.path, '/api/v1/feedback');
      expect(req.url.queryParameters['page'], '1');
      expect(req.url.queryParameters['per_page'], '20');
      expect(req.url.queryParameters.containsKey('clinic_id'), isFalse);
      expect(req.headers['authorization'], 'Bearer feedback-token');
      return _page([_record()]);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final page = await _source(client, guard).list();
    expect(page.total, 1);
    expect(page.items.single.id, 11);
  });

  test('cross-clinic feedback is rejected even with HTTP 200', () async {
    final client = MockClient((req) async => _page([_record(clinic: 99)]));
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    expect(_source(client, guard).list(), throwsFormatException);
  });

  test(
    'super admin reader requires selected clinic, not global list',
    () async {
      final client = MockClient((req) async {
        expect(req.url.queryParameters['clinic_id'], '3');
        return _page([_record()]);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final result = await _source(client, guard, role: 'super_admin').list();
      expect(result.total, 1);
    },
  );

  test('create executes one POST then authoritative GET readback', () async {
    var posts = 0;
    var reads = 0;
    final client = MockClient((req) async {
      if (req.method == 'POST') {
        posts++;
        expect(req.url.path, '/api/v1/feedback');
        expect(jsonDecode(req.body), {
          'feedback_type': 'bug_report',
          'category': 'usability',
          'subject': 'Issue with chart',
          'message': 'The chart does not refresh.',
        });
        return _success(_record(), status: 201);
      }
      reads++;
      expect(req.url.path, '/api/v1/feedback/11');
      return _success(_record());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final value = await source.create(
      type: 'bug_report',
      category: 'usability',
      subject: 'Issue with chart',
      message: 'The chart does not refresh.',
    );
    expect(value.id, 11);
    expect(posts, 1);
    expect(reads, 1);
    expect(source.writeLocked, isFalse);
  });

  test(
    'uncertain create response disables repeats across route revisits',
    () async {
      var posts = 0;
      final client = MockClient((req) async {
        posts++;
        return http.Response('{"error":"provider unavailable"}', 503);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      Future<FeedbackRecord> create() => source.create(
        type: 'bug_report',
        category: 'usability',
        subject: 'Issue with chart',
        message: 'The chart does not refresh.',
      );
      await expectLater(create(), throwsA(isA<FeedbackOutcomeUnconfirmed>()));
      await expectLater(create(), throwsA(isA<FeedbackWriteLocked>()));
      expect(source.outcomeUnconfirmed, isTrue);
      expect(posts, 1);
    },
  );

  test('patient cannot perform an admin-only status mutation', () async {
    var patches = 0;
    final client = MockClient((req) async {
      if (req.method == 'PATCH') {
        patches++;
      }
      return _success(_record());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await expectLater(
      source.changeStatus(11, status: 'triaged'),
      throwsA(isA<FeedbackPreflightChanged>()),
    );
    expect(patches, 0);
  });

  test(
    'admin status transition PATCH is confirmed by authoritative GET',
    () async {
      var patches = 0;
      var details = 0;
      final client = MockClient((req) async {
        if (req.method == 'PATCH') {
          patches++;
          expect(jsonDecode(req.body), {'status': 'triaged'});
          return _success(_record(status: 'triaged'));
        }
        details++;
        return _success(_record(status: details <= 2 ? 'open' : 'triaged'));
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard, role: 'admin');
      final result = await source.changeStatus(11, status: 'triaged');
      expect(result.status, 'triaged');
      expect(patches, 1);
      expect(details, 3);
    },
  );

  test(
    'comment POST readback authenticates author and feedback identity',
    () async {
      var posts = 0;
      final client = MockClient((req) async {
        if (req.url.path == '/api/v1/feedback/11' && req.method == 'GET') {
          return _success(_record());
        }
        if (req.method == 'POST') {
          posts++;
          expect(req.url.path, '/api/v1/feedback/11/comments');
          expect(jsonDecode(req.body), {'body': 'Additional details'});
          return _success(_comment(), status: 201);
        }
        expect(req.url.path, '/api/v1/feedback/11/comments/9');
        return _success(_comment());
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final item = await source.addComment(11, 'Additional details');
      expect(item.id, 9);
      expect(posts, 1);
      expect(source.outcomeUnconfirmed, isFalse);
    },
  );

  test('invalidation blocks disclosure before HTTP', () async {
    var requests = 0;
    final client = MockClient((req) async {
      requests++;
      return _success(_record());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await expectLater(
      source.detail(11),
      throwsA(isA<FeedbackSessionUnavailable>()),
    );
    expect(requests, 0);
  });
}
