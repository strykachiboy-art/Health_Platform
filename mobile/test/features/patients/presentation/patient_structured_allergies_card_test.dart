import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patients/data/patient_allergy_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_structured_allergies_card.dart';

AuthSessionState _session({String role = 'doctor'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 'allergy-ui-test-token',
    refreshToken: 'allergy-ui-refresh',
    userId: 42,
    role: role,
    clinicContextId: 3,
  ),
);

Map<String, dynamic> _row({
  int id = 11,
  int patientId = 7,
  String name = 'Penicillin',
  String severity = 'life_threatening',
  String clinicalStatus = 'active',
  String verification = 'confirmed',
}) => {
  'id': id,
  'clinic_id': 3,
  'patient_id': patientId,
  'allergen_name': name,
  'allergen_code': null,
  'allergen_type': 'drug',
  'reaction': 'Anaphylaxis',
  'severity': severity,
  'clinical_status': clinicalStatus,
  'verification_status': verification,
  'onset_at': null,
  'recorded_at': '2026-11-06T09:00:00+00:00',
  'recorded_by_id': 12,
  'notes': null,
  'created_at': '2026-11-06T09:00:00+00:00',
  'updated_at': '2026-11-06T09:00:00+00:00',
};

Map<String, dynamic> _page({
  List<Map<String, dynamic>>? items,
  int? total,
  int page = 1,
  int perPage = 20,
}) {
  final rows = items ?? [_row()];
  return {
    'success': true,
    'data': {
      'items': rows,
      'total': total ?? rows.length,
      'page': page,
      'per_page': perPage,
    },
  };
}

PatientAllergyRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => PatientAllergyRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _session(role: role),
  sessionNavigationGuard: guard,
);

Widget _host(
  PatientAllergyRemoteDataSource source, {
  int patientId = 7,
  int pageSize = 20,
}) => MaterialApp(
  home: Scaffold(
    body: SingleChildScrollView(
      child: PatientStructuredAllergiesCard(
        patientId: patientId,
        dataSource: source,
        pageSize: pageSize,
      ),
    ),
  ),
);

void main() {
  testWidgets('renders verified severity, reaction and status', (tester) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v1/patients/7/allergies');
      expect(request.url.queryParameters, {
        'page': '1',
        'per_page': '20',
        'active_only': 'false',
        'include_refuted': 'true',
      });
      expect(request.headers['authorization'], 'Bearer allergy-ui-test-token');
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(requests, 1);
    expect(find.text('Penicillin'), findsOneWidget);
    expect(find.textContaining('Life-threatening'), findsOneWidget);
    expect(find.textContaining('Confirmed'), findsOneWidget);
    expect(find.textContaining('Anaphylaxis'), findsOneWidget);
  });

  testWidgets('refuted records are visibly distinguished', (tester) async {
    final client = MockClient(
      (_) async => http.Response(
        jsonEncode(
          _page(
            items: [
              _row(
                name: 'Latex',
                severity: 'mild',
                clinicalStatus: 'inactive',
                verification: 'refuted',
              ),
            ],
          ),
        ),
        200,
      ),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Latex'), findsOneWidget);
    expect(find.textContaining('Refuted'), findsOneWidget);
    expect(find.textContaining('Inactive'), findsOneWidget);
  });

  testWidgets('empty registry does not claim no known allergies', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page(items: [], total: 0)), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-allergies-empty')),
      findsOneWidget,
    );
    expect(find.textContaining('does not confirm'), findsOneWidget);
  });

  testWidgets('403 denies display without exposing allergy records', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => http.Response('{"error":"Forbidden"}', 403),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-allergies-error')),
      findsOneWidget,
    );
    expect(find.text('Penicillin'), findsNothing);
  });

  testWidgets('unauthorized local role makes no network request', (
    tester,
  ) async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard, role: 'patient')));
    await tester.pumpAndSettle();

    expect(requests, 0);
    expect(find.text('Penicillin'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-allergies-error')),
      findsOneWidget,
    );
  });

  testWidgets('logout clears previously displayed clinical records', (
    tester,
  ) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page()), 200),
    );
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Penicillin'), findsOneWidget);

    guard.invalidate();
    await tester.pump();

    expect(find.text('Penicillin'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-allergies-error')),
      findsOneWidget,
    );
  });

  testWidgets('switching patient rejects an older pending response', (
    tester,
  ) async {
    final oldPending = Completer<http.Response>();
    final oldStarted = Completer<void>();

    final client = MockClient((request) {
      if (request.url.path == '/api/v1/patients/7/allergies') {
        oldStarted.complete();
        return oldPending.future;
      }
      expect(request.url.path, '/api/v1/patients/8/allergies');
      return Future.value(
        http.Response(
          jsonEncode(_page(items: [_row(id: 21, patientId: 8, name: 'Latex')])),
          200,
        ),
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);

    await tester.pumpWidget(_host(source));
    await oldStarted.future;

    await tester.pumpWidget(_host(source, patientId: 8));
    await tester.pumpAndSettle();

    expect(find.text('Latex'), findsOneWidget);
    expect(find.text('Penicillin'), findsNothing);

    oldPending.complete(http.Response(jsonEncode(_page()), 200));
    await tester.pumpAndSettle();

    expect(find.text('Latex'), findsOneWidget);
    expect(find.text('Penicillin'), findsNothing);
  });

  testWidgets('pagination requests the next page explicitly', (tester) async {
    var requests = 0;
    final client = MockClient((request) async {
      requests++;
      final page = int.parse(request.url.queryParameters['page']!);
      expect(request.url.queryParameters['per_page'], '1');

      final row = page == 1 ? _row() : _row(id: 12, name: 'Latex');

      return http.Response(
        jsonEncode(_page(items: [row], total: 2, page: page, perPage: 1)),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), pageSize: 1));
    await tester.pumpAndSettle();

    expect(find.text('Penicillin'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('patient-allergies-load-more')),
      findsOneWidget,
    );

    await tester.tap(find.byKey(const ValueKey('patient-allergies-load-more')));
    await tester.pumpAndSettle();

    expect(requests, 2);
    expect(find.text('Penicillin'), findsOneWidget);
    expect(find.text('Latex'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('patient-allergies-load-more')),
      findsNothing,
    );
  });

  testWidgets('failed refresh clears old allergy results', (tester) async {
    var requests = 0;

    final client = MockClient((_) async {
      requests++;
      if (requests == 2) {
        return http.Response('{"error":"Unavailable"}', 503);
      }
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(find.text('Penicillin'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('patient-allergies-refresh')));
    await tester.pumpAndSettle();

    expect(requests, 2);
    expect(find.text('Penicillin'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-allergies-error')),
      findsOneWidget,
    );
  });
}
