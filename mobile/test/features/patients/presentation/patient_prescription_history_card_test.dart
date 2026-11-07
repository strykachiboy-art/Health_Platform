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
import 'package:clinic_system_pro/features/patients/data/patient_prescription_remote_data_source.dart';
import 'package:clinic_system_pro/features/patients/presentation/patient_prescription_history_card.dart';

AuthSessionState _ready({String role = 'doctor'}) => AuthSessionState.ready(
  AuthTokenResponse(
    accessToken: 's04c-token',
    refreshToken: 's04c-refresh',
    userId: 42,
    clinicContextId: 3,
    role: role,
  ),
);

Map<String, dynamic> _row({
  int id = 11,
  int patientId = 7,
  String status = 'active',
}) => {
  'id': id,
  'clinic_id': 3,
  'patient_id': patientId,
  'consultation_id': null,
  'prescribed_by_id': 8,
  'status': status,
  'notes': null,
  'issued_at': '2026-11-06T09:00:00',
  'expires_at': null,
  'items': [
    {
      'id': id + 100,
      'prescription_id': id,
      'drug_id': 15,
      'dosage': '500 mg',
      'frequency': 'twice daily',
      'duration': '5 days',
      'quantity': 10,
      'instructions': 'Use as prescribed',
    },
  ],
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

PatientPrescriptionRemoteDataSource _source(
  MockClient client,
  SessionNavigationGuard guard, {
  String role = 'doctor',
}) => PatientPrescriptionRemoteDataSource(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => _ready(role: role),
  sessionNavigationGuard: guard,
);

Widget _host(
  PatientPrescriptionRemoteDataSource source, {
  int patientId = 7,
  int pageSize = 20,
}) => MaterialApp(
  home: Scaffold(
    body: SingleChildScrollView(
      child: PatientPrescriptionHistoryCard(
        patientId: patientId,
        pageSize: pageSize,
        dataSource: source,
      ),
    ),
  ),
);

void main() {
  testWidgets('GET displays prescribed medication and server status', (
    tester,
  ) async {
    final paths = <String>[];
    final client = MockClient((request) async {
      paths.add(request.url.path);
      expect(request.method, 'GET');
      expect(request.url.queryParameters, {'page': '1', 'per_page': '20'});
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(paths, ['/api/v1/prescriptions/patients/7']);
    expect(find.text('Status: Active'), findsOneWidget);
    expect(find.text('Drug ID: 15'), findsOneWidget);
    expect(find.text('Dosage: 500 mg'), findsOneWidget);
    expect(find.text('Quantity: 10'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('patient-prescription-11')),
      findsOneWidget,
    );
  });

  testWidgets('empty response is not represented as empty medication history', (
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
      find.byKey(const ValueKey('patient-prescriptions-empty')),
      findsOneWidget,
    );
    expect(find.textContaining('not proof'), findsOneWidget);
  });

  testWidgets('pagination loads second page with a real visible tap', (
    tester,
  ) async {
    final pages = <String>[];
    final client = MockClient((request) async {
      final page = request.url.queryParameters['page']!;
      pages.add(page);
      return http.Response(
        jsonEncode(
          _page(
            items: [page == '1' ? _row() : _row(id: 12)],
            total: 2,
            page: int.parse(page),
            perPage: 1,
          ),
        ),
        200,
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard), pageSize: 1));
    await tester.pumpAndSettle();

    final more = find.byKey(const ValueKey('patient-prescriptions-load-more'));
    await tester.ensureVisible(more);
    await tester.pumpAndSettle();
    await tester.tap(more);
    await tester.pumpAndSettle();

    expect(pages, ['1', '2']);
    expect(
      find.byKey(const ValueKey('patient-prescription-11')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('patient-prescription-12')),
      findsOneWidget,
    );
    expect(more, findsNothing);
  });

  testWidgets('unauthorized role issues no prescription GET', (tester) async {
    var requests = 0;
    final client = MockClient((_) async {
      requests++;
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard, role: 'nurse')));
    await tester.pumpAndSettle();

    expect(requests, 0);
    expect(
      find.byKey(const ValueKey('patient-prescriptions-error')),
      findsOneWidget,
    );
  });

  testWidgets('403 is a failure and cannot display records', (tester) async {
    final client = MockClient(
      (_) async => http.Response('{"error":"Forbidden"}', 403),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-prescriptions-error')),
      findsOneWidget,
    );
    expect(find.text('Dosage: 500 mg'), findsNothing);
  });

  testWidgets('logout clears prescription PHI', (tester) async {
    final client = MockClient(
      (_) async => http.Response(jsonEncode(_page()), 200),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();
    expect(find.text('Dosage: 500 mg'), findsOneWidget);

    guard.invalidate();
    await tester.pump();

    expect(find.text('Dosage: 500 mg'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-prescriptions-error')),
      findsOneWidget,
    );
  });

  testWidgets('failed refresh clears previously loaded prescriptions', (
    tester,
  ) async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      if (calls == 2) {
        return http.Response('{"error":"Unavailable"}', 503);
      }
      return http.Response(jsonEncode(_page()), 200);
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);

    await tester.pumpWidget(_host(_source(client, guard)));
    await tester.pumpAndSettle();

    final refresh = find.byKey(const ValueKey('patient-prescriptions-refresh'));
    await tester.ensureVisible(refresh);
    await tester.pumpAndSettle();
    await tester.tap(refresh);
    await tester.pumpAndSettle();

    expect(calls, 2);
    expect(find.text('Dosage: 500 mg'), findsNothing);
    expect(
      find.byKey(const ValueKey('patient-prescriptions-error')),
      findsOneWidget,
    );
  });

  testWidgets('patient switch drops old pending prescription data', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    final client = MockClient((request) {
      if (request.url.path.endsWith('/patients/7')) {
        return pending.future;
      }
      return Future.value(
        http.Response(
          jsonEncode(_page(items: [_row(id: 21, patientId: 8)])),
          200,
        ),
      );
    });
    addTearDown(client.close);

    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);

    await tester.pumpWidget(_host(source));
    await tester.pump();
    await tester.pumpWidget(_host(source, patientId: 8));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-prescription-21')),
      findsOneWidget,
    );

    pending.complete(http.Response(jsonEncode(_page()), 200));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('patient-prescription-21')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('patient-prescription-11')), findsNothing);
  });
}
