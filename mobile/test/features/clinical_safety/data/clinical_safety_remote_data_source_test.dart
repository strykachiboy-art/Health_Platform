import 'dart:convert';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/authenticated_session_access.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/clinical_safety_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert.dart';
import 'package:clinic_system_pro/features/clinical_safety/data/models/clinical_alert_page.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _token = 'phase21-test-token';
const _time = '2026-11-04T12:30:00+00:00';

Map<String, dynamic> _alertJson({
  String severity = 'critical',
  String action = 'block',
  String status = 'open',
}) => <String, dynamic>{
  'id': 19,
  'clinic_id': 3,
  'patient_id': 7,
  'rule_id': 12,
  'rule_version': 2,
  'severity': severity,
  'action': action,
  'status': status,
  'title': 'Medication warning',
  'message': 'Medication safety review is required.',
  'context': <String, dynamic>{'medication_id': 5},
  'source_type': 'prescription',
  'source_id': 9,
  'deduplication_key': 'clinical-alert-19',
  'generated_at': _time,
  'resolved_at': null,
  'created_at': _time,
  'updated_at': _time,
};

Map<String, dynamic> _pageJson({List<dynamic>? items}) => <String, dynamic>{
  'items': items ?? <dynamic>[_alertJson()],
  'total': 1,
  'page': 1,
  'per_page': 50,
};

ClinicalSafetyRemoteDataSource _source(
  MockClient client, {
  AuthenticatedAccessTokenLoader? loadAccessToken,
  void Function()? onAuthenticationRejected,
}) {
  final apiClient = ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  );
  apiClient.onAuthenticationRejected = onAuthenticationRejected;
  return ClinicalSafetyRemoteDataSource(
    apiClient: apiClient,
    loadAccessToken: loadAccessToken ?? () async => _token,
  );
}

http.Response _jsonResponse(Map<String, dynamic> body, {int code = 200}) =>
    http.Response(jsonEncode(body), code);

void main() {
  group('ClinicalAlert backend response models', () {
    test('parses the exact required alert fields and UTC timestamps', () {
      final alert = ClinicalAlert.fromJson(_alertJson());
      expect(alert.id, 19);
      expect(alert.clinicId, 3);
      expect(alert.patientId, 7);
      expect(alert.ruleVersion, 2);
      expect(alert.severity, ClinicalAlertSeverity.critical);
      expect(alert.action, ClinicalAlertAction.block);
      expect(alert.status, ClinicalAlertStatus.open);
      expect(alert.context['medication_id'], 5);
      expect(alert.sourceId, 9);
      expect(alert.resolvedAt, isNull);
      expect(alert.generatedAt.isUtc, isTrue);
      expect(
        () => alert.context['unexpected'] = 'value',
        throwsUnsupportedError,
      );
    });

    test('accepts every backend status, severity, and action', () {
      for (final value in <String>[
        'info',
        'low',
        'moderate',
        'high',
        'critical',
      ]) {
        expect(
          ClinicalAlert.fromJson(_alertJson(severity: value)).severity.name,
          value,
        );
      }
      for (final value in <String>[
        'open',
        'acknowledged',
        'overridden',
        'resolved',
        'expired',
      ]) {
        expect(
          ClinicalAlert.fromJson(_alertJson(status: value)).status.name,
          value,
        );
      }
      expect(
        ClinicalAlert.fromJson(_alertJson(action: 'require_acknowledgement'))
            .action,
        ClinicalAlertAction.requireAcknowledgement,
      );
      expect(
        ClinicalAlert.fromJson(_alertJson(action: 'require_justification'))
            .action,
        ClinicalAlertAction.requireJustification,
      );
      expect(
        ClinicalAlert.fromJson(_alertJson(action: 'alert')).action,
        ClinicalAlertAction.alert,
      );
      expect(
        ClinicalAlert.fromJson(_alertJson(action: 'inform')).action,
        ClinicalAlertAction.inform,
      );
    });

    test('rejects malformed IDs, enum values, context and timestamps', () {
      for (final mutation in <void Function(Map<String, dynamic>)>[
        (json) => json['clinic_id'] = 0,
        (json) => json['id'] = '19',
        (json) => json['severity'] = 'unknown',
        (json) => json['action'] = 'unsafe_action',
        (json) => json['status'] = 'granted',
        (json) => json['context'] = <dynamic>[],
        (json) => json['source_id'] = -1,
        (json) => json['generated_at'] = 'not-a-date',
        (json) => json['updated_at'] = null,
        (json) => json['title'] = '',
      ]) {
        final json = _alertJson();
        mutation(json);
        expect(() => ClinicalAlert.fromJson(json), throwsFormatException);
      }
    });

    test('parses nullable source/resolve fields and pagination metadata', () {
      final json = _alertJson(status: 'resolved');
      json['source_id'] = null;
      json['deduplication_key'] = null;
      json['resolved_at'] = _time;
      final page = ClinicalAlertPage.fromJson(
        _pageJson(items: <dynamic>[json]),
      );
      expect(page.total, 1);
      expect(page.page, 1);
      expect(page.perPage, 50);
      expect(page.items.single.sourceId, isNull);
      expect(page.items.single.resolvedAt, isNotNull);
      expect(page.items.single.status, ClinicalAlertStatus.resolved);
      expect(() => page.items.clear(), throwsUnsupportedError);
    });

    test('rejects malformed pages and impossible page size', () {
      for (final pageJson in <Map<String, dynamic>>[
        <String, dynamic>{'items': <dynamic>[], 'total': 0, 'page': 1},
        <String, dynamic>{
          ..._pageJson(),
          'items': <String>['bad'],
        },
        <String, dynamic>{..._pageJson(), 'total': '1'},
        <String, dynamic>{..._pageJson(), 'per_page': 0},
        <String, dynamic>{..._pageJson(), 'per_page': 501},
      ]) {
        expect(
          () => ClinicalAlertPage.fromJson(pageJson),
          throwsFormatException,
        );
      }
    });
  });

  group('ClinicalSafetyRemoteDataSource GET-only transport', () {
    test('lists unfiltered alerts using the flat response envelope', () async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/clinical-safety/alerts');
        expect(request.url.hasQuery, isFalse);
        expect(request.headers['authorization'], 'Bearer $_token');
        expect(request.headers.containsKey('idempotency-key'), isFalse);
        return _jsonResponse(_pageJson());
      });
      addTearDown(client.close);

      final page = await _source(client).fetchAlerts();
      expect(page.items.single.id, 19);
      expect(page.total, 1);
      expect(requests, 1);
    });

    test('loads a single alert from the detail alert envelope', () async {
      final client = MockClient((request) async {
        expect(request.method, 'GET');
        expect(request.url.path, '/api/v1/clinical-safety/alerts/19');
        expect(request.headers['authorization'], 'Bearer $_token');
        return _jsonResponse(<String, dynamic>{'alert': _alertJson()});
      });
      addTearDown(client.close);

      final result = await _source(client).fetchAlert(alertId: 19);
      expect(result.id, 19);
      expect(result.action, ClinicalAlertAction.block);
    });

    test('rejects invalid alert ID before session or HTTP', () async {
      var sessionLoads = 0;
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return _jsonResponse(<String, dynamic>{'alert': _alertJson()});
      });
      addTearDown(client.close);
      final source = _source(
        client,
        loadAccessToken: () async {
          sessionLoads++;
          return _token;
        },
      );

      await expectLater(source.fetchAlert(alertId: 0), throwsArgumentError);
      expect(sessionLoads, 0);
      expect(requests, 0);
    });

    test('signed-out session fails before any HTTP request', () async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return _jsonResponse(_pageJson());
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
        source.fetchAlerts(),
        throwsA(isA<AuthenticatedSessionUnavailableException>()),
      );
      expect(requests, 0);
    });

    test('rejects malformed backend list/detail envelopes', () async {
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/19')) {
          return _jsonResponse(<String, dynamic>{'data': _alertJson()});
        }
        return _jsonResponse(<String, dynamic>{
          'success': true,
          'data': _pageJson(),
        });
      });
      addTearDown(client.close);
      final source = _source(client);

      await expectLater(source.fetchAlerts(), throwsFormatException);
      await expectLater(source.fetchAlert(alertId: 19), throwsFormatException);
    });

    test(
      'HTTP 403 is surfaced and not silently interpreted as empty list',
      () async {
        final client = MockClient(
          (request) async => _jsonResponse(<String, dynamic>{
            'message': 'Forbidden',
          }, code: 403),
        );
        addTearDown(client.close);
        await expectLater(
          _source(client).fetchAlerts(),
          throwsA(
            isA<ApiException>().having(
              (exception) => exception.statusCode,
              'statusCode',
              403,
            ),
          ),
        );
      },
    );

    test('HTTP 401 triggers existing session invalidation callback', () async {
      var callbackCount = 0;
      final client = MockClient(
        (request) async => _jsonResponse(<String, dynamic>{
          'message': 'Unauthorized',
        }, code: 401),
      );
      addTearDown(client.close);
      await expectLater(
        _source(
          client,
          onAuthenticationRejected: () => callbackCount++,
        ).fetchAlert(alertId: 19),
        throwsA(
          isA<ApiException>().having(
            (exception) => exception.statusCode,
            'statusCode',
            401,
          ),
        ),
      );
      expect(callbackCount, 1);
    });

    test('HTTP 503 is not automatically retried', () async {
      var requests = 0;
      final client = MockClient((request) async {
        requests++;
        return _jsonResponse(<String, dynamic>{
          'message': 'Unavailable',
        }, code: 503);
      });
      addTearDown(client.close);
      await expectLater(
        _source(client).fetchAlerts(),
        throwsA(
          isA<ApiException>().having(
            (exception) => exception.statusCode,
            'statusCode',
            503,
          ),
        ),
      );
      expect(requests, 1);
    });
  });
}
