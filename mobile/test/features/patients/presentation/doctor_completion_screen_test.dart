import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/patients/presentation/doctor_observation_screen.dart';
import '../data/doctor_completion_contract_test.dart' show fixture, response;

void main() {
  testWidgets('Doctor observations disappear on session invalidation', (tester) async {
    final guard = SessionNavigationGuard()..markSessionAvailable();
    final client = MockClient((request) async => response({'success': true, 'data': {'id': 11, 'clinic_id': 7, 'is_active': true, 'first_name': 'Synthetic', 'last_name': 'Patient'}}));
    final source = fixture(guard, client);
    final review = await source.inspect(11);
    await tester.pumpWidget(MaterialApp(home: DoctorObservationScreen(source: source, review: review)));
    await tester.pumpAndSettle();
    expect(find.text('Synthetic Patient'), findsOneWidget);
    expect(find.text('Patient observations'), findsOneWidget);
    guard.invalidate();
    await tester.pumpAndSettle();
    expect(find.text('Synthetic Patient'), findsNothing);
    expect(find.text('Patient observation session unavailable.'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    guard.dispose();
    client.close();
  });
}
