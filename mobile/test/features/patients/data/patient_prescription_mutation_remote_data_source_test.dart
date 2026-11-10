import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_mutation_remote_data_source.dart';

AuthSessionState _ready({String role = 'doctor', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 's02-auth',
        refreshToken: 's02-refresh',
        userId: 42,
        clinicContextId: clinic,
        role: role,
      ),
    );

Map<String, dynamic> _record({
  String status = 'active',
  int clinic = 3,
  int patient = 7,
  int id = 11,
  int drug = 55,
}) => <String, dynamic>{
  'id': id,
  'clinic_id': clinic,
  'patient_id': patient,
  'consultation_id': null,
  'prescribed_by_id': 8,
  'status': status,
  'notes': null,
  'issued_at': '2026-11-10T12:00:00+00:00',
  'expires_at': null,
  'items': <Map<String, dynamic>>[
    <String, dynamic>{
      'id': 111,
      'prescription_id': id,
      'drug_id': drug,
      'dosage': '500 mg',
      'frequency': 'twice daily',
      'duration': '7 days',
      'quantity': 14,
      'instructions': null,
      'dose_value': null,
      'dose_unit': null,
      'route_code': 'PO',
      'route_display': 'Oral',
      'frequency_code': null,
      'frequency_interval': null,
      'frequency_interval_unit': null,
      'scheduled_times': <String>[],
      'start_at': null,
      'end_at': null,
      'is_prn': false,
      'prn_reason': null,
      'max_single_dose': null,
      'max_daily_dose': null,
      'administration_instructions': null,
      'order_priority': 'routine',
      'order_type': 'outpatient',
      'ordered_at': null,
    },
  ],
};

http.Response _ok(Map<String, dynamic> row, {bool created = false}) =>
    http.Response(
      jsonEncode(<String, dynamic>{
        'success': true,
        'data': row,
        if (created) 'interaction_warnings': <Map<String, dynamic>>[],
      }),
      200,
    );

PatientPrescriptionMutationRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => PatientPrescriptionMutationRemoteDataSource(
  reader: PatientPrescriptionRemoteDataSource(
    apiClient: ApiClient(
      config: const BackendConfig(
        baseUrl: 'http://127.0.0.1:5000',
        allowInsecureHttp: true,
      ),
      client: client,
    ),
    loadSession: loader ?? () async => _ready(),
    sessionNavigationGuard: guard,
  ),
);

const medication = PrescriptionDraftMedication(
  drugId: 55,
  dosage: '500 mg',
  frequency: 'twice daily',
  duration: '7 days',
  quantity: 14,
  routeCode: 'PO',
  routeDisplay: 'Oral',
  orderPriority: 'routine',
  orderType: 'outpatient',
);

void main() {
  test(
    'doctor create posts once with real idempotency and GET readback',
    () async {
      final methods = <String>[];
      final client = MockClient((request) async {
        methods.add('${request.method} ${request.url.path}');
        if (request.method == 'POST') {
          expect(request.url.path, '/api/v1/prescriptions');
          expect(request.headers['idempotency-key'], 's02-create-unique');
          expect(request.headers['authorization'], 'Bearer s02-auth');
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          expect(body['patient_id'], 7);
          expect(body.containsKey('clinic_id'), isFalse);
          expect(body.containsKey('prescribed_by_id'), isFalse);
          expect(body['clinical_safety_acknowledged'], false);
          final items = body['items'] as List;
          expect(items.single['route_code'], 'PO');
          expect(items.single['order_priority'], 'routine');
          expect(items.single['order_type'], 'outpatient');
          return _ok(_record(), created: true);
        }
        return _ok(_record());
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final result = await source.createOnce(
        patientId: 7,
        medications: const [medication],
        idempotencyKey: 's02-create-unique',
      );
      expect(result.prescription.prescription.id, 11);
      expect(result.interactionWarnings, isEmpty);
      expect(source.writeAttempted, isTrue);
      expect(methods, [
        'POST /api/v1/prescriptions',
        'GET /api/v1/prescriptions/11',
      ]);
      await expectLater(
        source.createOnce(
          patientId: 7,
          medications: const [medication],
          idempotencyKey: 'another-key',
        ),
        throwsA(isA<PrescriptionMutationAlreadyAttempted>()),
      );
    },
  );

  test('pharmacist cannot create; no HTTP dispatched', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return _ok(_record());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(
      client,
      guard,
      loader: () async => _ready(role: 'pharmacist'),
    );
    await expectLater(
      source.createOnce(
        patientId: 7,
        medications: const [medication],
        idempotencyKey: 'key',
      ),
      throwsA(isA<PrescriptionMutationScopeUnavailable>()),
    );
    expect(calls, 0);
    expect(source.writeAttempted, isFalse);
  });

  test('duplicate drugs and invalid evidence never POST', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return _ok(_record());
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final items in <List<PrescriptionDraftMedication>>[
      const [],
      const [medication, medication],
      const [
        PrescriptionDraftMedication(
          drugId: 55,
          dosage: '',
          frequency: 'BID',
          duration: '7 days',
          quantity: 10,
          routeCode: 'PO',
          routeDisplay: 'Oral',
          orderPriority: 'routine',
          orderType: 'outpatient',
        ),
      ],
    ]) {
      await expectLater(
        _source(
          client,
          guard,
        ).createOnce(patientId: 7, medications: items, idempotencyKey: 'key'),
        throwsA(isA<FormatException>()),
      );
    }
    await expectLater(
      _source(client, guard).createOnce(
        patientId: 7,
        medications: const [medication],
        idempotencyKey: ' ',
      ),
      throwsA(isA<FormatException>()),
    );
    await expectLater(
      _source(client, guard).createOnce(
        patientId: 7,
        medications: const [medication],
        idempotencyKey: 'key',
        clinicalSafetyAcknowledged: true,
      ),
      throwsA(isA<FormatException>()),
    );
    expect(calls, 0);
  });

  test(
    'explicit CDS acknowledgement passes reason only when entered',
    () async {
      var postCount = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') {
          postCount++;
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          expect(body['clinical_safety_acknowledged'], true);
          expect(
            body['clinical_safety_justification'],
            'Clinician-reviewed assessment',
          );
          return _ok(_record(), created: true);
        }
        return _ok(_record());
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await _source(client, guard).createOnce(
        patientId: 7,
        medications: const [medication],
        idempotencyKey: 'one-key',
        clinicalSafetyAcknowledged: true,
        clinicalSafetyJustification: 'Clinician-reviewed assessment',
      );
      expect(postCount, 1);
    },
  );

  test('POST 409 is uncertain and never retried', () async {
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') {
        posts++;
        return http.Response('{"error":"Conflict"}', 409);
      }
      throw StateError('No GET expected');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await expectLater(
      source.createOnce(
        patientId: 7,
        medications: const [medication],
        idempotencyKey: 'key',
      ),
      throwsA(isA<PrescriptionMutationOutcomeUnconfirmed>()),
    );
    await expectLater(
      source.createOnce(
        patientId: 7,
        medications: const [medication],
        idempotencyKey: 'key',
      ),
      throwsA(isA<PrescriptionMutationAlreadyAttempted>()),
    );
    expect(posts, 1);
  });

  test('loss of session during POST prevents acceptance and replay', () async {
    final pending = Completer<http.Response>();
    final started = Completer<void>();
    var posts = 0;
    final client = MockClient((request) {
      if (request.method != 'POST') throw StateError('Unexpected read');
      posts++;
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final future = source.createOnce(
      patientId: 7,
      medications: const [medication],
      idempotencyKey: 'key',
    );
    await started.future;
    guard.invalidate();
    pending.complete(_ok(_record(), created: true));
    await expectLater(
      future,
      throwsA(isA<PrescriptionMutationOutcomeUnconfirmed>()),
    );
    expect(source.writeAttempted, isTrue);
    expect(posts, 1);
  });

  test('wrong-clinic create response is not treated as confirmed', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      return _ok(_record(clinic: 8), created: true);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(client, guard).createOnce(
        patientId: 7,
        medications: const [medication],
        idempotencyKey: 'key',
      ),
      throwsA(isA<PrescriptionMutationOutcomeUnconfirmed>()),
    );
    expect(calls, 1);
  });

  test(
    'successful POST without a successful GET readback is unconfirmed',
    () async {
      final methods = <String>[];
      final client = MockClient((request) async {
        methods.add(request.method);
        if (request.method == 'POST') return _ok(_record(), created: true);
        return http.Response('{"error":"Unavailable"}', 503);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        _source(client, guard).createOnce(
          patientId: 7,
          medications: const [medication],
          idempotencyKey: 'key',
        ),
        throwsA(isA<PrescriptionMutationOutcomeUnconfirmed>()),
      );
      expect(methods, ['POST', 'GET']);
    },
  );

  for (final entry in <(String, bool)>[
    ('doctor', true),
    ('pharmacist', false),
  ]) {
    test(
      '${entry.$1} completes only after GET-confirmed active status',
      () async {
        final requests = <String>[];
        final client = MockClient((request) async {
          requests.add('${request.method} ${request.url.path}');
          if (request.method == 'POST') {
            expect(request.body, '{}');
            return _ok(_record(status: 'completed'));
          }
          return _ok(
            _record(
              status: requests.any((x) => x.startsWith('POST'))
                  ? 'completed'
                  : 'active',
            ),
          );
        });
        addTearDown(client.close);
        final guard = SessionNavigationGuard()..markSessionAvailable();
        addTearDown(guard.dispose);
        final source = _source(
          client,
          guard,
          loader: () async => _ready(role: entry.$1),
        );
        final reviewed = await source.inspect(prescriptionId: 11, patientId: 7);
        final confirmed = await source.transitionOnce(
          reviewed: reviewed,
          cancel: false,
        );
        expect(confirmed.prescription.prescription.status.name, 'completed');
        expect(requests, [
          'GET /api/v1/prescriptions/11',
          'GET /api/v1/prescriptions/11',
          'POST /api/v1/prescriptions/11/complete',
          'GET /api/v1/prescriptions/11',
        ]);
      },
    );
  }

  test('doctor cancels with reason and backend readback', () async {
    final requests = <String>[];
    final client = MockClient((request) async {
      requests.add(request.method);
      if (request.method == 'POST') {
        expect(request.url.path, '/api/v1/prescriptions/11/cancel');
        expect(jsonDecode(request.body), {'reason': 'Documented reassessment'});
        return _ok(_record(status: 'cancelled'));
      }
      return _ok(
        _record(status: requests.contains('POST') ? 'cancelled' : 'active'),
      );
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final reviewed = await source.inspect(prescriptionId: 11, patientId: 7);
    final result = await source.transitionOnce(
      reviewed: reviewed,
      cancel: true,
      reason: 'Documented reassessment',
    );
    expect(result.prescription.prescription.status.name, 'cancelled');
    expect(requests, ['GET', 'GET', 'POST', 'GET']);
  });

  test(
    'pharmacist cannot cancel: no POST even with reviewed evidence',
    () async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'POST') posts++;
        return _ok(_record());
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(
        client,
        guard,
        loader: () async => _ready(role: 'pharmacist'),
      );
      final reviewed = await source.inspect(prescriptionId: 11, patientId: 7);
      await expectLater(
        source.transitionOnce(
          reviewed: reviewed,
          cancel: true,
          reason: 'Documented reason',
        ),
        throwsA(isA<PrescriptionMutationScopeUnavailable>()),
      );
      expect(posts, 0);
    },
  );

  test('stale reviewed record fails before POST', () async {
    var reads = 0;
    var posts = 0;
    final client = MockClient((request) async {
      if (request.method == 'POST') posts++;
      reads++;
      return _ok(_record(status: reads == 1 ? 'active' : 'completed'));
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final reviewed = await source.inspect(prescriptionId: 11, patientId: 7);
    await expectLater(
      source.transitionOnce(reviewed: reviewed, cancel: false),
      throwsA(isA<PrescriptionMutationEvidenceChanged>()),
    );
    expect(posts, 0);
    expect(source.writeAttempted, isFalse);
  });
}
