/// P23H.S03B — server-owned pharmacy dispense evidence.
/// A dispense is not a medication administration or stock reservation.
class PharmacyDispenseLine {
  const PharmacyDispenseLine({
    required this.id,
    required this.recordId,
    required this.prescriptionItemId,
    required this.batchId,
    required this.quantityDispensed,
  });

  final int id;
  final int recordId;
  final int prescriptionItemId;
  final int batchId;
  final int quantityDispensed;

  factory PharmacyDispenseLine.fromJson(
    Map<String, dynamic> raw, {
    required int expectedRecordId,
  }) {
    final recordId = dispensePositive(raw, 'dispense_record_id');
    if (recordId != expectedRecordId) {
      throw const FormatException('Dispense line record scope mismatch.');
    }
    return PharmacyDispenseLine(
      id: dispensePositive(raw, 'id'),
      recordId: recordId,
      prescriptionItemId: dispensePositive(raw, 'prescription_item_id'),
      batchId: dispensePositive(raw, 'batch_id'),
      quantityDispensed: dispensePositive(raw, 'quantity_dispensed'),
    );
  }
}

class PharmacyDispenseRecord {
  const PharmacyDispenseRecord({
    required this.id,
    required this.prescriptionId,
    required this.dispensedById,
    required this.status,
    required this.notes,
    required this.lines,
  });

  final int id;
  final int prescriptionId;
  final int dispensedById;
  final String status;
  final String? notes;
  final List<PharmacyDispenseLine> lines;

  factory PharmacyDispenseRecord.fromEnvelope(
    Map<String, dynamic> response, {
    required int expectedPrescriptionId,
    int? expectedRecordId,
  }) {
    if (response['success'] != true ||
        response['data'] is! Map<String, dynamic>) {
      throw const FormatException('Untrusted dispense response envelope.');
    }
    final row = response['data'] as Map<String, dynamic>;
    final id = dispensePositive(row, 'id');
    final prescriptionId = dispensePositive(row, 'prescription_id');
    if (prescriptionId != expectedPrescriptionId ||
        (expectedRecordId != null && expectedRecordId != id)) {
      throw const FormatException('Dispense prescription or record mismatch.');
    }
    final status = row['status'];
    if (status is! String ||
        !const <String>{
          'pending',
          'partially_dispensed',
          'dispensed',
          'cancelled',
        }.contains(status)) {
      throw const FormatException('Unknown dispense status.');
    }
    final rawLines = row['items'];
    if (rawLines is! List || rawLines.isEmpty || rawLines.length > 100) {
      throw const FormatException('Missing or invalid dispense lines.');
    }
    final lines = <PharmacyDispenseLine>[];
    final ids = <int>{};
    for (final raw in rawLines) {
      if (raw is! Map<String, dynamic>) {
        throw const FormatException('Invalid dispense line.');
      }
      final line = PharmacyDispenseLine.fromJson(raw, expectedRecordId: id);
      if (!ids.add(line.id)) {
        throw const FormatException('Repeated dispense line.');
      }
      lines.add(line);
    }
    final notes = row['notes'];
    if (notes != null && (notes is! String || notes.length > 2000)) {
      throw const FormatException('Invalid dispense notes.');
    }
    return PharmacyDispenseRecord(
      id: id,
      prescriptionId: prescriptionId,
      dispensedById: dispensePositive(row, 'dispensed_by_id'),
      status: status,
      notes: notes as String?,
      lines: List<PharmacyDispenseLine>.unmodifiable(lines),
    );
  }

  /// A created record must confirm exactly the requested allocation.
  /// Partial/full status is determined by the backend, never by the client.
  void verifyCreatedLine({
    required int prescriptionItemId,
    required int batchId,
    required int quantity,
  }) {
    if (!const <String>{'partially_dispensed', 'dispensed'}.contains(status) ||
        lines.length != 1 ||
        lines.single.prescriptionItemId != prescriptionItemId ||
        lines.single.batchId != batchId ||
        lines.single.quantityDispensed != quantity) {
      throw const FormatException('Dispense evidence does not match request.');
    }
  }
}

int dispensePositive(Map<String, dynamic> raw, String key) {
  final value = raw[key];
  if (value is! int || value <= 0 || value > 2147483647) {
    throw FormatException('Invalid dispense identity/quantity: $key');
  }
  return value;
}
