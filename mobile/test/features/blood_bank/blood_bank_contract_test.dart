import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/blood_bank/data/blood_bank_contract.dart';
import 'package:clinic_system_pro/features/blood_bank/data/blood_bank_forms.dart';
import 'package:clinic_system_pro/features/clinical_orders/data/models/blood_product_order.dart';
import 'package:clinic_system_pro/features/clinical_timeline/data/timeline_source.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';

BbJson unitRow({
  int clinic = 3,
  String status = 'available',
  bool recall = false,
}) => {
  'id': 11,
  'clinic_id': clinic,
  'version': 4,
  'unit_barcode': 'DIN-1',
  'product_type': 'packed_red_blood_cells',
  'blood_type': 'O+',
  'status': status,
  'recall_blocked': recall,
  'tti_screening_status': 'cleared',
  'expires_at': '2099-01-01T00:00:00Z',
  'reserved_for_order_id': status == 'reserved' ? 19 : null,
  'din': 'D1',
  'product_identifier': 'P1',
  'division_identifier': '01',
};
BbJson orderRow({int clinic = 3, int issued = 1, bool inventory = true}) => {
  'id': 19,
  'clinic_id': clinic,
  'patient_id': 7,
  'admission_id': 9,
  'ordered_by_staff_id': 8,
  'product_type': 'packed_red_blood_cells',
  'units_requested': 2,
  'units_issued': issued,
  'indication': 'Documented clinical order',
  'priority': 'stat',
  'status': issued == 0
      ? 'active'
      : issued == 1
      ? 'in_progress'
      : 'completed',
  'version': 3,
  'patient_blood_type_at_order': 'O+',
  'compatibility_status': 'compatible',
  'compatibility_verified_by_staff_id': 11,
  'compatibility_verified_at': '2026-11-25T09:00:00Z',
  'patient_blood_type_verified': 'O+',
  'crossmatch_reference': 'C1',
  'emergency_release_reason': null,
  'product_blood_type': 'O+',
  'ordered_at': '2026-11-25T08:00:00Z',
  'fulfilled_by_staff_id': issued > 0 ? 11 : null,
  'cancelled_by_staff_id': null,
  'completed_at': issued == 2 ? '2026-11-25T09:30:00Z' : null,
  'cancelled_at': null,
  'inventory_required': inventory,
  'emergency_release_requested': false,
  'issued_units': [
    for (var i = 0; i < issued; i++)
      {
        'id': 21 + i,
        'clinic_id': clinic,
        'patient_id': 7,
        'admission_id': 9,
        'order_id': 19,
        'blood_unit_id': 11 + i,
        'compatibility_evidence_id': 30 + i,
        'unit_identifier': 'DIN-${i + 1}',
        'product_blood_type': 'O+',
        'issued_by_staff_id': 11,
        'issued_at': '2026-11-25T09:30:00Z',
      },
  ],
  'lab_followups': [],
};
BbJson transfusionRow({int clinic = 3, String status = 'in_progress'}) => {
  'id': 51,
  'clinic_id': clinic,
  'patient_id': 7,
  'admission_id': 9,
  'blood_product_order_id': 19,
  'blood_unit_id': 11,
  'version': 1,
  'status': status,
  'started_at': '2026-11-25T09:30:00Z',
  'ended_at': status == 'in_progress' ? null : '2026-11-25T10:00:00Z',
  'reaction_reported': false,
  'barcode_verification_event_id': 42,
};
BbJson policyRow() => {
  'policy_version': 'bb01-r02-deny-by-default',
  'automatic_replay_authorizations': 0,
  'items': [
    {
      'operation_type': 'blood_bank.start',
      'method': 'POST',
      'path': '/api/v1/blood-bank/transfusions',
      'disposition': 'online_authoritative_manual_reconcile_unknown_outcome',
      'automatic_replay': false,
      'server_no_effect_disposition': false,
    },
  ],
};
void main() {
  test('fixed catalogue routes and scope', () {
    expect(
      BbAction.reserve.path(id: 19),
      'api/v1/blood-bank/orders/19/reserve',
    );
    expect(
      BbAction.followup.path(id: 19, secondary: 21),
      'api/v1/clinical-orders/blood-products/19/units/21/lab-followup',
    );
    expect(() => BbAction.order.path(), throwsArgumentError);
    expect(() => BbAction.orders.path(), throwsArgumentError);
  });
  test('role split and no platform clinical grant', () {
    expect(BbAction.start.roles, {'doctor', 'nurse'});
    expect(BbAction.release.roles, {'lab_technician'});
    expect(BbAction.myHistory.roles, {'patient'});
    expect(bbRoles, isNot(contains('super_admin')));
  });
  test('cross clinic component denied', () {
    expect(() => BbUnit.fromJson(unitRow(clinic: 4), 3), throwsFormatException);
  });
  test('recalled component is unusable', () {
    expect(
      BbUnit.fromJson(
        unitRow(recall: true, status: 'recalled'),
        3,
      ).currentlyUsable,
      isFalse,
    );
  });
  test('uncleared component is unusable', () {
    expect(
      BbUnit.fromJson(
        unitRow()..['tti_screening_status'] = 'pending',
        3,
      ).currentlyUsable,
      isFalse,
    );
  });
  test('physical partial order accepted', () {
    final o = BloodProductOrder.fromJson(orderRow(), expectedClinicId: 3);
    expect(o.inventoryRequired, isTrue);
    expect(o.unitsIssued, 1);
    expect(o.status, 'in_progress');
  });
  test('missing physical issue evidence denied', () {
    expect(
      () => bbOrderJson(orderRow()..['issued_units'] = [], 3),
      throwsFormatException,
    );
  });
  test('foreign physical issue evidence denied', () {
    final j = orderRow();
    bbRows(j['issued_units']).single['patient_id'] = 999;
    expect(() => bbOrderJson(j, 3), throwsFormatException);
  });
  test('duplicate physical component denied', () {
    final j = orderRow(issued: 2);
    bbRows(j['issued_units']).last['blood_unit_id'] = 11;
    expect(() => bbOrderJson(j, 3), throwsFormatException);
  });
  test('legacy partial order denied', () {
    expect(
      () => bbOrderJson(orderRow(inventory: false), 3),
      throwsFormatException,
    );
  });
  test('transfusion start is not completed', () {
    expect(BbTransfusion.fromJson(transfusionRow(), 3).status, 'in_progress');
    expect(
      () => BbTransfusion.fromJson(
        transfusionRow()..['ended_at'] = '2026-11-25T10:00:00Z',
        3,
      ),
      throwsFormatException,
    );
  });
  test('wrong transfusion patient denied', () {
    expect(
      () => BbTransfusion.fromJson(transfusionRow(), 3, expectedPatient: 8),
      throwsFormatException,
    );
  });
  test('empty intake consent not silently inferred', () {
    expect(
      () => bbParse(const BbField('consent', kind: 'bool'), null),
      throwsFormatException,
    );
    expect(bbParse(const BbField('consent', kind: 'bool'), false), false);
  });
  test('incomplete screening panel denied', () {
    expect(
      () => bbParse(bbPanel, [
        {
          'marker': 'hiv',
          'result': 'non_reactive',
          'evidence_reference': 'ref',
        },
      ]),
      throwsFormatException,
    );
  });
  test('complete marker references retained', () {
    final panel = bbMap(
      bbParse(bbPanel, [
        for (final m in ['hiv', 'hbv', 'hcv', 'syphilis'])
          {
            'marker': m,
            'result': 'non_reactive',
            'evidence_reference': '$m-ref',
          },
      ]),
    );
    expect(panel.keys.toSet(), {'hiv', 'hbv', 'hcv', 'syphilis'});
  });
  test('timezone required on input', () {
    expect(
      () => bbParse(
        const BbField('collected_at', kind: 'time'),
        '2026-11-25T09:00:00',
      ),
      throwsFormatException,
    );
  });
  test('policy cannot silently grant replay', () {
    expect(
      bbValidate(
        BbAction.offline,
        policyRow(),
        3,
      )['automatic_replay_authorizations'],
      0,
    );
    expect(
      () => bbValidate(
        BbAction.offline,
        policyRow()..['automatic_replay_authorizations'] = 1,
        3,
      ),
      throwsFormatException,
    );
  });
  test('patient projection excludes donor and component identifiers', () {
    final result = bbValidate(BbAction.myHistory, {
      'success': true,
      'data': {
        'items': [
          {
            'id': 51,
            'admission_id': 9,
            'order_id': 19,
            'started_at': '2026-11-25T09:30:00Z',
            'ended_at': '2026-11-25T10:00:00Z',
            'status': 'completed',
            'reaction_reported': false,
            'donor_name': 'PRIVATE',
            'blood_unit_id': 11,
          },
        ],
      },
    }, 3);
    expect(bbRows(result['items']).single.containsKey('donor_name'), isFalse);
    expect(
      bbRows(result['items']).single.containsKey('blood_unit_id'),
      isFalse,
    );
  });
  test('documentary timeline distinguishes issue/start/completion', () {
    expect(timelineCategories, contains('blood_transfusion'));
    expect(timelineEvents['blood_component_issued'], (
      'blood_transfusion',
      'recorded',
      'blood_product_order',
    ));
    expect(timelineEvents['blood_transfusion_started'], (
      'blood_transfusion',
      'recorded',
      'blood_transfusion',
    ));
    expect(timelineEvents['blood_transfusion_completed'], (
      'blood_transfusion',
      'completed',
      'blood_transfusion',
    ));
  });
  test('home role integration', () {
    for (final role in [
      'doctor',
      'nurse',
      'lab_technician',
      'admin',
      'patient',
    ]) {
      expect(
        homeDestinationsFor(role: role, clinicContextId: 3).map((d) => d.path),
        contains('/blood-bank'),
      );
    }
    for (final role in ['super_admin', 'driver', 'pharmacist']) {
      expect(
        homeDestinationsFor(role: role, clinicContextId: 3).map((d) => d.path),
        isNot(contains('/blood-bank')),
      );
    }
    expect(
      homeDestinationsFor(
        role: 'doctor',
        clinicContextId: null,
      ).map((d) => d.path),
      isNot(contains('/blood-bank')),
    );
  });
}
