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
import 'package:clinic_system_pro/features/clinical_orders/data/blood_product_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/diet_nursing_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/imaging_procedure_remote_data_source.dart';

AuthSessionState _ready(String role, {int clinic = 3}) =>
    AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'token',
        refreshToken: 'refresh',
        userId: 41,
        role: role,
        clinicContextId: clinic,
      ),
    );

Map<String, dynamic> _blood() => <String, dynamic>{
  'id': 17,
  'clinic_id': 3,
  'patient_id': 7,
  'admission_id': 9,
  'ordered_by_staff_id': 8,
  'product_type': 'packed_red_blood_cells',
  'units_requested': 2,
  'units_issued': 0,
  'indication': 'Acute blood loss',
  'priority': 'urgent',
  'status': 'active',
  'version': 1,
  'patient_blood_type_at_order': 'O+',
  'compatibility_status': 'pending',
  'ordered_at': '2026-11-07T08:00:00+00:00',
};

ClinicalOrdersRemoteDataSource _unified(
  MockClient client,
  SessionNavigationGuard guard,
  String role,
) => ClinicalOrdersRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _ready(role),
  sessionNavigationGuard: guard,
);

void main() {
  test(
    'lab blood detail remains available while other order APIs deny lab',
    () async {
      var gets = 0;
      final client = MockClient((req) async {
        gets++;
        expect(req.url.path, '/api/v1/clinical-orders/blood-products/17');
        return http.Response(jsonEncode(_blood()), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final unified = _unified(client, guard, 'lab_technician');
      final blood = BloodProductRemoteDataSource(unifiedSource: unified);
      expect((await blood.get(orderId: 17)).patientId, 7);
      await expectLater(
        unified.fetchPatientOrders(patientId: 7),
        throwsA(isA<ClinicalOrdersSessionException>()),
      );
      await expectLater(
        ImagingProcedureRemoteDataSource(unifiedSource: unified)
            .list(kind: 'imaging', patientId: 7, admissionId: 9),
        throwsA(isA<ImagingProcedureSessionException>()),
      );
      await expectLater(
        DietNursingRemoteDataSource(unifiedSource: unified)
            .list(kind: 'diets', patientId: 7, admissionId: 9),
        throwsA(isA<DietNursingSessionException>()),
      );
      expect(gets, 1);
    },
  );

  test('blood module rejects non-clinical roles before any HTTP', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('{}', 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    for (final role in <String>[
      'patient',
      'pharmacist',
      'accountant',
      'receptionist',
      'super_admin',
    ]) {
      await expectLater(
        BloodProductRemoteDataSource(
          unifiedSource: _unified(client, guard, role),
        ).get(orderId: 17),
        throwsA(isA<BloodProductSessionException>()),
      );
    }
    expect(calls, 0);
  });

  test(
    'blood product read rejects foreign-clinic payload without mutation',
    () async {
      var calls = 0;
      final client = MockClient((req) async {
        calls++;
        final row = _blood()..['clinic_id'] = 4;
        return http.Response(jsonEncode(row), 200);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      await expectLater(
        BloodProductRemoteDataSource(
          unifiedSource: _unified(client, guard, 'lab_technician'),
        ).get(orderId: 17),
        throwsFormatException,
      );
      expect(calls, 1);
    },
  );

  test(
    'pending clinical mutation followed by sign-out must fail closed',
    () async {
      final pending = Completer<http.Response>();
      var posts = 0;
      final client = MockClient((request) {
        posts++;
        return pending.future;
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final future =
          BloodProductRemoteDataSource(
            unifiedSource: _unified(client, guard, 'doctor'),
          ).create(
            patientId: 7,
            admissionId: 9,
            productType: 'packed_red_blood_cells',
            unitsRequested: 2,
            indication: 'Acute blood loss',
          );
      await Future<void>.delayed(Duration.zero);
      guard.markSignedOut();
      pending.complete(http.Response(jsonEncode({'order': _blood()}), 201));
      await expectLater(future, throwsA(isA<BloodProductSessionException>()));
      expect(posts, 1);
    },
  );
}
