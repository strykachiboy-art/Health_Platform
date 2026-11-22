import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/patient_portal/data/patient_portal_remote_data_source.dart';
import 'package:clinic_system_pro/features/patient_portal/presentation/patient_portal_screen.dart';
import 'package:clinic_system_pro/features/patient_portal/presentation/patient_portal_service_screens.dart';

class _FakeServices
    implements PatientPortalDataSource, PatientPortalServiceActions {
  int checkoutCalls = 0;
  int messageCalls = 0;
  @override
  Future<PatientPortalView> load() async => const PatientPortalView(
    name: 'Synthetic Patient',
    clinicians: [],
    appointments: [],
    prescriptions: [
      {'id': 12, 'status': 'active', 'issued_at': '2026-11-21T11:00:00Z'},
    ],
    invoices: [
      {
        'id': 34,
        'invoice_number': 'S13-INV',
        'status': 'issued',
        'currency': 'NGN',
        'total_amount': '200.00',
        'amount_paid': '0.00',
      },
    ],
  );
  @override
  Future<Map<String, dynamic>> prescriptionDetail(int id) async => {
    'id': id,
    'status': 'active',
    'issued_at': '2026-11-21T11:00:00Z',
    'expires_at': null,
    'items': [
      {
        'id': 1,
        'medicine': 'Synthetic tablet',
        'strength': '50 mg',
        'dosage': 'One tablet',
        'frequency': 'once daily',
        'duration': '7 days',
        'route': 'oral',
        'quantity': 7,
        'instructions': 'After food',
      },
    ],
  };
  @override
  Future<Map<String, dynamic>> invoiceDetail(int id) async => {
    'id': id,
    'invoice_number': 'S13-INV',
    'status': 'issued',
    'currency': 'NGN',
    'total_amount': '200.00',
    'amount_paid': '0.00',
    'outstanding_balance': '200.00',
    'sandbox_checkout_available': false,
    'items': [
      {
        'description': 'Synthetic consultation',
        'quantity': 1,
        'unit_price': '200.00',
        'subtotal': '200.00',
      },
    ],
    'payments': [],
  };
  @override
  Future<Map<String, dynamic>> checkout(
    int invoiceId,
    String idempotencyKey,
  ) async {
    checkoutCalls++;
    return {'outcome': 'pending', 'checkout_url': null, 'payment_id': 1};
  }

  @override
  Future<List<Map<String, dynamic>>> careConversations() async => [
    {'id': 77, 'title': 'Approved care thread'},
  ];
  @override
  Future<List<Map<String, dynamic>>> careMessages(int conversationId) async => [
    {'id': 1, 'content': 'Welcome to your care team', 'sender_is_me': false},
  ];
  @override
  Future<void> sendCareMessage(int conversationId, String content) async {
    messageCalls++;
  }

  @override
  Future<void> book({
    required int staffId,
    required DateTime startUtc,
    required String appointmentType,
    required String idempotencyKey,
  }) async {}
  @override
  Future<void> cancel(int appointmentId) async {}
}

void main() {
  testWidgets('S13 prescription row opens owned clinical item details', (
    tester,
  ) async {
    final source = _FakeServices();
    await tester.pumpWidget(
      MaterialApp(home: PatientPortalScreen(source: source)),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Prescription #12'),
      160,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('Prescription #12'));
    await tester.pumpAndSettle();
    expect(find.text('My prescription details'), findsOneWidget);
    expect(find.text('Synthetic tablet'), findsOneWidget);
    expect(find.text('Instructions: After food'), findsOneWidget);
  });

  testWidgets('S13 invoice row shows lines and disallows unavailable payment', (
    tester,
  ) async {
    final source = _FakeServices();
    await tester.pumpWidget(
      MaterialApp(home: PatientPortalScreen(source: source)),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('S13-INV'),
      180,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.ensureVisible(find.text('S13-INV'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('S13-INV'));
    await tester.pumpAndSettle();
    expect(find.text('My invoice details'), findsOneWidget);
    expect(find.text('Synthetic consultation'), findsOneWidget);
    expect(find.textContaining('Outstanding: 200.00'), findsOneWidget);
    expect(find.text('Paystack TEST checkout'), findsNothing);
    expect(source.checkoutCalls, 0);
  });

  testWidgets('S13 Patient care messages link opens approved thread only', (
    tester,
  ) async {
    final source = _FakeServices();
    await tester.pumpWidget(
      MaterialApp(home: PatientPortalScreen(source: source)),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('s13-care-messages')),
      150,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.byKey(const ValueKey('s13-care-messages')));
    await tester.pumpAndSettle();
    expect(find.text('My care messages'), findsOneWidget);
    await tester.tap(find.text('Approved care thread'));
    await tester.pumpAndSettle();
    expect(find.text('Welcome to your care team'), findsOneWidget);
    expect(source.messageCalls, 0);
  });

  testWidgets('S13 checkout-disabled invoice never creates a payment intent', (
    tester,
  ) async {
    final source = _FakeServices();
    await tester.pumpWidget(
      MaterialApp(
        home: PatientInvoiceDetailPage(actions: source, recordId: 34),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('My invoice details'), findsOneWidget);
    expect(
      find.textContaining('Online payment is unavailable'),
      findsOneWidget,
    );
    expect(source.checkoutCalls, 0);
  });
}
