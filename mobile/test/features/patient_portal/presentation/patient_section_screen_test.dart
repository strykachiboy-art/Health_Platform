import 'dart:async';

import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/patient_portal/data/patient_portal_remote_data_source.dart';
import 'package:clinic_system_pro/features/patient_portal/presentation/patient_section_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeSource implements PatientPortalDataSource {
  _FakeSource(this.getView);
  final Future<PatientPortalView> Function() getView;
  int readCalls = 0;

  @override
  Future<PatientPortalView> load() {
    readCalls++;
    return getView();
  }

  @override
  Future<void> book({
    required int staffId,
    required DateTime startUtc,
    required String appointmentType,
    required String idempotencyKey,
  }) => throw StateError('S15 must not initiate clinical writes');

  @override
  Future<void> cancel(int appointmentId) =>
      throw StateError('S15 must not initiate clinical writes');
}

final _view = PatientPortalView(
  name: 'Synthetic Patient',
  clinicians: const [],
  appointments: const [
    <String, dynamic>{
      'id': 22,
      'status': 'scheduled',
      'scheduled_start': '2026-12-03T10:00:00Z',
      'appointment_type': 'in_person',
    },
  ],
  prescriptions: const [
    <String, dynamic>{'id': 13, 'status': 'active', 'issued_at': '2026-11-21T10:00:00Z'},
  ],
  invoices: const [
    <String, dynamic>{
      'invoice_number': 'TEST-ONLY-100',
      'status': 'issued',
      'total_amount': '100.50',
      'amount_paid': '0.00',
      'currency': 'NGN',
      'due_date': '2026-12-01',
    },
  ],
);

AuthSessionState _session(String role) => AuthSessionState.ready(
      AuthTokenResponse(
        accessToken: 'synthetic-token',
        refreshToken: 'synthetic-refresh',
        userId: 22,
        role: role,
        clinicContextId: 3,
      ),
    );

void main() {
  for (final section in PatientWorkspaceSection.values) {
    test('S15 patient section parsing accepts only known sections: ${section.name}', () {
      expect(PatientWorkspaceSection.fromRoute(section.name), section);
    });
  }
  test('S15 invalid section never resolves to patient data route', () {
    expect(PatientWorkspaceSection.fromRoute('staff'), isNull);
    expect(PatientWorkspaceSection.fromRoute('../billing'), isNull);
  });

  for (final section in PatientWorkspaceSection.values) {
    testWidgets('S15 ${section.name} uses verified patient-owned source only', (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _FakeSource(() async => _view);
      await tester.pumpWidget(MaterialApp(
        home: PatientSectionScreen(
          section: section,
          source: source,
          loadSession: () async => _session('patient'),
          sessionNavigationGuard: guard,
        ),
      ));
      await tester.pumpAndSettle();
      expect(source.readCalls, 1);
      expect(find.byKey(const ValueKey('s15-patient-section-content')), findsOneWidget);
      expect(find.byKey(const ValueKey('s15-open-full-my-care')), findsOneWidget);
      switch (section) {
        case PatientWorkspaceSection.appointments:
          expect(find.text('Appointment #22'), findsOneWidget);
          expect(find.text('TEST-ONLY-100'), findsNothing);
          break;
        case PatientWorkspaceSection.prescriptions:
          expect(find.text('Prescription #13'), findsOneWidget);
          expect(find.text('Appointment #22'), findsNothing);
          break;
        case PatientWorkspaceSection.billing:
          expect(find.text('TEST-ONLY-100'), findsOneWidget);
          expect(find.textContaining('Online payment is not enabled'), findsOneWidget);
          break;
        case PatientWorkspaceSection.messages:
          expect(find.textContaining('existing My care screen contains'), findsOneWidget);
          expect(find.text('Appointment #22'), findsNothing);
          break;
      }
    });
  }

  testWidgets('S15 doctor cannot fetch patient-owned records', (tester) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _FakeSource(() async => _view);
    await tester.pumpWidget(MaterialApp(
      home: PatientSectionScreen(
        section: PatientWorkspaceSection.appointments,
        source: source,
        loadSession: () async => _session('doctor'),
        sessionNavigationGuard: guard,
      ),
    ));
    await tester.pumpAndSettle();
    expect(source.readCalls, 0);
    expect(find.byKey(const ValueKey('s15-patient-section-unavailable')), findsOneWidget);
    expect(find.text('Appointment #22'), findsNothing);
  });

  testWidgets('S15 precise enrollment code produces safe actionable message', (tester) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _FakeSource(() async => throw ApiException(
          kind: ApiFailureKind.httpStatus,
          statusCode: 409,
          serverCode: 'PATIENT_ENROLLMENT_REQUIRED',
          message: 'DO_NOT_DISPLAY_BACKEND_DETAIL',
          uri: Uri.parse('https://example.invalid/api/v1/patient-portal/me'),
        ));
    await tester.pumpWidget(MaterialApp(
      home: PatientSectionScreen(
        section: PatientWorkspaceSection.prescriptions,
        source: source,
        loadSession: () async => _session('patient'),
        sessionNavigationGuard: guard,
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.textContaining('clinical enrollment is pending'), findsOneWidget);
    expect(find.textContaining('DO_NOT_DISPLAY_BACKEND_DETAIL'), findsNothing);
  });

  testWidgets('S15 invalidation prevents stale async records after sign-out', (tester) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final pending = Completer<PatientPortalView>();
    final source = _FakeSource(() => pending.future);
    await tester.pumpWidget(MaterialApp(
      home: PatientSectionScreen(
        section: PatientWorkspaceSection.appointments,
        source: source,
        loadSession: () async => _session('patient'),
        sessionNavigationGuard: guard,
      ),
    ));
    await tester.pump();
    guard.invalidate();
    pending.complete(_view);
    await tester.pumpAndSettle();
    expect(find.text('Appointment #22'), findsNothing);
    expect(find.byKey(const ValueKey('s15-patient-section-unavailable')), findsOneWidget);
    expect(find.byKey(const ValueKey('s15-open-full-my-care')), findsNothing);
  });

  for (final section in <PatientWorkspaceSection>[
    PatientWorkspaceSection.billing,
    PatientWorkspaceSection.prescriptions,
  ]) {
    testWidgets('S16 empty ${section.name} does not offer nonexistent detail actions', (tester) async {
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _FakeSource(() async => const PatientPortalView(
        name: 'Empty Patient',
        clinicians: [],
        appointments: [],
        prescriptions: [],
        invoices: [],
      ));
      await tester.pumpWidget(MaterialApp(
        home: PatientSectionScreen(
          section: section,
          source: source,
          loadSession: () async => _session('patient'),
          sessionNavigationGuard: guard,
        ),
      ));
      await tester.pumpAndSettle();
      expect(source.readCalls, 1);
      expect(find.byKey(const ValueKey('s15-patient-section-content')), findsOneWidget);
      expect(find.byKey(const ValueKey('s15-open-full-my-care')), findsNothing);
      expect(find.text('Records for Empty Patient. Only backend-authorized patient information is displayed.'), findsOneWidget);
      expect(find.textContaining('No invoices are recorded for this patient.'),
          section == PatientWorkspaceSection.billing ? findsOneWidget : findsNothing);
    });
  }

  testWidgets('S16 appointments retain booking route even with no records', (tester) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _FakeSource(() async => const PatientPortalView(
      name: 'Empty Patient',
      clinicians: [],
      appointments: [],
      prescriptions: [],
      invoices: [],
    ));
    await tester.pumpWidget(MaterialApp(
      home: PatientSectionScreen(
        section: PatientWorkspaceSection.appointments,
        source: source,
        loadSession: () async => _session('patient'),
        sessionNavigationGuard: guard,
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('s15-open-full-my-care')), findsOneWidget);
    expect(find.text('Book or manage in My care'), findsOneWidget);
  });
}
