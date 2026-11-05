import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _stamp = '2026-11-05T10:00:00+00:00';
const _accessToken = 'test-staff-token';

Map<String, dynamic> _alert({
  int id = 19,
  int clinicId = 3,
  String status = 'resolved',
  String? resolvedAt = _stamp,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinicId,
  'patient_id': 7,
  'rule_id': 12,
  'rule_version': 2,
  'severity': 'critical',
  'action': 'block',
  'status': status,
  'title': 'Clinical review needed',
  'message': 'Review authoritative clinical safety information.',
  'context': <String, dynamic>{'sensitive': 'DO_NOT_RENDER'},
  'source_type': 'prescription',
  'source_id': 9,
  'deduplication_key': 'private-dedup-key',
  'generated_at': _stamp,
  'resolved_at': resolvedAt,
  'created_at': _stamp,
  'updated_at': _stamp,
};

http.Response _ok([Map<String, dynamic>? alert]) => http.Response(
  jsonEncode(<String, dynamic>{
    'message': 'Clinical alert resolved successfully',
    'alert': alert ?? _alert(),
  }),
  200,
);

ClinicalSafetyRemoteDataSource _source(
  MockClient client, {
  AuthenticatedAccessTokenLoader? loadAccessToken,
  void Function()? onRejected,
  Duration timeout = const Duration(seconds: 2),
}) {
  final api = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
    requestTimeout: timeout,
  )..onAuthenticationRejected = onRejected;
  return ClinicalSafetyRemoteDataSource(
    apiClient: api,
    loadAccessToken: loadAccessToken ?? () async => _accessToken,
  );
}

void main() {
  group('P21.S05A backend-authoritative resolution transport', () {
    test(
      'one authenticated POST with empty JSON body and no idempotency key',
      () async {
        var calls = 0;
        final client = MockClient((request) async {
          calls++;
          expect(request.method, 'POST');
          expect(request.url.path, '/api/v1/clinical-safety/alerts/19/resolve');
          expect(request.url.hasQuery, isFalse);
          expect(request.headers['authorization'], 'Bearer $_accessToken');
          expect(request.headers.containsKey('idempotency-key'), isFalse);
          expect(request.headers['content-type'], contains('application/json'));
          expect(jsonDecode(request.body), <String, dynamic>{});
          return _ok();
        });
        addTearDown(client.close);
        final result = await _source(client).resolveAlert(alertId: 19);
        expect(calls, 1);
        expect(result.id, 19);
        expect(result.clinicId, 3);
        expect(result.status, ClinicalAlertStatus.resolved);
        expect(result.resolvedAt, isNotNull);
        expect(result.resolvedAt!.isUtc, isTrue);
        expect(result.action, ClinicalAlertAction.block);
      },
    );

    test('invalid alert IDs fail before token loading or HTTP', () async {
      var loads = 0;
      var requests = 0;
      final client = MockClient((_) async {
        requests++;
        return _ok();
      });
      addTearDown(client.close);
      final source = _source(
        client,
        loadAccessToken: () async {
          loads++;
          return _accessToken;
        },
      );
      for (final id in <int>[0, -1, -100]) {
        await expectLater(
          source.resolveAlert(alertId: id),
          throwsArgumentError,
        );
      }
      expect(loads, 0);
      expect(requests, 0);
    });

    for (final failure in AuthenticatedSessionAccessFailure.values) {
      test('${failure.name} session prevents the POST', () async {
        var requests = 0;
        final client = MockClient((_) async {
          requests++;
          return _ok();
        });
        addTearDown(client.close);
        final source = _source(
          client,
          loadAccessToken: () async =>
              throw AuthenticatedSessionUnavailableException(failure),
        );
        await expectLater(
          source.resolveAlert(alertId: 19),
          throwsA(isA<AuthenticatedSessionUnavailableException>()),
        );
        expect(requests, 0);
      });
    }

    test(
      'HTTP 401 invokes session rejection callback and does not retry',
      () async {
        var calls = 0;
        var rejected = 0;
        final client = MockClient((_) async {
          calls++;
          return http.Response(
            jsonEncode(<String, dynamic>{'message': 'unauthorized'}),
            401,
          );
        });
        addTearDown(client.close);
        final source = _source(client, onRejected: () => rejected++);
        await expectLater(
          source.resolveAlert(alertId: 19),
          throwsA(
            isA<ApiException>().having((e) => e.statusCode, 'status', 401),
          ),
        );
        expect(calls, 1);
        expect(rejected, 1);
      },
    );

    for (final status in <int>[403, 404, 409, 422, 500, 503]) {
      test('HTTP $status is not retried or treated as resolution', () async {
        var calls = 0;
        final client = MockClient((_) async {
          calls++;
          return http.Response(
            jsonEncode(<String, dynamic>{'message': 'not confirmed'}),
            status,
          );
        });
        addTearDown(client.close);
        await expectLater(
          _source(client).resolveAlert(alertId: 19),
          throwsA(
            isA<ApiException>().having((e) => e.statusCode, 'status', status),
          ),
        );
        expect(calls, 1);
      });
    }

    test(
      'connection error is not retried and is classified as unconfirmed',
      () async {
        var calls = 0;
        final client = MockClient((_) async {
          calls++;
          throw http.ClientException('No network');
        });
        addTearDown(client.close);
        await expectLater(
          _source(client).resolveAlert(alertId: 19),
          throwsA(
            isA<ApiException>().having(
              (e) => e.kind,
              'kind',
              ApiFailureKind.connection,
            ),
          ),
        );
        expect(calls, 1);
      },
    );

    test(
      'timeout is not retried even if the server might have committed',
      () async {
        var calls = 0;
        final pending = Completer<http.Response>();
        final client = MockClient((_) {
          calls++;
          return pending.future;
        });
        addTearDown(client.close);
        await expectLater(
          _source(
            client,
            timeout: const Duration(milliseconds: 25),
          ).resolveAlert(alertId: 19),
          throwsA(
            isA<ApiException>().having(
              (e) => e.kind,
              'kind',
              ApiFailureKind.timeout,
            ),
          ),
        );
        expect(calls, 1);
        // Complete the abandoned mock request to prevent leaking a pending future.
        pending.complete(_ok());
      },
    );

    test('HTTP 200 malformed JSON remains an unconfirmed outcome', () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return http.Response('invalid { json', 200);
      });
      addTearDown(client.close);
      await expectLater(
        _source(client).resolveAlert(alertId: 19),
        throwsA(
          isA<ApiException>().having(
            (e) => e.kind,
            'kind',
            ApiFailureKind.invalidResponse,
          ),
        ),
      );
      expect(calls, 1);
    });

    for (final payload in <Map<String, dynamic>>[
      <String, dynamic>{'message': 'success without alert'},
      <String, dynamic>{'alert': null},
      <String, dynamic>{'alert': <String, dynamic>{}},
    ]) {
      test(
        'HTTP 200 missing/malformed alert is not confirmation: $payload',
        () async {
          var calls = 0;
          final client = MockClient((_) async {
            calls++;
            return http.Response(jsonEncode(payload), 200);
          });
          addTearDown(client.close);
          await expectLater(
            _source(client).resolveAlert(alertId: 19),
            throwsFormatException,
          );
          expect(calls, 1);
        },
      );
    }

    test('HTTP 200 wrong alert ID fails closed', () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return _ok(_alert(id: 20));
      });
      addTearDown(client.close);
      await expectLater(
        _source(client).resolveAlert(alertId: 19),
        throwsFormatException,
      );
      expect(calls, 1);
    });

    for (final status in <String>[
      'open',
      'acknowledged',
      'overridden',
      'expired',
    ]) {
      test('HTTP 200 $status is not mistaken for resolved', () async {
        var calls = 0;
        final client = MockClient((_) async {
          calls++;
          return _ok(_alert(status: status, resolvedAt: null));
        });
        addTearDown(client.close);
        await expectLater(
          _source(client).resolveAlert(alertId: 19),
          throwsFormatException,
        );
        expect(calls, 1);
      });
    }

    test('HTTP 200 resolved without resolved_at is not confirmation', () async {
      final client = MockClient((_) async => _ok(_alert(resolvedAt: null)));
      addTearDown(client.close);
      await expectLater(
        _source(client).resolveAlert(alertId: 19),
        throwsFormatException,
      );
    });

    test('HTTP 200 invalid resolved_at is not confirmation', () async {
      final client = MockClient(
        (_) async => _ok(_alert(resolvedAt: 'not-a-date')),
      );
      addTearDown(client.close);
      await expectLater(
        _source(client).resolveAlert(alertId: 19),
        throwsFormatException,
      );
    });

    test(
      'HTTP 200 with missing patient identity is not confirmation',
      () async {
        final broken = _alert()..remove('patient_id');
        final client = MockClient((_) async => _ok(broken));
        addTearDown(client.close);
        await expectLater(
          _source(client).resolveAlert(alertId: 19),
          throwsFormatException,
        );
      },
    );
  });
}
