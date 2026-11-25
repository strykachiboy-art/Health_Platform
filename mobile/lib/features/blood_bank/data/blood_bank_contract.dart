// BB02: pinned BB01 contracts. The server is the only clinical authority.
import 'package:clinic_system_pro/features/clinical_orders/data/models/blood_product_order.dart';

typedef BbJson = Map<String, dynamic>;
const bbStockRoles = {'admin', 'doctor', 'nurse', 'lab_technician'};
const bbLabRoles = {'lab_technician'};
const bbIntakeRoles = {'admin', 'nurse', 'lab_technician'};
const bbClinicalRoles = {'doctor', 'nurse'};
const bbAdminLabRoles = {'admin', 'lab_technician'};
const bbDoctorRoles = {'doctor'};
const bbPatientRoles = {'patient'};
const bbRoles = {...bbStockRoles, ...bbPatientRoles};
const bbProductTypes = [
  'packed_red_blood_cells',
  'whole_blood',
  'platelets',
  'fresh_frozen_plasma',
  'cryoprecipitate',
];
const bbBloodTypes = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'];
const bbUnitStatuses = [
  'quarantined',
  'available',
  'reserved',
  'issued',
  'administration_started',
  'administration_stopped',
  'returned_quarantine',
  'recalled',
  'transfused',
  'discarded',
  'expired',
];
const bbTransfusionStatuses = [
  'in_progress',
  'completed',
  'stopped_reaction',
  'stopped_other',
];
const bbScreenResults = ['non_reactive', 'reactive', 'indeterminate'];

Never bbInvalid() =>
    throw const FormatException('Unverified Blood Bank response.');
int bbId(Object? value) =>
    value is int && value > 0 && value <= 2147483647 ? value : bbInvalid();
int bbCount(Object? value) =>
    value is int && value >= 0 && value <= 2147483647 ? value : bbInvalid();
BbJson bbMap(Object? value) => value is BbJson ? value : bbInvalid();
String bbText(Object? value, {int max = 5000}) =>
    value is String && value.trim().isNotEmpty && value.length <= max
    ? value
    : bbInvalid();
String bbChoice(Object? value, Iterable<String> allowed) =>
    value is String && allowed.contains(value) ? value : bbInvalid();
bool bbBool(Object? value) => value is bool ? value : bbInvalid();
DateTime bbTime(Object? value) {
  final s = bbText(value, max: 60);
  // BB01 uses both naive UTC database timestamps and explicit UTC offsets.
  final re = RegExp(
    r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)?$',
  );
  if (!re.hasMatch(s)) return bbInvalid();
  final explicit = s.endsWith('Z') || RegExp(r'[+-]\d\d:\d\d$').hasMatch(s);
  return DateTime.tryParse(explicit ? s : '${s}Z')?.toUtc() ?? bbInvalid();
}

List<BbJson> bbRows(Object? value, {int max = 10000}) {
  if (value is! List || value.length > max) return bbInvalid();
  return List<BbJson>.unmodifiable(value.map(bbMap));
}

// No unrestricted URL, verb, or offline queue is exposed by this catalogue.
enum BbAction {
  stock('Stock summary', 'Inventory', bbStockRoles, 'stock-summary'),
  inventory('Physical components', 'Inventory', bbAdminLabRoles, 'units'),
  unit('Component detail', 'Inventory', bbAdminLabRoles, 'units/:id'),
  suppliers('Approved suppliers', 'Inventory', bbAdminLabRoles, 'suppliers'),
  createSupplier(
    'Register supplier',
    'Inventory',
    bbAdminLabRoles,
    'suppliers',
    write: true,
  ),
  deactivateSupplier(
    'Deactivate supplier',
    'Inventory',
    bbAdminLabRoles,
    'suppliers/:id/deactivate',
    write: true,
  ),
  receive(
    'Receive external component (quarantine)',
    'Inventory',
    bbAdminLabRoles,
    'units/receive',
    write: true,
  ),
  discard(
    'Discard component',
    'Inventory',
    bbLabRoles,
    'units/:id/discard',
    write: true,
  ),
  returnUnit(
    'Return issued component for inspection',
    'Inventory',
    bbLabRoles,
    'units/:id/return',
    write: true,
  ),
  expire(
    'Record expired inventory',
    'Inventory',
    bbLabRoles,
    'units/expire',
    write: true,
  ),
  threshold(
    'Set stock threshold',
    'Inventory',
    {'admin'},
    'stock-thresholds',
    write: true,
  ),
  evaluate(
    'Evaluate shortage alerts',
    'Inventory',
    bbAdminLabRoles,
    'stock-alerts/evaluate',
    write: true,
  ),
  report('Operational report', 'Inventory', bbAdminLabRoles, 'reports/summary'),
  donors('Donor registry', 'Donors', bbIntakeRoles, 'donors'),
  registerDonor(
    'Register donor',
    'Donors',
    bbIntakeRoles,
    'donors',
    write: true,
  ),
  deferral(
    'Record donor eligibility / deferral',
    'Donors',
    {'doctor', 'nurse', 'lab_technician'},
    'donors/:id/deferral',
    write: true,
  ),
  donations('Donation records', 'Donors', {
    'nurse',
    'lab_technician',
  }, 'donations'),
  donate(
    'Record assessed donation',
    'Donors',
    {'nurse', 'lab_technician'},
    'donations',
    write: true,
  ),
  matches(
    'Consent-controlled shortage matches',
    'Donors',
    bbAdminLabRoles,
    'shortages/:id/donor-matches',
  ),
  screenProgress(
    'Verified screening progress',
    'Laboratory',
    bbLabRoles,
    'donations/:id/screening-progress',
  ),
  artifacts(
    'Laboratory evidence',
    'Laboratory',
    bbLabRoles,
    'lab-artifacts',
    paged: true,
  ),
  artifact(
    'Laboratory evidence detail',
    'Laboratory',
    bbLabRoles,
    'lab-artifacts/:id',
  ),
  recordArtifact(
    'Record laboratory artifact',
    'Laboratory',
    bbLabRoles,
    'lab-artifacts',
    write: true,
  ),
  verifyArtifact(
    'Independently verify laboratory artifact',
    'Laboratory',
    bbLabRoles,
    'lab-artifacts/:id/verify',
    write: true,
  ),
  screen(
    'Record donation screening',
    'Laboratory',
    bbLabRoles,
    'donations/:id/screen',
    write: true,
  ),
  release(
    'Release quarantined component',
    'Laboratory',
    bbLabRoles,
    'units/:id/release',
    write: true,
  ),
  orders(
    'Admission blood orders',
    'Orders',
    bbStockRoles,
    'blood-products',
    clinical: true,
  ),
  order(
    'Blood order detail',
    'Orders',
    bbStockRoles,
    'blood-products/:id',
    clinical: true,
  ),
  createOrder(
    'Create blood product order',
    'Orders',
    bbDoctorRoles,
    'blood-products',
    clinical: true,
    write: true,
  ),
  compatibility(
    'Record order compatibility / emergency verification',
    'Orders',
    bbLabRoles,
    'blood-products/:id/verify-compatibility',
    clinical: true,
    write: true,
  ),
  reserve(
    'Reserve physical components',
    'Orders',
    bbLabRoles,
    'orders/:id/reserve',
    write: true,
  ),
  unreserve(
    'Release reservations',
    'Orders',
    bbLabRoles,
    'orders/:id/release-reservations',
    write: true,
  ),
  issue(
    'Issue exact reserved component set',
    'Orders',
    bbLabRoles,
    'blood-products/:id/fulfill',
    clinical: true,
    write: true,
  ),
  cancel(
    'Cancel unissued order',
    'Orders',
    bbDoctorRoles,
    'blood-products/:id/cancel',
    clinical: true,
    write: true,
  ),
  closePartial(
    'Close partially issued order',
    'Orders',
    bbDoctorRoles,
    'orders/:id/close-partial',
    write: true,
  ),
  review(
    'Independent emergency release review',
    'Orders',
    bbDoctorRoles,
    'blood-products/:id/emergency-review',
    clinical: true,
    write: true,
  ),
  followup(
    'Record post-release laboratory follow-up',
    'Orders',
    bbLabRoles,
    'blood-products/:id/units/:secondary/lab-followup',
    clinical: true,
    write: true,
  ),
  verifyBarcode(
    'Verify wristband and component',
    'Bedside',
    bbClinicalRoles,
    'barcode/verify',
    write: true,
    dedicated: true,
  ),
  start(
    'Start verified transfusion',
    'Bedside',
    bbClinicalRoles,
    'transfusions',
    write: true,
    dedicated: true,
  ),
  history(
    'Patient transfusion history',
    'Bedside',
    bbClinicalRoles,
    'patients/:id/transfusions',
    paged: true,
  ),
  transfusion(
    'Transfusion detail',
    'Bedside',
    bbClinicalRoles,
    'transfusions/:id',
  ),
  complete(
    'Complete transfusion',
    'Bedside',
    bbClinicalRoles,
    'transfusions/:id/complete',
    write: true,
  ),
  stop(
    'Document stopped transfusion',
    'Bedside',
    bbClinicalRoles,
    'transfusions/:id/stop',
    write: true,
  ),
  reaction(
    'Document suspected transfusion reaction',
    'Bedside',
    bbClinicalRoles,
    'transfusions/:id/reactions',
    write: true,
  ),
  patientRelease(
    'Release reviewed history to patient',
    'Bedside',
    bbDoctorRoles,
    'transfusions/:id/patient-release',
    write: true,
  ),
  recalls('Recall records', 'Safety', bbLabRoles, 'recalls'),
  recall(
    'Recall donation / supplier components',
    'Safety',
    bbLabRoles,
    'recalls',
    write: true,
  ),
  investigations(
    'Investigation evidence',
    'Safety',
    bbLabRoles,
    'investigations',
    paged: true,
  ),
  investigation(
    'Record investigation event',
    'Safety',
    {'doctor', 'lab_technician'},
    'investigations',
    write: true,
  ),
  inbox(
    'My blood safety inbox',
    'Safety',
    bbStockRoles,
    'safety-inbox',
    paged: true,
  ),
  acknowledge(
    'Acknowledge safety alert',
    'Safety',
    bbStockRoles,
    'safety-inbox/:id/acknowledge',
    write: true,
  ),
  reconcileAlerts(
    'Reconcile recall safety alerts',
    'Safety',
    bbLabRoles,
    'safety-alerts/reconcile',
    write: true,
  ),
  offline(
    'Online-only operation policy',
    'Safety',
    bbStockRoles,
    'offline-policy',
  ),
  myHistory(
    'My released transfusion history',
    'My care',
    bbPatientRoles,
    'my/transfusions',
  );

  const BbAction(
    this.label,
    this.section,
    this.roles,
    this.template, {
    this.clinical = false,
    this.write = false,
    this.paged = false,
    this.dedicated = false,
  });
  final String label, section, template;
  final Set<String> roles;
  final bool clinical, write, paged, dedicated;
  bool get needsId => template.contains(':id');
  bool get needsSecondary => template.contains(':secondary');
  String path({int? id, int? secondary, int? admission, int? after}) {
    if (needsId != (id != null) || needsSecondary != (secondary != null)) {
      throw ArgumentError('Exact target required.');
    }
    if (id != null) bbId(id);
    if (secondary != null) bbId(secondary);
    if ((this == orders) != (admission != null) || (after != null && !paged)) {
      throw ArgumentError('Invalid filter.');
    }
    if (admission != null) bbId(admission);
    if (after != null) bbCount(after);
    final part = template
        .replaceAll(':id', '$id')
        .replaceAll(':secondary', '$secondary');
    return Uri(
      path: 'api/v1/${clinical ? 'clinical-orders' : 'blood-bank'}/$part',
      queryParameters: admission != null
          ? {'admission_id': '$admission'}
          : paged
          ? {'limit': '50', if (after != null) 'after_id': '$after'}
          : null,
    ).toString();
  }
}

class BbUnit {
  BbUnit.fromJson(BbJson j, int clinic)
    : id = bbId(j['id']),
      version = bbId(j['version']),
      barcode = bbText(j['unit_barcode'], max: 128),
      product = bbChoice(j['product_type'], bbProductTypes),
      group = bbChoice(j['blood_type'], bbBloodTypes),
      status = bbChoice(j['status'], bbUnitStatuses),
      recalled = bbBool(j['recall_blocked']),
      screening = bbChoice(j['tti_screening_status'], [
        'pending',
        'cleared',
        'reactive',
        'indeterminate',
      ]),
      expires = bbTime(j['expires_at']),
      reservedOrder = j['reserved_for_order_id'] == null
          ? null
          : bbId(j['reserved_for_order_id']) {
    if (bbId(j['clinic_id']) != clinic) bbInvalid();
    bbText(j['din'], max: 64);
    bbText(j['product_identifier'], max: 80);
    bbText(j['division_identifier'], max: 40);
    if (status == 'reserved' && reservedOrder == null) bbInvalid();
  }
  final int id, version;
  final int? reservedOrder;
  final String barcode, product, group, status, screening;
  final bool recalled;
  final DateTime expires;
  bool get currentlyUsable =>
      !recalled &&
      screening == 'cleared' &&
      expires.isAfter(DateTime.now().toUtc());
}

class BbTransfusion {
  BbTransfusion.fromJson(
    BbJson j,
    int clinic, {
    int? expectedId,
    int? expectedPatient,
    int? expectedOrder,
    int? expectedUnit,
    int? expectedAdmission,
  }) : id = bbId(j['id']),
       patient = bbId(j['patient_id']),
       admission = bbId(j['admission_id']),
       order = bbId(j['blood_product_order_id']),
       unit = bbId(j['blood_unit_id']),
       status = bbChoice(j['status'], bbTransfusionStatuses),
       version = bbId(j['version']) {
    if (bbId(j['clinic_id']) != clinic ||
        (expectedId != null && id != expectedId) ||
        (expectedPatient != null && patient != expectedPatient) ||
        (expectedOrder != null && order != expectedOrder) ||
        (expectedUnit != null && unit != expectedUnit) ||
        (expectedAdmission != null && admission != expectedAdmission)) {
      bbInvalid();
    }
    final started = bbTime(j['started_at']);
    final ended = j['ended_at'] == null ? null : bbTime(j['ended_at']);
    if ((status == 'in_progress') != (ended == null) ||
        (ended != null && ended.isBefore(started))) {
      bbInvalid();
    }
    bbBool(j['reaction_reported']);
  }
  final int id, patient, admission, order, unit, version;
  final String status;
}

// Typed response gates for lifecycle-bearing rows, with explicit route scope.
void bbScope(Object? object, int clinic) {
  if (object is Map<String, dynamic>) {
    if (object.containsKey('clinic_id') && bbId(object['clinic_id']) != clinic) {
      bbInvalid();
    }
    for (final value in object.values) {
      bbScope(value, clinic);
    }
  } else if (object is List) {
    for (final value in object) {
      bbScope(value, clinic);
    }
  }
}

BbJson bbOrderJson(
  BbJson j,
  int clinic, {
  int? id,
  int? patient,
  int? admission,
}) {
  final order = j.containsKey('order') ? bbMap(j['order']) : j;
  BloodProductOrder.fromJson(
    order,
    expectedClinicId: clinic,
    expectedId: id,
    expectedPatientId: patient,
    expectedAdmissionId: admission,
  );
  return order;
}

BbJson bbValidate(
  BbAction a,
  BbJson json,
  int clinic, {
  int? id,
  int? admission,
  int? patient,
}) {
  bbScope(json, clinic);
  if (a == BbAction.myHistory) {
    if (json['success'] != true) bbInvalid();
    final data = bbMap(json['data']);
    final rows = bbRows(data['items'], max: 100);
    final safe = <BbJson>[];
    for (final r in rows) {
      safe.add({
        'id': bbId(r['id']),
        'admission_id': bbId(r['admission_id']),
        'order_id': bbId(r['order_id']),
        'started_at': bbTime(r['started_at']).toIso8601String(),
        'ended_at': bbTime(r['ended_at']).toIso8601String(),
        'status': bbChoice(
          r['status'],
          bbTransfusionStatuses.where((s) => s != 'in_progress'),
        ),
        'reaction_reported': bbBool(r['reaction_reported']),
      });
    }
    return {'items': safe};
  }
  if (a == BbAction.order ||
      (a.clinical && a.write) ||
      a == BbAction.closePartial) {
    final order = bbOrderJson(
      json,
      clinic,
      id: id,
      patient: patient,
      admission: admission,
    );
    if (a == BbAction.createOrder && order['inventory_required'] != true) {
      bbInvalid();
    }
    return order;
  }
  if (a == BbAction.orders) {
    final rows = bbRows(json['items']);
    if (bbCount(json['total']) != rows.length) bbInvalid();
    for (final r in rows) {
      bbOrderJson(r, clinic, patient: patient, admission: admission);
    }
  }
  if (a == BbAction.inventory ||
      a == BbAction.reserve ||
      a == BbAction.unreserve ||
      a == BbAction.expire) {
    final rows = bbRows(json['items']);
    if (bbCount(json['total']) != rows.length) bbInvalid();
    final seen = <int>{};
    for (final r in rows) {
      if (!seen.add(BbUnit.fromJson(r, clinic).id)) bbInvalid();
    }
  }
  if ({
    BbAction.unit,
    BbAction.receive,
    BbAction.release,
    BbAction.returnUnit,
    BbAction.discard,
  }.contains(a)) {
    final row = BbUnit.fromJson(json, clinic);
    if (id != null && row.id != id) bbInvalid();
    if (a == BbAction.receive && row.status != 'quarantined') bbInvalid();
    if (a == BbAction.release && row.status != 'available') bbInvalid();
    if (a == BbAction.returnUnit && row.status != 'returned_quarantine') {
      bbInvalid();
    }
    if (a == BbAction.discard && row.status != 'discarded') bbInvalid();
  }
  if ({
    BbAction.transfusion,
    BbAction.complete,
    BbAction.stop,
    BbAction.start,
    BbAction.reaction,
  }.contains(a)) {
    final row = BbTransfusion.fromJson(
      a == BbAction.reaction ? bbMap(json['transfusion']) : json,
      clinic,
      expectedId: id,
      expectedPatient: patient,
    );
    if (a == BbAction.complete && row.status != 'completed') bbInvalid();
    if (a == BbAction.stop && row.status != 'stopped_other') bbInvalid();
    if (a == BbAction.start && row.status != 'in_progress') bbInvalid();
  }
  if (a == BbAction.history) {
    if (patient != null && id != patient) bbInvalid();
    for (final r in bbRows(json['items'], max: 50)) {
      BbTransfusion.fromJson(r, clinic, expectedPatient: id);
    }
  }
  if (a.paged) {
    if (json['next_after_id'] != null) bbId(json['next_after_id']);
    bbRows(json['items'], max: 50);
  }
  if (a == BbAction.offline) {
    if (json['policy_version'] != 'bb01-r02-deny-by-default' ||
        json['automatic_replay_authorizations'] != 0) {
      bbInvalid();
    }
    final items = bbRows(json['items']);
    if (items.isEmpty) bbInvalid();
    for (final r in items) {
      if (r['automatic_replay'] != false ||
          r['server_no_effect_disposition'] != false ||
          r['disposition'] !=
              'online_authoritative_manual_reconcile_unknown_outcome') {
        bbInvalid();
      }
    }
  }
  if (a == BbAction.matches) {
    if (bbId(json['blood_product_order_id']) != id) bbInvalid();
    bbRows(json['eligible_donors']);
  }
  if (a == BbAction.screenProgress) {
    if (bbId(json['donation_id']) != id || json['release_authorized'] != false) {
      bbInvalid();
    }
    bbRows(json['items']);
  }
  if (a == BbAction.stock || a == BbAction.evaluate) {
    bbRows(json['items']);
    bbCount(json['total_available']);
    bbCount(json['total_quarantined']);
  }
  if ({
    BbAction.donors,
    BbAction.donations,
    BbAction.suppliers,
    BbAction.recalls,
  }.contains(a)) {
    for (final r in bbRows(json['items'])) {
      bbId(r['id']);
    }
  }
  if ({
    BbAction.registerDonor,
    BbAction.deferral,
    BbAction.createSupplier,
    BbAction.deactivateSupplier,
    BbAction.recordArtifact,
    BbAction.verifyArtifact,
    BbAction.artifact,
    BbAction.investigation,
    BbAction.acknowledge,
    BbAction.patientRelease,
    BbAction.threshold,
  }.contains(a)) {
    final actual = bbId(json['id']);
    if (id != null && a != BbAction.patientRelease && actual != id) bbInvalid();
    if (a == BbAction.patientRelease &&
        (bbId(json['transfusion_id']) != id ||
            (patient != null && bbId(json['patient_id']) != patient))) {
      bbInvalid();
    }
  }
  if (a == BbAction.donate || a == BbAction.screen) {
    final d = bbMap(json['donation']);
    if (id != null && bbId(d['id']) != id) bbInvalid();
    for (final r in bbRows(json['units'])) {
      BbUnit.fromJson(r, clinic);
    }
  }
  if (json.isEmpty) bbInvalid();
  return json;
}
