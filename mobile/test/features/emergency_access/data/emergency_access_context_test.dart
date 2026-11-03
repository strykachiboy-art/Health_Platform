import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/emergency_access/data/emergency_access_context.dart';
import 'package:clinic_system_pro/features/emergency_access/data/models/emergency_access_grant.dart';

EmergencyAccessGrant _grant({
  String status = 'active',
  String? expiresAt = '2026-11-03T13:00:00+00:00',
  int patientId = 7,
}) {
  return EmergencyAccessGrant.fromJson(<String, dynamic>{
    'id': 17,
    'clinic_id': 3,
    'patient_id': patientId,
    'requester_user_id': 42,
    'requester_role': 'DOCTOR',
    'reason': 'Emergency treatment',
    'purpose': 'Immediate clinical review',
    'scope': <String>['patient:read'],
    'status': status,
    'requested_at': '2026-11-03T12:00:00+00:00',
    'granted_at': status == 'active' ? '2026-11-03T12:05:00+00:00' : null,
    'expires_at': expiresAt,
    'revoked_at': null,
    'reviewed_at': status == 'requested' ? null : '2026-11-03T12:05:00+00:00',
    'reviewed_by_user_id': status == 'requested' ? null : 99,
    'review_notes': status == 'requested' ? null : 'Reviewed',
  });
}

void main() {
  test('active unexpired grant is available only for its patient', () {
    final context = EmergencyAccessContext(
      nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
    );
    addTearDown(context.dispose);

    context.activate(_grant());

    expect(context.activeEmergencyAccessId, 17);
    expect(context.activePatientId, 7);
    expect(context.emergencyAccessIdForPatient(7), 17);
    expect(context.emergencyAccessIdForPatient(8), isNull);
  });

  test('requested and expired grants fail closed', () {
    final context = EmergencyAccessContext(
      nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
    );
    addTearDown(context.dispose);

    expect(
      () => context.activate(_grant(status: 'requested', expiresAt: null)),
      throwsArgumentError,
    );

    expect(
      () => context.activate(_grant(expiresAt: '2026-11-03T12:29:59+00:00')),
      throwsArgumentError,
    );

    expect(context.activeEmergencyAccessId, isNull);
  });

  test('reconciliation clears revoked or expired active context', () {
    final context = EmergencyAccessContext(
      nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
    );
    addTearDown(context.dispose);

    context.activate(_grant());
    expect(context.activeEmergencyAccessId, 17);

    context.reconcile(
      _grant(status: 'revoked', expiresAt: '2026-11-03T13:00:00+00:00'),
    );

    expect(context.activeEmergencyAccessId, isNull);
  });

  test('session invalidation clears active emergency access', () {
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/patients', '/emergency-access'],
    );

    guard.markSessionAvailable();

    final context = EmergencyAccessContext(
      sessionNavigationGuard: guard,
      nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
    );

    context.activate(_grant());
    expect(context.activeEmergencyAccessId, 17);

    guard.invalidate();

    expect(context.activeEmergencyAccessId, isNull);

    context.dispose();
    guard.dispose();
  });

  test('activation fails when the bound session guard is not available', () {
    final guard = SessionNavigationGuard(
      protectedRoutePrefixes: const <String>['/patients', '/emergency-access'],
    );

    final context = EmergencyAccessContext(
      sessionNavigationGuard: guard,
      nowUtc: () => DateTime.utc(2026, 11, 3, 12, 30),
    );

    expect(() => context.activate(_grant()), throwsStateError);

    context.dispose();
    guard.dispose();
  });
}
