import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_remote_data_source.dart';
import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';

const _token = 'test-access-token';

Map<String, dynamic> _grantJson({String status = 'requested'}) {
  return <String, dynamic>{
    'id': 17,
    'clinic_id': 3,
    'patient_id': 7,
    'requester_user_id': 42,
    'requester_role': 'DOCTOR',
    'reason': 'Emergency treatment',
    'purpose': 'Immediate clinical review',
    'scope': <String>['patient:read'],
    'status': status,
    'requested_at': '2026-11-03T12:00:00+00:00',
    'granted_at': status == 'active' ? '2026-11-03T12:05:00+00:00' : null,
    'expires_at': status == 'active' ? '2026-11-03T12:35:00+00:00' : null,
    'revoked_at': null,
    'reviewed_at': status == 'requested' ? null : '2026-11-03T12:05:00+00:00',
    'reviewed_by_user_id': status == 'requested' ? null : 99,
    'review_notes': null,
  };
}

EmergencyAccessRemoteDataSource _source(
  MockClient client, {
  AuthenticatedAccessTokenLoader? loadAccessToken,
}) {
  return EmergencyAccessRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadAccessToken: loadAccessToken ?? () async => _token,
  );
}

http.Response _ok({String status = 'requested', int code = 200}) {
  return http.Response(
    jsonEncode(<String, dynamic>{
      'success': true,
      'data': _grantJson(status: status),
    }),
    code,
  );
}

void main() {
  group('EmergencyAccessRemoteDataSource', () {
    test(
      'creates request with normalized scope and no idempotency header',
      () async {
        final client = MockClient((request) async {
          expect(request.method, 'POST');
          expect(request.url.path, '/api/v1/emergency-access/requests');
          expect(request.headers['authorization'], 'Bearer $_token');
          expect(request.headers.containsKey('idempotency-key'), isFalse);

          expect(jsonDecode(request.body), <String, dynamic>{
            'patient_id': 7,
            'reason': 'Emergency treatment',
            'purpose': 'Immediate clinical review',
            'scope': <String>['patient:read', 'nursing_note:7:*'],
          });

          return _ok(code: 201);
        });
        addTearDown(client.close);

        final grant = await _source(client).requestAccess(
          patientId: 7,
          reason: '  Emergency treatment  ',
          purpose: '  Immediate clinical review  ',
          scope: const <String>[
            ' Patient:Read ',
            'nursing_note:7:*',
            'patient:read',
          ],
        );

        expect(grant.id, 17);
        expect(grant.status, EmergencyAccessStatus.requested);
      },
    );

    test('fetches an emergency access request by ID', () async {
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/emergency-access/requests/17');
        expect(request.headers['authorization'], 'Bearer $_token');
        return _ok();
      });
      addTearDown(client.close);

      final result = await _source(client).fetchRequest(emergencyAccessId: 17);

      expect(result.id, 17);
    });

    test('grant sends duration and normalized review notes', () async {
      final client = MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/emergency-access/requests/17/grant');
        expect(jsonDecode(request.body), <String, dynamic>{
          'duration_minutes': 30,
          'review_notes': 'Approved',
        });
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        return _ok(status: 'active');
      });
      addTearDown(client.close);

      final result = await _source(client).grantRequest(
        emergencyAccessId: 17,
        durationMinutes: 30,
        reviewNotes: '  Approved  ',
      );

      expect(result.status, EmergencyAccessStatus.active);
    });

    test(
      'deny follows the backend decision schema including duration',
      () async {
        final client = MockClient((request) async {
          expect(request.method, 'POST');
          expect(request.url.path, '/api/v1/emergency-access/requests/17/deny');
          expect(jsonDecode(request.body), <String, dynamic>{
            'duration_minutes': 15,
            'review_notes': 'Cannot approve',
          });
          return _ok(status: 'denied');
        });
        addTearDown(client.close);

        final result = await _source(client).denyRequest(
          emergencyAccessId: 17,
          durationMinutes: 15,
          reviewNotes: '  Cannot approve  ',
        );

        expect(result.status, EmergencyAccessStatus.denied);
      },
    );

    test('revoke sends the required normalized reason', () async {
      final client = MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.url.path, '/api/v1/emergency-access/requests/17/revoke');
        expect(jsonDecode(request.body), <String, dynamic>{
          'reason': 'Clinical need ended',
        });
        return _ok(status: 'revoked');
      });
      addTearDown(client.close);

      final result = await _source(
        client,
      ).revokeRequest(emergencyAccessId: 17, reason: '  Clinical need ended  ');

      expect(result.status, EmergencyAccessStatus.revoked);
    });

    test('validates request fields before resolving the session', () async {
      var sessionLoads = 0;
      var requests = 0;

      final client = MockClient((request) async {
        requests++;
        return _ok(code: 201);
      });
      addTearDown(client.close);

      final source = _source(
        client,
        loadAccessToken: () async {
          sessionLoads++;
          return _token;
        },
      );

      await expectLater(
        source.requestAccess(
          patientId: 0,
          reason: 'Emergency',
          purpose: 'Treatment',
          scope: const <String>['patient:read'],
        ),
        throwsArgumentError,
      );

      await expectLater(
        source.requestAccess(
          patientId: 7,
          reason: 'Emergency',
          purpose: 'Treatment',
          scope: const <String>['*:read'],
        ),
        throwsArgumentError,
      );

      expect(sessionLoads, 0);
      expect(requests, 0);
    });

    test('enforces the backend scope grammar locally', () async {
      final client = MockClient((request) async {
        throw StateError('No HTTP request expected.');
      });
      addTearDown(client.close);

      final source = _source(client);

      for (final scope in <List<String>>[
        const <String>[],
        const <String>['patient'],
        const <String>['*:read'],
        const <String>['patient:0:read'],
        const <String>['patient:abc:read'],
        const <String>['patient:read:extra:bad'],
      ]) {
        await expectLater(
          source.requestAccess(
            patientId: 7,
            reason: 'Emergency',
            purpose: 'Treatment',
            scope: scope,
          ),
          throwsArgumentError,
        );
      }
    });

    test('enforces decision and revoke limits before HTTP', () async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return _ok();
      });
      addTearDown(client.close);

      final source = _source(client);

      await expectLater(
        source.grantRequest(emergencyAccessId: 17, durationMinutes: 61),
        throwsArgumentError,
      );

      await expectLater(
        source.denyRequest(
          emergencyAccessId: 0,
          durationMinutes: 15,
          reviewNotes: 'Test review notes',
        ),
        throwsArgumentError,
      );

      await expectLater(
        source.revokeRequest(emergencyAccessId: 17, reason: '   '),
        throwsArgumentError,
      );

      expect(requests, 0);
    });

    test(
      'deny requires nonblank review notes before session or HTTP',
      () async {
        var sessionLoads = 0;
        var requests = 0;

        final client = MockClient((request) async {
          requests++;
          return _ok(status: 'denied');
        });
        addTearDown(client.close);

        final source = _source(
          client,
          loadAccessToken: () async {
            sessionLoads++;
            return _token;
          },
        );

        await expectLater(
          source.denyRequest(
            emergencyAccessId: 17,
            durationMinutes: 15,
            reviewNotes: '   ',
          ),
          throwsArgumentError,
        );

        expect(sessionLoads, 0);
        expect(requests, 0);
      },
    );

    test('session unavailable fails before HTTP', () async {
      var requests = 0;

      final client = MockClient((request) async {
        requests++;
        return _ok(code: 201);
      });
      addTearDown(client.close);

      final source = _source(
        client,
        loadAccessToken: () async {
          throw const AuthenticatedSessionUnavailableException(
            AuthenticatedSessionAccessFailure.signedOut,
          );
        },
      );

      await expectLater(
        source.requestAccess(
          patientId: 7,
          reason: 'Emergency',
          purpose: 'Treatment',
          scope: const <String>['patient:read'],
        ),
        throwsA(isA<AuthenticatedSessionUnavailableException>()),
      );

      expect(requests, 0);
    });

    test('rejects malformed success envelopes', () async {
      final client = MockClient((request) async {
        return http.Response(
          jsonEncode(<String, dynamic>{'success': true, 'data': <dynamic>[]}),
          200,
        );
      });
      addTearDown(client.close);

      await expectLater(
        _source(client).fetchRequest(emergencyAccessId: 17),
        throwsFormatException,
      );
    });
  });
}
