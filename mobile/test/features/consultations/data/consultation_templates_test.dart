import 'dart:convert';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/administration/data/wd09cf_operations_remote_data_source.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_remote_data_source.dart';
import 'package:clinic_system_pro/features/consultations/data/consultation_templates.dart';
import 'package:clinic_system_pro/features/home/presentation/wd04_clinical_workflow_hub.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Map<String, dynamic> _row({int? clinic = 7, int id = 12}) => {'id': id, 'clinic_id': clinic, 'name': 'Reusable', 'specialty': null, 'structure': {'sections': ['Assessment']}, 'is_active': true};
void main() {
  test('Doctor template navigation requires selected clinic', () {
    for (final role in ['doctor', 'admin', 'nurse', 'patient', 'super_admin']) {
      final links = wd04ClinicalWorkflowLinks(role: role, clinicContextId: 7);
      expect(links.any((x) => x.path == '/consultations/templates'), role == 'doctor');
    }
    expect(wd04ClinicalWorkflowLinks(role: 'doctor', clinicContextId: null), isEmpty);
  });
  test('Template draft exact keys and rejects invalid object', () {
    expect(ConsultationTemplateSource.draft(' Reusable ', '', '{"sections":["Assessment"]}'), {'name': 'Reusable', 'structure': {'sections': ['Assessment']}});
    for (final j in ['{}', '[]', 'null', 'broken']) {
      expect(() => ConsultationTemplateSource.draft('Reusable', '', j), throwsFormatException);
    }
  });
  for (final mode in ['list', 'foreign', 'duplicate', 'wrong-role', 'stale', 'create', 'wrong-receipt', 'replay']) {
    test('Doctor template contract $mode', () async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        expect(request.headers['authorization'], 'Bearer token');
        expect(request.url.path, '/api/v1/consultations/templates');
        if (request.method == 'GET') {
          expect(request.url.queryParameters, {'page': '1', 'per_page': '20'});
          if (mode == 'stale') guard.invalidate();
          final items = [_row(clinic: mode == 'foreign' ? 99 : 7), _row(clinic: null, id: mode == 'duplicate' ? 12 : 13)];
          return http.Response(jsonEncode({'success': true, 'data': items, 'pagination': {'page': 1, 'per_page': 20, 'total': 2, 'has_next': false}}), 200);
        }
        expect(request.method, 'POST');
        expect(jsonDecode(request.body), {'name': 'Reusable', 'structure': {'sections': ['Assessment']}});
        return http.Response(jsonEncode({'success': true, 'data': _row(clinic: mode == 'wrong-receipt' ? null : 7)}), 201);
      });
      addTearDown(client.close);
      final source = ConsultationTemplateSource(ConsultationRemoteDataSource(apiClient: ApiClient(config: const BackendConfig(baseUrl: 'http://127.0.0.1:5000', allowInsecureHttp: true), client: client), loadSession: () async => AuthSessionState.ready(AuthTokenResponse(accessToken: 'token', refreshToken: 'refresh', userId: 41, role: mode == 'wrong-role' ? 'nurse' : 'doctor', clinicContextId: 7, tenantId: '12345678-1234-1234-1234-123456789abc')), sessionNavigationGuard: guard), Wd09cfOneShotGate());
      Future<ConsultationTemplateRecord> create() => source.create('Reusable', '', '{"sections":["Assessment"]}', expectedRevision: guard.revision);
      switch (mode) {
        case 'list':
          final p = await source.list();
          expect(p.items.length, 2); expect(p.items.last.clinicId, isNull);
        case 'foreign':
        case 'duplicate':
          await expectLater(source.list(), throwsFormatException);
        case 'wrong-role':
        case 'stale':
          await expectLater(source.list(), throwsA(isA<TemplateUnavailable>()));
        case 'create':
          expect((await create()).id, 12);
        case 'wrong-receipt':
          await expectLater(create(), throwsA(isA<TemplateUnconfirmed>()));
        case 'replay':
          await create(); await expectLater(create(), throwsA(isA<TemplateUnconfirmed>()));
      }
      expect(calls, mode == 'wrong-role' ? 0 : 1);
    });
  }
}
