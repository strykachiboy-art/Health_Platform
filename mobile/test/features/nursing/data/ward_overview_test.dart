import 'package:flutter_test/flutter_test.dart';
import 'package:clinic_system_pro/features/nursing/data/models/ward_overview.dart';

Map<String, dynamic> ward({int clinic = 3, String type = 'icu'}) => {
  'id': 9,
  'clinic_id': clinic,
  'name': 'Intensive Care',
  'ward_type': type,
  'capacity': 5,
};

Map<String, dynamic> bed({int wardId = 9, String status = 'reserved'}) => {
  'id': 44,
  'ward_id': wardId,
  'bed_number': 'ICU-04',
  'status': status,
};

Map<String, dynamic> occupancy({int clinic = 3, int wardId = 9}) => {
  'ward_id': wardId,
  'clinic_id': clinic,
  'ward_name': 'Intensive Care',
  'capacity': 5,
  'total_beds': 4,
  'available': 1,
  'occupied': 1,
  'reserved': 1,
  'maintenance': 1,
};

void main() {
  test('ward and bed enums are closed, names scoped, no fake default', () {
    final parsedWard = WardRecord.fromJson(ward(), expectedClinicId: 3);
    expect(parsedWard.kind, WardKind.icu);
    expect(parsedWard.capacity, 5);
    final parsedBed = WardBedRecord.fromJson(bed(), expectedWardId: 9);
    expect(parsedBed.status, WardBedStatus.reserved);
    expect(
      () => WardRecord.fromJson(ward(clinic: 8), expectedClinicId: 3),
      throwsFormatException,
    );
    expect(
      () => WardRecord.fromJson(ward(type: 'unknown'), expectedClinicId: 3),
      throwsFormatException,
    );
    expect(
      () => WardBedRecord.fromJson(bed(wardId: 8), expectedWardId: 9),
      throwsFormatException,
    );
    expect(
      () => WardBedRecord.fromJson(bed(status: 'unknown'), expectedWardId: 9),
      throwsFormatException,
    );
  });

  test('occupancy requires clinic, ward and consistent counts', () {
    final parsed = WardOccupancy.fromJson(
      occupancy(),
      expectedWardId: 9,
      expectedClinicId: 3,
    );
    expect(parsed.available, 1);
    expect(parsed.reserved, 1);
    expect(
      () => WardOccupancy.fromJson(
        occupancy(clinic: 2),
        expectedWardId: 9,
        expectedClinicId: 3,
      ),
      throwsFormatException,
    );
    expect(
      () => WardOccupancy.fromJson(
        occupancy(wardId: 2),
        expectedWardId: 9,
        expectedClinicId: 3,
      ),
      throwsFormatException,
    );
    final broken = occupancy()..['occupied'] = 9;
    expect(
      () => WardOccupancy.fromJson(
        broken,
        expectedWardId: 9,
        expectedClinicId: 3,
      ),
      throwsFormatException,
    );
  });

  test('pagination requires raw items, current page and correct scope', () {
    final data = {
      'items': [ward()],
      'total': 1,
      'page': 1,
      'per_page': 20,
    };
    final page = WardPage<WardRecord>.fromJson(
      data,
      requestedPage: 1,
      requestedPerPage: 20,
      parseRow: (row) => WardRecord.fromJson(row, expectedClinicId: 3),
    );
    expect(page.items.single.id, 9);
    expect(page.hasNext, isFalse);
    expect(
      () => WardPage<WardRecord>.fromJson(
        {...data, 'page': 2},
        requestedPage: 1,
        requestedPerPage: 20,
        parseRow: (row) => WardRecord.fromJson(row, expectedClinicId: 3),
      ),
      throwsFormatException,
    );
    expect(
      () => WardPage<WardRecord>.fromJson(
        {'success': true, 'data': data},
        requestedPage: 1,
        requestedPerPage: 20,
        parseRow: (row) => WardRecord.fromJson(row, expectedClinicId: 3),
      ),
      throwsFormatException,
    );
  });

  test('empty page is valid, status and identifier fail closed', () {
    final page = WardPage<WardRecord>.fromJson(
      {'items': <Object>[], 'total': 0, 'page': 1, 'per_page': 20},
      requestedPage: 1,
      requestedPerPage: 20,
      parseRow: (row) => WardRecord.fromJson(row, expectedClinicId: 3),
    );
    expect(page.items, isEmpty);
    expect(
      () => WardRecord.fromJson({...ward(), 'id': 0}, expectedClinicId: 3),
      throwsFormatException,
    );
    expect(
      () => WardBedRecord.fromJson({
        ...bed(),
        'bed_number': ' ',
      }, expectedWardId: 9),
      throwsFormatException,
    );
  });
}
