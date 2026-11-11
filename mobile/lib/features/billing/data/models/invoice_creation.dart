/// P23I.S03 — strictly server-owned invoice creation request/confirmation.
/// Money is decimal text: no floating-point financial calculations.
class InvoiceDraftItem {
  const InvoiceDraftItem({
    required this.description,
    required this.quantity,
    required this.unitPrice,
  });

  final String description;
  final int quantity;
  final String unitPrice;

  Map<String, dynamic> toJson() {
    if (description.isEmpty ||
        description != description.trim() ||
        description.length > 255 ||
        quantity <= 0 ||
        quantity > 2147483647) {
      throw const FormatException(
        'Invalid invoice item description or quantity.',
      );
    }
    InvoiceCreationValidation.decimal(unitPrice, allowZero: true);
    return <String, dynamic>{
      'description': description,
      'quantity': quantity,
      'unit_price': unitPrice,
    };
  }
}

class InvoiceDraft {
  const InvoiceDraft({
    required this.patientId,
    required this.currency,
    required this.items,
    this.appointmentId,
    this.dueDate,
    this.isInsuranceClaim = false,
    this.insuranceProvider,
  });

  final int patientId;
  final String currency;
  final List<InvoiceDraftItem> items;
  final int? appointmentId;
  final String? dueDate;
  final bool isInsuranceClaim;
  final String? insuranceProvider;

  Map<String, dynamic> toJson() {
    InvoiceCreationValidation.id(patientId, 'patientId');
    if (appointmentId != null) {
      InvoiceCreationValidation.id(appointmentId!, 'appointmentId');
    }
    if (!RegExp(r'^[A-Z]{3}$').hasMatch(currency)) {
      throw const FormatException(
        'Currency must be three uppercase ASCII letters.',
      );
    }
    if (items.isEmpty || items.length > 25) {
      throw const FormatException('Invoice requires 1–25 items.');
    }
    if (dueDate != null) InvoiceCreationValidation.date(dueDate!);
    if (isInsuranceClaim &&
        (insuranceProvider == null ||
            insuranceProvider!.isEmpty ||
            insuranceProvider!.trim() != insuranceProvider ||
            insuranceProvider!.length > 120)) {
      throw const FormatException('Insurance provider is required for claims.');
    }
    if (!isInsuranceClaim && insuranceProvider != null) {
      throw const FormatException(
        'Non-insurance invoice cannot name an insurer.',
      );
    }
    return <String, dynamic>{
      'patient_id': patientId,
      'currency': currency,
      if (appointmentId != null) 'appointment_id': appointmentId,
      if (dueDate != null) 'due_date': dueDate,
      'is_insurance_claim': isInsuranceClaim,
      if (insuranceProvider != null) 'insurance_provider': insuranceProvider,
      'items': items.map((item) => item.toJson()).toList(growable: false),
    };
  }
}

class InvoiceCreationValidation {
  const InvoiceCreationValidation._();

  static void id(int value, String name) {
    if (value <= 0 || value > 2147483647) {
      throw FormatException('Invalid invoice identity: $name');
    }
  }

  static String decimal(Object? value, {bool allowZero = false}) {
    if (value is! String ||
        value.length > 64 ||
        !RegExp(r'^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$').hasMatch(value)) {
      throw const FormatException('Invalid exact decimal amount.');
    }
    if (!allowZero && BigInt.parse(value.replaceAll('.', '')) == BigInt.zero) {
      throw const FormatException('Amount must be positive.');
    }
    return value;
  }

  static String date(String value) {
    if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(value)) {
      throw const FormatException('Invalid calendar date.');
    }
    final dt = DateTime.tryParse(value);
    if (dt == null || dt.toIso8601String().substring(0, 10) != value) {
      throw const FormatException('Invalid calendar date.');
    }
    return value;
  }

  static int requiredId(Map<String, dynamic> row, String field) {
    final idValue = row[field];
    if (idValue is! int) throw FormatException('Invalid $field.');
    id(idValue, field);
    return idValue;
  }

  static String text(Map<String, dynamic> row, String field) {
    final value = row[field];
    if (value is! String ||
        value.isEmpty ||
        value.trim() != value ||
        value.length > 255) {
      throw FormatException('Invalid $field.');
    }
    return value;
  }
}

enum InvoiceCreatedStatus {
  draft,
  issued,
  partiallyPaid,
  paid,
  overdue,
  cancelled,
}

class CreatedInvoice {
  const CreatedInvoice({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.invoiceNumber,
    required this.currency,
    required this.totalAmount,
    required this.amountPaid,
    required this.status,
    required this.itemCount,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final String invoiceNumber;
  final String currency;
  final String totalAmount;
  final String amountPaid;
  final InvoiceCreatedStatus status;
  final int itemCount;

  factory CreatedInvoice.fromEnvelope(
    Map<String, dynamic> envelope, {
    required int expectedClinicId,
    required InvoiceDraft requested,
  }) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invoice creation not confirmed by backend.');
    }
    final row = envelope['data'] as Map<String, dynamic>;
    final clinicId = InvoiceCreationValidation.requiredId(row, 'clinic_id');
    final patientId = InvoiceCreationValidation.requiredId(row, 'patient_id');
    if (clinicId != expectedClinicId || patientId != requested.patientId) {
      throw const FormatException('Invoice scope confirmation mismatch.');
    }
    final currency = InvoiceCreationValidation.text(row, 'currency');
    if (currency != requested.currency) {
      throw const FormatException('Invoice currency confirmation mismatch.');
    }
    final status = switch (row['status']) {
      'draft' => InvoiceCreatedStatus.draft,
      'issued' => InvoiceCreatedStatus.issued,
      'partially_paid' => InvoiceCreatedStatus.partiallyPaid,
      'paid' => InvoiceCreatedStatus.paid,
      'overdue' => InvoiceCreatedStatus.overdue,
      'cancelled' => InvoiceCreatedStatus.cancelled,
      _ => throw const FormatException('Invalid server invoice status.'),
    };
    final amount = InvoiceCreationValidation.decimal(
      row['total_amount'],
      allowZero: true,
    );
    final paid = InvoiceCreationValidation.decimal(
      row['amount_paid'],
      allowZero: true,
    );
    if (_scaled(paid, amount) > 0) {
      throw const FormatException('Invalid confirmed invoice amount.');
    }
    final rawItems = row['items'];
    if (rawItems is! List || rawItems.length != requested.items.length) {
      throw const FormatException('Invoice item confirmation mismatch.');
    }
    for (final entry in rawItems) {
      if (entry is! Map<String, dynamic>) {
        throw const FormatException('Malformed confirmed invoice item.');
      }
      if (InvoiceCreationValidation.requiredId(entry, 'invoice_id') !=
          InvoiceCreationValidation.requiredId(row, 'id')) {
        throw const FormatException('Invoice item belongs to another invoice.');
      }
      InvoiceCreationValidation.text(entry, 'description');
      final quantity = entry['quantity'];
      if (quantity is! int || quantity <= 0) {
        throw const FormatException('Invalid confirmed invoice item quantity.');
      }
      InvoiceCreationValidation.decimal(entry['unit_price'], allowZero: true);
      InvoiceCreationValidation.decimal(entry['subtotal'], allowZero: true);
    }
    return CreatedInvoice(
      id: InvoiceCreationValidation.requiredId(row, 'id'),
      clinicId: clinicId,
      patientId: patientId,
      invoiceNumber: InvoiceCreationValidation.text(row, 'invoice_number'),
      currency: currency,
      totalAmount: amount,
      amountPaid: paid,
      status: status,
      itemCount: rawItems.length,
    );
  }

  static int _scaled(String a, String b) {
    final left = a.split('.');
    final right = b.split('.');
    final scale = left.length > 1 ? left[1].length : 0;
    final other = right.length > 1 ? right[1].length : 0;
    final maxScale = scale > other ? scale : other;
    BigInt exact(List<String> parts) =>
        BigInt.parse(parts[0]) * BigInt.from(10).pow(maxScale) +
        (parts.length == 2
            ? BigInt.parse(parts[1]) *
                  BigInt.from(10).pow(maxScale - parts[1].length)
            : BigInt.zero);
    return exact(left).compareTo(exact(right));
  }
}
