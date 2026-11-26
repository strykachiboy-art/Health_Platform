import 'dart:convert';

import 'package:clinic_system_pro/core/sync/run2_module_catalog.dart';
import 'package:clinic_system_pro/features/blood_bank/data/blood_bank_contract.dart';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:clinic_system_pro/core/routing/app_router.dart';
import 'package:clinic_system_pro/features/backend_health/data/backend_health_remote_data_source.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/administration/presentation/wd05_administration_hub.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/blood_product_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/clinical_orders_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/diet_nursing_remote_data_source.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/imaging_procedure_remote_data_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';
import 'package:clinic_system_pro/features/home/presentation/wd04_clinical_workflow_hub.dart';
import 'package:clinic_system_pro/features/unified_search/data/search_source.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';

// Independent oracle of the promoted Flask policy, not derived from the
// adapter's resource map. All synthetic rows carry the same clinic binding.
const _adminResources = <String, List<String>>{
  'patient': ['active', 'inactive'],
  'appointment': [
    'cancelled',
    'completed',
    'confirmed',
    'no_show',
    'scheduled',
  ],
  'lab_order': [
    'cancelled',
    'completed',
    'in_progress',
    'ordered',
    'sample_collected',
  ],
};
const _deniedResources = <String>[
  'imaging_order',
  'procedure_order',
  'blood_product_order',
  'diet_order',
  'nursing_order',
  'nursing_task',
  'prescription',
  'medication_administration',
];
const _searchTenant = '12345678-1234-1234-1234-123456789abc';

SearchSource _adminSearch(MockClient client, SessionNavigationGuard guard) =>
    SearchSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
      loadSession: () async => AuthSessionState.ready(
        const AuthTokenResponse(
          accessToken: 'test-access',
          refreshToken: 'test-refresh',
          userId: 41,
          role: 'admin',
          clinicContextId: 7,
          tenantId: _searchTenant,
        ),
      ),
      guard: guard,
    );

Map<String, dynamic> _searchHit(String type, String status, {int? admission}) =>
    {
      'resource_type': type,
      'record_id': 7,
      'patient_id': 7,
      'patient_name': 'Synthetic Person',
      'patient_number': 'SYN-007',
      'status': status,
      'admission_id': admission,
      'kind': type == 'patient' ? 'patient' : 'operational',
    };

http.Response _searchReply(
  List<Map<String, dynamic>> items, {
  Map<String, List<String>> resources = _adminResources,
}) => http.Response(
  jsonEncode({
    'success': true,
    'data': {
      'organization_id': _searchTenant,
      'clinic_id': 7,
      'available_resources': resources,
      'items': items,
      'next_cursor': null,
    },
  }),
  200,
  headers: {'content-type': 'application/json'},
);

void main() {
  test(
    'alternate blood bank and offline entry points obey clinical order policy',
    () {
      expect(BbAction.orders.roles, {'doctor', 'nurse', 'lab_technician'});
      expect(BbAction.order.roles, {'doctor', 'nurse', 'lab_technician'});
      expect(BbAction.stock.roles, contains('admin'));
      expect(
        Run2ModuleCatalog.readsFor('admin', true).map((s) => s.key),
        isNot(contains('nursing_order')),
      );
      for (final role in ['doctor', 'nurse']) {
        expect(
          Run2ModuleCatalog.readsFor(role, true).map((s) => s.key),
          contains('nursing_order'),
        );
      }
    },
  );

  test('admin search accepts exactly the three promoted resources', () async {
    var calls = 0;
    final client = MockClient((r) async {
      calls++;
      expect(r.method, 'POST');
      expect(r.url.path, '/api/v1/search');
      expect(r.headers['authorization'], 'Bearer test-access');
      expect(r.url.query, isEmpty);
      return _searchReply([
        _searchHit('patient', 'active'),
        _searchHit('appointment', 'scheduled'),
        _searchHit('lab_order', 'ordered'),
      ]);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = await _adminSearch(client, guard).open();
    expect(s.available, _adminResources.map((k, v) => MapEntry(k, v.toSet())));
    final result = await s.read(SearchFilters('Synthetic'));
    expect(result.items.map((x) => x.resourceType), _adminResources.keys);
    expect(result.items.map((x) => x.routeFor('admin')), [
      '/patients/7',
      '/appointments/patient/7',
      '/patients/7/lab-orders',
    ]);
    expect(calls, 1);
  });

  test('admin search denies all eight clinical filters before HTTP', () async {
    var calls = 0;
    final client = MockClient((r) async {
      calls++;
      throw StateError('Unexpected HTTP');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final s = await _adminSearch(client, guard).open();
    expect(s.available.keys.toSet(), _adminResources.keys.toSet());
    for (final kind in _deniedResources) {
      await expectLater(
        s.read(SearchFilters('#7', resourceType: kind)),
        throwsA(isA<SearchUnavailable>()),
      );
    }
    expect(calls, 0);
  });

  test(
    'admin search rejects stale resource metadata and forbidden rows',
    () async {
      var calls = 0;
      final client = MockClient((r) async {
        calls++;
        if (calls == 1) {
          return _searchReply(
            [],
            resources: {
              ..._adminResources,
              'imaging_order': ['active'],
            },
          );
        }
        return _searchReply([
          _searchHit('imaging_order', 'active', admission: 9),
        ]);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final s = await _adminSearch(client, guard).open();
      await expectLater(
        s.read(SearchFilters('Synthetic')),
        throwsFormatException,
      );
      await expectLater(
        s.read(SearchFilters('Synthetic')),
        throwsFormatException,
      );
      expect(calls, 2);
    },
  );

  test('admin clinical order sources deny before HTTP', () async {
    var calls = 0;
    final client = MockClient((r) async {
      calls++;
      throw StateError('Unexpected HTTP');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final unified = ClinicalOrdersRemoteDataSource(
      apiClient: ApiClient(
        config: const BackendConfig(
          baseUrl: 'http://127.0.0.1:5000',
          allowInsecureHttp: true,
        ),
        client: client,
      ),
      loadSession: () async => AuthSessionState.ready(
        const AuthTokenResponse(
          accessToken: 'test-access',
          refreshToken: 'test-refresh',
          userId: 41,
          role: 'admin',
          clinicContextId: 7,
          tenantId: '12345678-1234-1234-1234-123456789abc',
        ),
      ),
      sessionNavigationGuard: guard,
    );
    await expectLater(
      unified.fetchPatientOrders(patientId: 7),
      throwsA(isA<ClinicalOrdersSessionException>()),
    );
    final imaging = ImagingProcedureRemoteDataSource(unifiedSource: unified);
    for (final kind in ['imaging', 'procedures']) {
      await expectLater(
        imaging.list(kind: kind, patientId: 7, admissionId: 9),
        throwsA(isA<ImagingProcedureSessionException>()),
      );
      await expectLater(
        imaging.get(kind: kind, patientId: 7, admissionId: 9, orderId: 17),
        throwsA(isA<ImagingProcedureSessionException>()),
      );
    }
    final diet = DietNursingRemoteDataSource(unifiedSource: unified);
    for (final kind in ['diets', 'nursing-orders']) {
      await expectLater(
        diet.list(kind: kind, patientId: 7, admissionId: 9),
        throwsA(isA<DietNursingSessionException>()),
      );
      await expectLater(
        diet.get(kind: kind, patientId: 7, admissionId: 9, orderId: 17),
        throwsA(isA<DietNursingSessionException>()),
      );
    }
    await expectLater(
      diet.executions(patientId: 7, admissionId: 9, orderId: 17),
      throwsA(isA<DietNursingSessionException>()),
    );
    final blood = BloodProductRemoteDataSource(unifiedSource: unified);
    await expectLater(
      blood.list(patientId: 7, admissionId: 9),
      throwsA(isA<BloodProductSessionException>()),
    );
    await expectLater(
      blood.get(orderId: 17),
      throwsA(isA<BloodProductSessionException>()),
    );
    expect(calls, 0);
  });

  test(
    'admin navigation retains management and excludes clinical order entry',
    () {
      final home = homeDestinationsFor(
        role: 'admin',
        clinicContextId: 7,
      ).map((e) => e.id).toSet();
      expect(home, containsAll(['patients', 'appointments', 'administration']));
      expect(home, isNot(contains('clinical_orders')));
      expect(home, isNot(contains('consultations')));
      expect(home, isNot(contains('radiology')));
      final workflow = wd04ClinicalWorkflowLinks(
        role: 'admin',
        clinicContextId: 7,
      ).map((e) => e.id).toSet();
      expect(workflow, isNot(contains('clinical_orders')));
      expect(workflow, isNot(contains('radiology')));
      final admin = wd05AdministrationLinks(role: 'admin', clinicContextId: 7);
      expect(
        admin.singleWhere((e) => e.id == 'clinic_workspace').path,
        '/administration/clinic-workspace',
      );
      expect(admin.map((e) => e.id), isNot(contains('sa_workspace')));
      for (final role in ['super_admin', 'doctor', 'nurse', 'patient']) {
        expect(
          wd05AdministrationLinks(
            role: role,
            clinicContextId: 7,
          ).map((e) => e.id),
          isNot(contains('clinic_workspace')),
        );
      }
      expect(
        wd05AdministrationLinks(role: 'admin', clinicContextId: null),
        isEmpty,
      );
      expect(searchRoles, contains('admin'));
      expect(searchRoles, contains('doctor'));
      final doctor = homeDestinationsFor(
        role: 'doctor',
        clinicContextId: 7,
      ).map((e) => e.id);
      expect(
        doctor,
        containsAll(['consultations', 'clinical_orders', 'radiology']),
      );
    },
  );

  testWidgets('admin direct clinical deep links return to home without HTTP', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((r) async {
      calls++;
      throw StateError('Unexpected HTTP');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final router = createAppRouter(
      healthDataSource: BackendHealthRemoteDataSource(
        apiClient: ApiClient(
          config: const BackendConfig(
            baseUrl: 'http://127.0.0.1:5000',
            allowInsecureHttp: true,
          ),
          client: client,
        ),
      ),
      loginScreen: const Scaffold(body: Text('Login sentinel')),
      sessionNavigationGuard: guard,
      loadHomeSession: () async => AuthSessionState.ready(
        const AuthTokenResponse(
          accessToken: 'test-access',
          refreshToken: 'test-refresh',
          userId: 41,
          role: 'admin',
          clinicContextId: 7,
          tenantId: '12345678-1234-1234-1234-123456789abc',
        ),
      ),
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    await tester.pumpAndSettle();
    for (final path in [
      '/consultations',
      '/consultations/patient/7/11/note',
      '/clinical-orders',
      '/clinical-orders/patient/7/imaging',
      '/clinical-orders/patient/7/procedures',
      '/clinical-orders/patient/7/diets',
      '/clinical-orders/patient/7/nursing-orders',
      '/clinical-orders/blood-products',
    ]) {
      router.go(path);
      await tester.pumpAndSettle();
      expect(
        router.routeInformationProvider.value.uri.path,
        '/home',
        reason: path,
      );
    }
    expect(calls, 0);
  });
}
