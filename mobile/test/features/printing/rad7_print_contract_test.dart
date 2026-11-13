import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/printing/data/rad7_print_contract.dart';

void main() {
  test('RAD6 download and print remain disabled in RAD7', () {
    expect(Rad7PrintContract.downloadAndPrintApproved, isFalse);
  });
  test('only canonical four backend kinds exist', () {
    expect(Rad7PrintKind.values.map((e) => e.apiKind).toList(), [
      'invoice',
      'appointment',
      'lab_order',
      'radiology_draft',
    ]);
  });
  test('role suggestions cannot override backend authority', () {
    expect(Rad7PrintContract.permitted('accountant'), [Rad7PrintKind.invoice]);
    expect(Rad7PrintContract.permitted('doctor'), [
      Rad7PrintKind.appointment,
      Rad7PrintKind.radiologyDraft,
    ]);
    expect(Rad7PrintContract.permitted('patient'), isEmpty);
    expect(Rad7PrintContract.permitted('super_admin'), isEmpty);
  });
  test('print path carries source identity only', () {
    expect(
      Rad7PrintContract.plannedServerPath(Rad7PrintKind.invoice, 3),
      'api/v1/printing/invoice/3.pdf',
    );
    expect(
      Rad7PrintContract.plannedServerPath(Rad7PrintKind.invoice, -2),
      isNull,
    );
  });
}
