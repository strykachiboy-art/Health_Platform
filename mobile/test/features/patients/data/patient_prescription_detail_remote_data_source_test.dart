import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';

AuthSessionState _ready({String role = 'doctor', int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'detail-access',
        refreshToken: 'detail-refresh',
        userId: 12,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> _record({int clinic = 3, int patient = 7, int id = 11}) =>
    {
      'id': id,
      'clinic_id': clinic,
      'patient_id': patient,
      'consultation_id': null,
      'prescribed_by_id': 5,
      'status': 'active',
      'notes': 'Verified by staff',
      'issued_at': '2026-11-10T12:00:00+00:00',
      'expires_at': null,
      'items': [
        {
          'id': 111,
          'prescription_id': id,
          'drug_id': 55,
          'dosage': '500 mg',
          'frequency': 'twice daily',
          'duration': '7 days',
          'quantity': 14,
          'instructions': null,
          'dose_value': 500.0,
          'dose_unit': 'mg',
          'route_code': 'PO',
          'route_display': 'Oral',
          'frequency_code': 'BID',
          'frequency_interval': 12,
          'frequency_interval_unit': 'hour',
          'scheduled_times': ['08:00', '20:00'],
          'start_at': '2026-11-10T12:00:00+00:00',
          'end_at': '2026-11-17T12:00:00+00:00',
          'is_prn': false,
          'prn_reason': null,
          'max_single_dose': 500.0,
          'max_daily_dose': 1000.0,
          'administration_instructions': 'With food',
          'order_priority': 'routine',
          'order_type': 'outpatient',
          'ordered_at': '2026-11-10T11:59:00+00:00',
        },
      ],
    };

Map<String, dynamic> _envelope(Map<String, dynamic> row) => {
  'success': true,
  'data': row,
};

PatientPrescriptionRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  Future<AuthSessionState> Function()? loader,
}) => PatientPrescriptionRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: loader ?? () async => _ready(),
  sessionNavigationGuard: guard,
);

void main() {
  test('doctor detail reads one server row, all structured fields', () async {
    var calls = 0;
    final client = MockClient((request) async {
      calls++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/prescriptions/11');
      expect(request.headers['authorization'], 'Bearer detail-access');
      return http.Response(jsonEncode(_envelope(_record())), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final detail = await _source(
      client,
      guard,
    ).fetchPrescriptionDetail(prescriptionId: 11, expectedPatientId: 7);
    expect(calls, 1);
    expect(detail.prescription.id, 11);
    expect(detail.structuredItems.single.doseValue, 500.0);
    expect(detail.structuredItems.single.routeDisplay, 'Oral');
    expect(detail.structuredItems.single.frequencyInterval, 12);
    expect(detail.structuredItems.single.scheduledTimes, ['08:00', '20:00']);
    expect(detail.structuredItems.single.maxDailyDose, 1000.0);
    expect(detail.structuredItems.single.priority, 'routine');
    expect(detail.structuredItems.single.isPrn, isFalse);
  });

  test('pharmacist is a valid reader, nurse admin patient blocked', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response(jsonEncode(_envelope(_record())), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(
      client,
      guard,
      loader: () async => _ready(role: 'pharmacist'),
    );
    expect(
      (await source.fetchPrescriptionDetail(
        prescriptionId: 11,
        expectedPatientId: 7,
      )).prescription.patientId,
      7,
    );
    for (final role in ['nurse', 'admin', 'patient']) {
      await expectLater(
        _source(
          client,
          guard,
          loader: () async => _ready(role: role),
        ).fetchPrescriptionDetail(prescriptionId: 11, expectedPatientId: 7),
        throwsA(isA<PatientPrescriptionSessionMismatchException>()),
      );
    }
    expect(calls, 1);
  });

  test('wrong clinic patient prescription or item fails closed', () async {
    final badRows = [
      _record(clinic: 9),
      _record(patient: 8),
      _record(id: 99),
      (() {
        final row = _record();
        (row['items'] as List).first['drug_id'] = 0;
        return row;
      })(),
      (() {
        final row = _record();
        (row['items'] as List).first['prescription_id'] = 99;
        return row;
      })(),
    ];
    for (final row in badRows) {
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_envelope(row)), 200),
      );
      final guard = SessionNavigationGuard()..markSessionAvailable();
      await expectLater(
        _source(
          client,
          guard,
        ).fetchPrescriptionDetail(prescriptionId: 11, expectedPatientId: 7),
        throwsA(isA<FormatException>()),
      );
      guard.dispose();
      client.close();
    }
  });

  for (final field in [
    'order_priority',
    'order_type',
    'is_prn',
    'scheduled_times',
    'frequency_interval_unit',
    'dose_value',
    'start_at',
  ]) {
    test('invalid structured $field rejects detail', () async {
      final row = _record();
      final item = (row['items'] as List).first as Map<String, dynamic>;
      item[field] = switch (field) {
        'is_prn' => 'yes',
        'scheduled_times' => ['29:00'],
        'dose_value' => '500',
        'start_at' => '2026-11-10T12:00:00',
        _ => 'unsupported',
      };
      final client = MockClient(
        (_) async => http.Response(jsonEncode(_envelope(row)), 200),
      );
      final guard = SessionNavigationGuard()..markSessionAvailable();
      await expectLater(
        _source(
          client,
          guard,
        ).fetchPrescriptionDetail(prescriptionId: 11, expectedPatientId: 7),
        throwsA(isA<FormatException>()),
      );
      guard.dispose();
      client.close();
    });
  }

  test('bad numeric IDs reject before any HTTP', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final id in [0, -1, 2147483648]) {
      await expectLater(
        _source(
          client,
          guard,
        ).fetchPrescriptionDetail(prescriptionId: id, expectedPatientId: 7),
        throwsArgumentError,
      );
    }
    expect(calls, 0);
  });

  test('logout while HTTP pending discards late detail', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final future = _source(
      client,
      guard,
    ).fetchPrescriptionDetail(prescriptionId: 11, expectedPatientId: 7);
    await started.future;
    guard.invalidate();
    pending.complete(http.Response(jsonEncode(_envelope(_record())), 200));
    await expectLater(
      future,
      throwsA(isA<PatientPrescriptionSessionMismatchException>()),
    );
  });

  test('clinic switch mid-request discards old-clinic detail', () async {
    final started = Completer<void>();
    final pending = Completer<http.Response>();
    var state = _ready();
    final client = MockClient((_) {
      started.complete();
      return pending.future;
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final future = _source(
      client,
      guard,
      loader: () async => state,
    ).fetchPrescriptionDetail(prescriptionId: 11, expectedPatientId: 7);
    await started.future;
    state = _ready(clinic: 9);
    pending.complete(http.Response(jsonEncode(_envelope(_record())), 200));
    await expectLater(
      future,
      throwsA(isA<PatientPrescriptionSessionMismatchException>()),
    );
  });

  test('HTTP 403 makes one GET only; no retry or PHI', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{"error":"Forbidden"}', 403);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    await expectLater(
      _source(
        client,
        guard,
      ).fetchPrescriptionDetail(prescriptionId: 11, expectedPatientId: 7),
      throwsA(
        isA<ApiException>().having((e) => e.statusCode, 'statusCode', 403),
      ),
    );
    expect(calls, 1);
  });
}
