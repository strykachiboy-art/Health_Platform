import 'package:flutter_test/flutter_test.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';

void main() {
  group('SessionNavigationGuard route policy', () {
    test(
      'default policy protects patient route family with segment boundaries',
      () {
        final guard = SessionNavigationGuard();
        addTearDown(guard.dispose);

        expect(guard.redirect('/patients'), '/login');
        expect(guard.redirect('/patients/register'), '/login');
        expect(guard.redirect('/patients/7'), '/login');

        expect(
          guard.redirect('/patientssomething'),
          isNull,
          reason: 'A protected prefix must match a complete route segment.',
        );
        expect(guard.redirect('/health'), isNull);

        guard.markSessionAvailable();

        expect(guard.redirect('/patients'), isNull);
        expect(guard.redirect('/patients/7'), isNull);
      },
    );

    test('custom policy protects multiple route families without changing public routes', () {
      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const <String>[
          '/patients',
          '/emergency-access',
        ],
      );
      addTearDown(guard.dispose);

      expect(guard.redirect('/patients'), '/login');
      expect(guard.redirect('/emergency-access'), '/login');
      expect(guard.redirect('/emergency-access/requests/42'), '/login');

      expect(guard.redirect('/health'), isNull);
      expect(guard.redirect('/login'), isNull);
      expect(guard.redirect('/emergency-accessory'), isNull);

      guard.markSessionAvailable();

      expect(guard.redirect('/patients/7'), isNull);
      expect(guard.redirect('/emergency-access/requests/42'), isNull);

      guard.invalidate();

      expect(guard.redirect('/patients/7'), '/login');
      expect(guard.redirect('/emergency-access/requests/42'), '/login');
      expect(guard.requiresLocalRecovery, isTrue);
    });

    test('rejects unsafe protected route prefix configuration', () {
      expect(
        () => SessionNavigationGuard(protectedRoutePrefixes: const <String>[]),
        throwsArgumentError,
      );

      for (final prefix in const <String>[
        'patients',
        '/',
        '/patients/',
        ' /patients',
      ]) {
        expect(
          () =>
              SessionNavigationGuard(protectedRoutePrefixes: <String>[prefix]),
          throwsArgumentError,
          reason: 'Rejected prefix: $prefix',
        );
      }
    });

    test('deduplicates equivalent configured prefixes', () {
      final guard = SessionNavigationGuard(
        protectedRoutePrefixes: const <String>['/patients', '/patients'],
      );
      addTearDown(guard.dispose);

      expect(guard.redirect('/patients/7'), '/login');

      guard.markSessionAvailable();

      expect(guard.redirect('/patients/7'), isNull);
    });
  });
}
