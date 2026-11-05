import 'dart:async';
import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert_acknowledgement.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _time = '2026-11-05T10:00:00+00:00';
const _token = 'staff-access-token';

Map<String, dynamic> _ack({
  int id = 41,
  int alertId = 19,
  int userId = 42,
  String type = 'acknowledged',
  String? justification,
}) => <String, dynamic>{
  'id': id,
  'alert_id': alertId,
  'acknowledged_by_user_id': userId,
  'acknowledgement_type': type,
  'justification': justification,
  'acknowledged_at': _time,
  'created_at': _time,
  'updated_at': _time,
};

http.Response _success(Map<String, dynamic> ack) => http.Response(
  jsonEncode(<String, dynamic>{
    'message': 'Clinical alert acknowledgement recorded',
    'acknowledgement': ack,
  }),
  201,
);

http.Response _error(int status) => http.Response(
  jsonEncode(<String, dynamic>{'message': 'Backend rejected operation'}),
  status,
);

ClinicalSafetyRemoteDataSource _source(
  MockClient client, {
  AuthenticatedAccessTokenLoader? loadAccessToken,
  void Function()? onRejected,
  Duration timeout = const Duration(seconds: 1),
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
    loadAccessToken: loadAccessToken ?? () async => _token,
  );
}

void main() {
  group('ClinicalAlertAcknowledgement backend model', () {
    test('parses backend fields and UTC timestamps', () {
      final value = ClinicalAlertAcknowledgement.fromJson(_ack());
      expect(value.id, 41);
      expect(value.alertId, 19);
      expect(value.acknowledgedByUserId, 42);
      expect(value.type, ClinicalAlertAcknowledgementType.acknowledged);
      expect(value.justification, isNull);
      expect(value.acknowledgedAt.isUtc, isTrue);
      expect(value.createdAt.isUtc, isTrue);
      expect(value.updatedAt.isUtc, isTrue);
    });

    test('recognizes overridden acknowledgement with justification', () {
      final value = ClinicalAlertAcknowledgement.fromJson(
        _ack(type: 'overridden', justification: 'Clinical rationale'),
      );
      expect(value.type, ClinicalAlertAcknowledgementType.overridden);
      expect(value.justification, 'Clinical rationale');
    });

    test('rejects invalid ids, type, timestamp and justification', () {
      for (final change in <void Function(Map<String, dynamic>)>[
        (a) => a['id'] = 0,
        (a) => a['alert_id'] = '19',
        (a) => a['acknowledged_by_user_id'] = -1,
        (a) => a['acknowledgement_type'] = 'resolved',
        (a) => a['justification'] = <String>[],
        (a) => a['acknowledged_at'] = null,
        (a) => a['updated_at'] = 'bad-date',
        (a) => a.remove('created_at'),
      ]) {
        final payload = _ack();
        change(payload);
        expect(
          () => ClinicalAlertAcknowledgement.fromJson(payload),
          throwsFormatException,
        );
      }
    });
  });

  group('Clinical Safety acknowledgement write transport', () {
    test(
      'acknowledge sends one authenticated POST without idempotency',
      () async {
        var calls = 0;
        final client = MockClient((request) async {
          calls++;
          expect(request.method, 'POST');
          expect(
            request.url.path,
            '/api/v1/clinical-safety/alerts/19/acknowledge',
          );
          expect(request.url.hasQuery, isFalse);
          expect(request.headers['authorization'], 'Bearer $_token');
          expect(request.headers.containsKey('idempotency-key'), isFalse);
          expect(jsonDecode(request.body), <String, dynamic>{
            'acknowledgement_type': 'acknowledged',
          });
          return _success(_ack());
        });
        addTearDown(client.close);
        final result = await _source(client).acknowledgeAlert(
          alertId: 19,
          type: ClinicalAlertAcknowledgementType.acknowledged,
        );
        expect(result.alertId, 19);
        expect(result.type, ClinicalAlertAcknowledgementType.acknowledged);
        expect(calls, 1);
      },
    );

    test('override requires and trims documented justification', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(jsonDecode(request.body), <String, dynamic>{
          'acknowledgement_type': 'overridden',
          'justification': 'Urgent documented reason',
        });
        return _success(
          _ack(type: 'overridden', justification: 'Urgent documented reason'),
        );
      });
      addTearDown(client.close);
      final result = await _source(client).acknowledgeAlert(
        alertId: 19,
        type: ClinicalAlertAcknowledgementType.overridden,
        justification: '  Urgent documented reason  ',
      );
      expect(result.type, ClinicalAlertAcknowledgementType.overridden);
      expect(result.justification, 'Urgent documented reason');
      expect(calls, 1);
    });

    test('optional acknowledgement justification is trimmed', () async {
      final client = MockClient((request) async {
        expect(jsonDecode(request.body), <String, dynamic>{
          'acknowledgement_type': 'acknowledged',
          'justification': 'I reviewed the warning',
        });
        return _success(_ack(justification: 'I reviewed the warning'));
      });
      addTearDown(client.close);
      await _source(client).acknowledgeAlert(
        alertId: 19,
        type: ClinicalAlertAcknowledgementType.acknowledged,
        justification: ' I reviewed the warning ',
      );
    });

    test('blank optional justification is omitted from body', () async {
      final client = MockClient((request) async {
        expect(jsonDecode(request.body), <String, dynamic>{
          'acknowledgement_type': 'acknowledged',
        });
        return _success(_ack());
      });
      addTearDown(client.close);
      await _source(client).acknowledgeAlert(
        alertId: 19,
        type: ClinicalAlertAcknowledgementType.acknowledged,
        justification: '   ',
      );
    });

    test(
      'rejects invalid alert ID before session acquisition or HTTP',
      () async {
        var loads = 0;
        var calls = 0;
        final client = MockClient((_) async {
          calls++;
          return _success(_ack());
        });
        addTearDown(client.close);
        final source = _source(
          client,
          loadAccessToken: () async {
            loads++;
            return _token;
          },
        );
        await expectLater(
          source.acknowledgeAlert(
            alertId: 0,
            type: ClinicalAlertAcknowledgementType.acknowledged,
          ),
          throwsArgumentError,
        );
        expect(loads, 0);
        expect(calls, 0);
      },
    );

    test('override cannot submit missing or blank justification', () async {
      var calls = 0;
      var loads = 0;
      final client = MockClient((_) async {
        calls++;
        return _success(_ack());
      });
      addTearDown(client.close);
      final source = _source(
        client,
        loadAccessToken: () async {
          loads++;
          return _token;
        },
      );
      for (final justification in <String?>[null, '', '   ']) {
        await expectLater(
          source.acknowledgeAlert(
            alertId: 19,
            type: ClinicalAlertAcknowledgementType.overridden,
            justification: justification,
          ),
          throwsArgumentError,
        );
      }
      expect(loads, 0);
      expect(calls, 0);
    });

    test('5001-character justification rejected before session/HTTP', () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return _success(_ack());
      });
      addTearDown(client.close);
      await expectLater(
        _source(client).acknowledgeAlert(
          alertId: 19,
          type: ClinicalAlertAcknowledgementType.overridden,
          justification: 'j'.padRight(5001, 'j'),
        ),
        throwsArgumentError,
      );
      expect(calls, 0);
    });

    test('5000-character justification accepted without truncation', () async {
      final original = 'j'.padRight(5000, 'j');
      final client = MockClient((request) async {
        expect((jsonDecode(request.body) as Map)['justification'], original);
        return _success(_ack(type: 'overridden', justification: original));
      });
      addTearDown(client.close);
      final result = await _source(client).acknowledgeAlert(
        alertId: 19,
        type: ClinicalAlertAcknowledgementType.overridden,
        justification: original,
      );
      expect(result.justification, original);
    });

    test('signed-out session blocks write before HTTP', () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        return _success(_ack());
      });
      addTearDown(client.close);
      final source = _source(
        client,
        loadAccessToken: () async =>
            throw const AuthenticatedSessionUnavailableException(
              AuthenticatedSessionAccessFailure.signedOut,
            ),
      );
      await expectLater(
        source.acknowledgeAlert(
          alertId: 19,
          type: ClinicalAlertAcknowledgementType.acknowledged,
        ),
        throwsA(isA<AuthenticatedSessionUnavailableException>()),
      );
      expect(calls, 0);
    });

    test('successful response with wrong alert ID is unconfirmed', () async {
      final client = MockClient((_) async => _success(_ack(alertId: 20)));
      addTearDown(client.close);
      await expectLater(
        _source(client).acknowledgeAlert(
          alertId: 19,
          type: ClinicalAlertAcknowledgementType.acknowledged,
        ),
        throwsFormatException,
      );
    });

    test(
      'successful response with wrong acknowledgement type is unconfirmed',
      () async {
        final client = MockClient(
          (_) async => _success(_ack(type: 'overridden')),
        );
        addTearDown(client.close);
        await expectLater(
          _source(client).acknowledgeAlert(
            alertId: 19,
            type: ClinicalAlertAcknowledgementType.acknowledged,
          ),
          throwsFormatException,
        );
      },
    );

    test(
      'malformed success response cannot confirm a clinical write',
      () async {
        final client = MockClient(
          (_) async => http.Response(
            jsonEncode(<String, dynamic>{
              'message': 'recorded',
              'data': _ack(),
            }),
            201,
          ),
        );
        addTearDown(client.close);
        await expectLater(
          _source(client).acknowledgeAlert(
            alertId: 19,
            type: ClinicalAlertAcknowledgementType.acknowledged,
          ),
          throwsFormatException,
        );
      },
    );

    test('HTTP 401 invalidates session and does not retry', () async {
      var calls = 0;
      var rejections = 0;
      final client = MockClient((_) async {
        calls++;
        return _error(401);
      });
      addTearDown(client.close);
      await expectLater(
        _source(client, onRejected: () => rejections++).acknowledgeAlert(
          alertId: 19,
          type: ClinicalAlertAcknowledgementType.acknowledged,
        ),
        throwsA(isA<ApiException>().having((e) => e.statusCode, 'HTTP', 401)),
      );
      expect(calls, 1);
      expect(rejections, 1);
    });

    for (final code in <int>[403, 404, 409, 422, 503]) {
      test('HTTP $code surfaces unchanged without blind retry', () async {
        var calls = 0;
        final client = MockClient((_) async {
          calls++;
          return _error(code);
        });
        addTearDown(client.close);
        await expectLater(
          _source(client).acknowledgeAlert(
            alertId: 19,
            type: ClinicalAlertAcknowledgementType.overridden,
            justification: 'Documented rationale',
          ),
          throwsA(
            isA<ApiException>().having((e) => e.statusCode, 'HTTP', code),
          ),
        );
        expect(calls, 1);
      });
    }

    test('timeout propagates without retry; caller must reconcile', () async {
      var calls = 0;
      final never = Completer<http.Response>();
      final client = MockClient((_) {
        calls++;
        return never.future;
      });
      addTearDown(client.close);
      await expectLater(
        _source(
          client,
          timeout: const Duration(milliseconds: 20),
        ).acknowledgeAlert(
          alertId: 19,
          type: ClinicalAlertAcknowledgementType.acknowledged,
        ),
        throwsA(
          isA<ApiException>().having(
            (e) => e.kind,
            'kind',
            ApiFailureKind.timeout,
          ),
        ),
      );
      expect(calls, 1);
    });

    test('connection error propagates without retry', () async {
      var calls = 0;
      final client = MockClient((_) {
        calls++;
        throw http.ClientException('simulated offline');
      });
      addTearDown(client.close);
      await expectLater(
        _source(client).acknowledgeAlert(
          alertId: 19,
          type: ClinicalAlertAcknowledgementType.acknowledged,
        ),
        throwsA(
          isA<ApiException>().having(
            (e) => e.kind,
            'kind',
            ApiFailureKind.connection,
          ),
        ),
      );
      expect(calls, 1);
    });
  });
}
