import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/patient_portal/data/patient_portal_remote_data_source.dart';
import 'package:clinic_system_pro/features/patient_portal/presentation/patient_portal_screen.dart';

class FakePatientPortal implements PatientPortalDataSource {
  FakePatientPortal({this.clinicians = const [], this.appointments = const []});
  final List<Map<String, dynamic>> clinicians;
  final List<Map<String, dynamic>> appointments;
  int bookings = 0;
  int cancellations = 0;

  @override
  Future<PatientPortalView> load() async => PatientPortalView(
    name: 'Synthetic Patient',
    clinicians: clinicians,
    appointments: appointments,
    prescriptions: const [],
    invoices: const [],
  );

  @override
  Future<void> book({
    required int staffId,
    required DateTime startUtc,
    required String appointmentType,
    required String idempotencyKey,
  }) async {
    bookings++;
  }

  @override
  Future<void> cancel(int appointmentId) async {
    cancellations++;
  }
}

void main() {
  testWidgets(
    'S12 patient-owned dashboard renders real empty state, not fabricated cards',
    (tester) async {
      final source = FakePatientPortal();
      await tester.pumpWidget(
        MaterialApp(home: PatientPortalScreen(source: source)),
      );
      await tester.pumpAndSettle();
      expect(find.textContaining('Welcome, Synthetic Patient'), findsOneWidget);
      expect(find.text('My appointments'), findsOneWidget);
      await tester.scrollUntilVisible(
        find.text('No available clinician is listed for your clinic.'),
        140,
      );
      expect(
        find.text('No available clinician is listed for your clinic.'),
        findsOneWidget,
      );
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('s12-book')),
        140,
      );
      expect(find.byKey(const ValueKey('s12-book')), findsOneWidget);
      expect(
        tester
            .widget<FilledButton>(find.byKey(const ValueKey('s12-book')))
            .onPressed,
        isNull,
      );
      expect(source.bookings, 0);
      await tester.scrollUntilVisible(find.text('My prescriptions'), 140);
      expect(find.text('My prescriptions'), findsOneWidget);
      await tester.scrollUntilVisible(find.text('My invoices'), 140);
      expect(find.text('My invoices'), findsOneWidget);
    },
  );

  testWidgets(
    'S12 patient appointment shows server-derived status and no staff notes',
    (tester) async {
      final source = FakePatientPortal(
        appointments: const [
          {
            'id': 21,
            'status': 'confirmed',
            'scheduled_start': '2026-12-01T10:00:00',
            'appointment_type': 'in_person',
          },
        ],
      );
      await tester.pumpWidget(
        MaterialApp(home: PatientPortalScreen(source: source)),
      );
      await tester.pumpAndSettle();
      expect(find.textContaining('Appointment #21'), findsOneWidget);
      expect(find.text('Cancel request'), findsNothing);
      expect(source.cancellations, 0);
    },
  );
}
