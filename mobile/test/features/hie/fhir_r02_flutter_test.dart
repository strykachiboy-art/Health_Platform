import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/hie/data/fhir_export_preview.dart';
import 'package:clinic_system_pro/features/hie/data/hie_remote_data_source.dart';
import 'package:clinic_system_pro/features/hie/presentation/fhir_export_panel.dart';

import '../problems/problem_contract_test.dart' as fixtures;

const tenant = 'b11c9d07-7411-4526-a42a-e0d9bda625d5';
const issuer = 'https://synthetic.example.invalid/fhir/$tenant/1';
Map<String, dynamic> bundle() =>
    jsonDecode(syntheticBundleJson) as Map<String, dynamic>;
FhirExportPreview parse(Map<String, dynamic> raw, {String section = 'all'}) =>
    FhirExportPreview.parse(
      raw,
      patientId: 1,
      clinicId: 1,
      tenantId: tenant,
      section: section,
    );

AuthSessionState actor({
  String role = 'doctor',
  String? organization = tenant,
  int clinic = 1,
  String token = 'bound-token',
}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: token,
    refreshToken: 'refresh-token',
    userId: 19,
    role: role,
    clinicContextId: clinic,
    tenantId: organization,
  ),
);

class Harness {
  Harness(MockClient client, {AuthSessionState? state})
    : state = state ?? actor() {
    source = HieRemoteDataSource(
      apiClient: fixtures.apiFor(client),
      loadSession: () async => this.state,
      guard: guard,
    );
    addTearDown(guard.dispose);
    addTearDown(client.close);
  }
  AuthSessionState state;
  final guard = SessionNavigationGuard()..markSessionAvailable();
  late final HieRemoteDataSource source;
}

http.Response receipt(Map<String, dynamic> value) => http.Response.bytes(
  utf8.encode(jsonEncode(value)),
  200,
  headers: {
    'content-type': 'application/fhir+json',
    'cache-control': 'no-store',
  },
);

void main() {
  test(
    'parses real synthetic FHIR-R02 SQLite evidence, all seven resource types',
    () {
      final result = parse(bundle());
      expect(result.patientId, 1);
      expect(result.rows.length, 12);
      expect(result.rows.map((r) => r.resourceType).toSet(), {
        'Patient',
        'Encounter',
        'Observation',
        'Condition',
        'AllergyIntolerance',
        'MedicationRequest',
        'DiagnosticReport',
      });
      expect(
        result.rows
            .where((row) => row.id.endsWith('85354-9'))
            .single
            .description,
        contains('Systolic blood pressure'),
      );
      expect(
        result.rows
            .where((row) => row.id.endsWith('8310-5'))
            .single
            .description,
        contains('°C'),
      );
    },
  );
  test('rejects collection total, foreign namespace, patient and dangling report reference', () {
    expect(() => parse(bundle()..['total'] = 12), throwsFormatException);
    final foreign = bundle();
    final entries = foreign['entry'] as List<dynamic>;
    (entries[0] as Map<String, dynamic>)['fullUrl'] =
        'https://other.invalid/Patient/1';
    expect(() => parse(foreign), throwsFormatException);
    final mismatch = bundle();
    final second =
        (mismatch['entry'] as List<dynamic>)[1] as Map<String, dynamic>;
    (second['resource'] as Map<String, dynamic>)['subject'] = {
      'reference': 'Patient/2',
    };
    expect(() => parse(mismatch), throwsFormatException);
    final dangling = bundle();
    (dangling['entry'] as List<dynamic>).removeLast();
    expect(() => parse(dangling), throwsFormatException);
  });
  test(
    'rejects wrong section, duplicate, unexpected resource and empty response',
    () {
      expect(
        () => parse(bundle(), section: 'conditions'),
        throwsFormatException,
      );
      final duplicate = bundle();
      final entries = duplicate['entry'] as List<dynamic>;
      entries.add(entries.first);
      expect(() => parse(duplicate), throwsFormatException);
      expect(() => parse(bundle()..['entry'] = []), throwsFormatException);
      final unexpected = bundle();
      (unexpected['entry'] as List<dynamic>).add({
        'fullUrl': '$issuer/Practitioner/1',
        'resource': {
          'resourceType': 'Practitioner',
          'id': '1',
          'subject': {'reference': 'Patient/1'},
        },
      });
      expect(() => parse(unexpected), throwsFormatException);
    },
  );
  test(
    'scoped sections accept only Patient plus appropriate resource types',
    () {
      final raw = bundle();
      raw['id'] = 'patient-1-conditions';
      final entries = raw['entry'] as List<dynamic>;
      entries.removeWhere(
        (e) => !{
          'Patient',
          'Condition',
        }.contains((e as Map)['resource']['resourceType']),
      );
      expect(parse(raw, section: 'conditions').rows.length, 2);
    },
  );
  test(
    'uses exact GET path and bearer with no query or external transmission',
    () async {
      final calls = <String>[];
      final h = Harness(
        MockClient((r) async {
          calls.add(r.url.path);
          expect(r.method, 'GET');
          expect(r.headers['accept'], 'application/fhir+json');
          expect(r.headers['authorization'], 'Bearer bound-token');
          expect(r.url.hasQuery, isFalse);
          return receipt(bundle());
        }),
      );
      expect((await h.source.exportPatient(patientId: 1)).rows.length, 12);
      expect(calls, ['/api/v1/hie/fhir/export/patient/1']);
    },
  );
  test('denies all non-doctors, foreign clinic and unbound tenant before transport', () async {
    final h = Harness(
      MockClient((_) async => throw StateError('Unexpected transport')),
    );
    for (final role in [
      'admin',
      'super_admin',
      'nurse',
      'pharmacist',
      'patient',
      'lab_technician',
    ]) {
      h.state = actor(role: role);
      await expectLater(
        h.source.exportPatient(patientId: 1),
        throwsA(isA<HieUnavailable>()),
      );
    }
    h.state = actor(organization: null);
    await expectLater(
      h.source.exportPatient(patientId: 1),
      throwsA(isA<HieUnavailable>()),
    );
    h.state = actor(clinic: 2);
    await expectLater(
      h.source.exportPatient(patientId: 1, expectedClinicId: 1),
      throwsA(isA<HieUnavailable>()),
    );
  });
  test(
    'late response after tenant change is rejected even with same token',
    () async {
      final entered = Completer<void>();
      final reply = Completer<http.Response>();
      final h = Harness(
        MockClient((_) async {
          entered.complete();
          return reply.future;
        }),
      );
      final pending = h.source.exportPatient(patientId: 1);
      final check = expectLater(pending, throwsA(isA<HieUnavailable>()));
      await entered.future;
      h.state = actor(organization: '123e4567-e89b-42d3-a456-426614174009');
      reply.complete(receipt(bundle()));
      await check;
    },
  );
  testWidgets('preview is explicit, role-scoped and cleared on invalidation', (
    tester,
  ) async {
    var calls = 0;
    final h = Harness(
      MockClient((_) async {
        calls++;
        return receipt(bundle());
      }),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: FhirExportPanel(source: h.source, patientId: 1, clinicId: 1),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(calls, 0);
    final button = find.byKey(const ValueKey('fhir-preview-request'));
    await tester.ensureVisible(button);
    await tester.tap(button);
    await tester.pumpAndSettle();
    expect(calls, 1);
    expect(find.byKey(const ValueKey('fhir-preview-count')), findsOneWidget);
    h.guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('fhir-preview-count')), findsNothing);
    expect(find.byKey(const ValueKey('fhir-export-panel')), findsNothing);
    h.guard.markSessionAvailable();
    await tester.pumpAndSettle();
    expect(calls, 1);
    expect(find.byKey(const ValueKey('fhir-export-panel')), findsNothing);
  });
  testWidgets('Admin cannot see the Doctor export preview', (tester) async {
    final h = Harness(
      MockClient((_) async => throw StateError('Unexpected request')),
      state: actor(role: 'admin'),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: FhirExportPanel(source: h.source)),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('fhir-export-panel')), findsNothing);
  });
}

const syntheticBundleJson =
    r'''{"entry":[{"fullUrl":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/Patient/1","resource":{"active":true,"gender":"unknown","id":"1","identifier":[{"system":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/identifier/patient","value":"1"},{"system":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/identifier/patient-number","value":"MRN-1"}],"name":[{"family":"Doe","given":["Jane"]}],"resourceType":"Patient"}},{"fullUrl":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/Encounter/1","resource":{"class":{"code":"IMP","system":"http://terminology.hl7.org/CodeSystem/v3-ActCode"},"id":"1","period":{"start":"2026-11-25T19:34:56.518923+00:00"},"resourceType":"Encounter","status":"in-progress","subject":{"reference":"Patient/1"}}},{"fullUrl":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/Observation/1-8310-5","resource":{"category":[{"coding":[{"code":"vital-signs","system":"http://terminology.hl7.org/CodeSystem/observation-category"}]}],"code":{"coding":[{"code":"8310-5","display":"Body temperature","system":"http://loinc.org"}]},"effectiveDateTime":"2026-11-25T19:34:56.477892+00:00","id":"1-8310-5","resourceType":"Observation","status":"final","subject":{"reference":"Patient/1"},"valueQuantity":{"code":"Cel","system":"http://unitsofmeasure.org","unit":"°C","value":37.1}}},{"fullUrl":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/Observation/1-8867-4","resource":{"category":[{"coding":[{"code":"vital-signs","system":"http://terminology.hl7.org/CodeSystem/observation-category"}]}],"code":{"coding":[{"code":"8867-4","display":"Heart rate","system":"http://loinc.org"}]},"effectiveDateTime":"2026-11-25T19:34:56.477892+00:00","id":"1-8867-4","resourceType":"Observation","status":"final","subject":{"reference":"Patient/1"},"valueQuantity":{"code":"/min","system":"http://unitsofmeasure.org","unit":"beats/min","value":80.0}}},{"fullUrl":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/Observation/1-29463-7","resource":{"category":[{"coding":[{"code":"vital-signs","system":"http://terminology.hl7.org/CodeSystem/observation-category"}]}],"code":{"coding":[{"code":"29463-7","display":"Body weight","system":"http://loinc.org"}]},"effectiveDateTime":"2026-11-25T19:34:56.477892+00:00","id":"1-29463-7","resourceType":"Observation","status":"final","subject":{"reference":"Patient/1"},"valueQuantity":{"code":"kg","system":"http://unitsofmeasure.org","unit":"kg","value":70.0}}},{"fullUrl":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/Observation/1-8302-2","resource":{"category":[{"coding":[{"code":"vital-signs","system":"http://terminology.hl7.org/CodeSystem/observation-category"}]}],"code":{"coding":[{"code":"8302-2","display":"Body height","system":"http://loinc.org"}]},"effectiveDateTime":"2026-11-25T19:34:56.477892+00:00","id":"1-8302-2","resourceType":"Observation","status":"final","subject":{"reference":"Patient/1"},"valueQuantity":{"code":"cm","system":"http://unitsofmeasure.org","unit":"cm","value":175.0}}},{"fullUrl":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/Observation/1-85354-9","resource":{"category":[{"coding":[{"code":"vital-signs","system":"http://terminology.hl7.org/CodeSystem/observation-category"}]}],"code":{"coding":[{"code":"85354-9","display":"Blood pressure panel","system":"http://loinc.org"}]},"component":[{"code":{"coding":[{"code":"8480-6","display":"Systolic blood pressure","system":"http://loinc.org"}]},"valueQuantity":{"code":"mm[Hg]","system":"http://unitsofmeasure.org","unit":"mmHg","value":120.0}},{"code":{"coding":[{"code":"8462-4","display":"Diastolic blood pressure","system":"http://loinc.org"}]},"valueQuantity":{"code":"mm[Hg]","system":"http://unitsofmeasure.org","unit":"mmHg","value":80.0}}],"effectiveDateTime":"2026-11-25T19:34:56.477892+00:00","id":"1-85354-9","resourceType":"Observation","status":"final","subject":{"reference":"Patient/1"}}},{"fullUrl":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/AllergyIntolerance/1","resource":{"category":["medication"],"clinicalStatus":{"coding":[{"code":"active","system":"http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical"}]},"code":{"text":"Synthetic allergen"},"id":"1","patient":{"reference":"Patient/1"},"reaction":[{"manifestation":[{"text":"Recorded rash"}],"severity":"moderate"}],"recordedDate":"2026-11-25T19:34:56.511876+00:00","resourceType":"AllergyIntolerance","verificationStatus":{"coding":[{"code":"confirmed","system":"http://terminology.hl7.org/CodeSystem/allergyintolerance-verification"}]}}},{"fullUrl":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/MedicationRequest/1","resource":{"authoredOn":"2026-11-25T19:34:56.494134+00:00","dosageInstruction":[{"text":"5 mg; Daily"}],"id":"1","intent":"order","medicationCodeableConcept":{"text":"Test Drug 1"},"resourceType":"MedicationRequest","status":"active","subject":{"reference":"Patient/1"}}},{"fullUrl":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/Condition/1","resource":{"category":[{"coding":[{"code":"problem-list-item","system":"http://terminology.hl7.org/CodeSystem/condition-category"}]}],"clinicalStatus":{"coding":[{"code":"active","system":"http://terminology.hl7.org/CodeSystem/condition-clinical"}]},"code":{"coding":[{"code":"I10","system":"http://hl7.org/fhir/sid/icd-10"}],"text":"Synthetic hypertension"},"id":"1","meta":{"versionId":"1"},"recordedDate":"2026-11-25T19:34:56.654137+00:00","resourceType":"Condition","subject":{"reference":"Patient/1"},"verificationStatus":{"coding":[{"code":"confirmed","system":"http://terminology.hl7.org/CodeSystem/condition-ver-status"}]}}},{"fullUrl":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/DiagnosticReport/lab-1","resource":{"category":[{"coding":[{"code":"LAB","system":"http://terminology.hl7.org/CodeSystem/v2-0074"}]}],"code":{"text":"Laboratory report"},"id":"lab-1","issued":"2026-11-25T19:34:57.049272+00:00","meta":{"versionId":"1"},"resourceType":"DiagnosticReport","result":[{"reference":"Observation/lab-1-1"}],"status":"final","subject":{"reference":"Patient/1"}}},{"fullUrl":"https://synthetic.example.invalid/fhir/b11c9d07-7411-4526-a42a-e0d9bda625d5/1/Observation/lab-1-1","resource":{"category":[{"coding":[{"code":"laboratory","system":"http://terminology.hl7.org/CodeSystem/observation-category"}]}],"code":{"coding":[{"code":"2345-7","system":"http://loinc.org"}],"text":"Synthetic test"},"id":"lab-1-1","interpretation":[{"coding":[{"code":"N","system":"http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"}]}],"issued":"2026-11-25T19:34:57.049272+00:00","meta":{"versionId":"1"},"referenceRange":[{"text":"1 - 10"}],"resourceType":"Observation","status":"final","subject":{"reference":"Patient/1"},"valueString":"5 mg/dL"}}],"id":"patient-1-all","resourceType":"Bundle","timestamp":"2026-11-25T19:34:57.272553+00:00","type":"collection"}''';
