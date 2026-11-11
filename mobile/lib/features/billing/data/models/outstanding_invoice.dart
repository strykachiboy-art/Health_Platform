/// P23I.S02: immutable, server-originated outstanding-invoice read models.
/// Decimal money stays as an exact string. Never calculate balances with double.
enum OutstandingInvoiceStatus { issued, partiallyPaid, overdue }

class OutstandingInvoice {
  const OutstandingInvoice({
    required this.id,
    required this.clinicId,
    required this.patientId,
    required this.appointmentId,
    required this.invoiceNumber,
    required this.currency,
    required this.totalAmount,
    required this.amountPaid,
    required this.status,
    required this.dueDate,
    required this.isInsuranceClaim,
    required this.insuranceProvider,
    required this.createdAt,
    required this.updatedAt,
  });

  final int id;
  final int clinicId;
  final int patientId;
  final int? appointmentId;
  final String invoiceNumber;
  final String? currency;
  final String totalAmount;
  final String amountPaid;
  final OutstandingInvoiceStatus status;
  final String? dueDate;
  final bool isInsuranceClaim;
  final String? insuranceProvider;
  final String createdAt;
  final String updatedAt;

  factory OutstandingInvoice.fromJson(
    Map<String, dynamic> json, {
    required int expectedClinicId,
  }) {
    final id = _id(json, 'id');
    final clinicId = _id(json, 'clinic_id');
    if (clinicId != expectedClinicId) {
      throw const FormatException('Cross-clinic invoice rejected.');
    }
    final total = _decimal(json, 'total_amount');
    final paid = _decimal(json, 'amount_paid');
    if (_compareDecimal(paid, total) > 0) {
      throw const FormatException('Invoice paid amount exceeds total.');
    }
    final status = switch (json['status']) {
      'issued' => OutstandingInvoiceStatus.issued,
      'partially_paid' => OutstandingInvoiceStatus.partiallyPaid,
      'overdue' => OutstandingInvoiceStatus.overdue,
      _ => throw const FormatException(
        'Unexpected outstanding invoice status.',
      ),
    };
    final currency = _nullableText(json, 'currency');
    if (currency != null && !RegExp(r'^[A-Z]{3}$').hasMatch(currency)) {
      throw const FormatException('Invalid invoice currency.');
    }
    final insurance = json['is_insurance_claim'];
    if (insurance is! bool) {
      throw const FormatException('Invalid invoice insurance flag.');
    }
    final provider = _nullableText(json, 'insurance_provider');
    if (insurance && provider == null) {
      throw const FormatException('Missing insurance provider.');
    }
    return OutstandingInvoice(
      id: id,
      clinicId: clinicId,
      patientId: _id(json, 'patient_id'),
      appointmentId: _nullableId(json, 'appointment_id'),
      invoiceNumber: _requiredText(json, 'invoice_number'),
      currency: currency,
      totalAmount: total,
      amountPaid: paid,
      status: status,
      dueDate: _nullableDate(json, 'due_date'),
      isInsuranceClaim: insurance,
      insuranceProvider: provider,
      createdAt: _timestamp(json, 'created_at'),
      updatedAt: _timestamp(json, 'updated_at'),
    );
  }

  static int _compareDecimal(String a, String b) {
    final first = a.split('.');
    final second = b.split('.');
    final fraction = first.length == 2 ? first[1].length : 0;
    final otherFraction = second.length == 2 ? second[1].length : 0;
    final scale = fraction > otherFraction ? fraction : otherFraction;
    BigInt exact(List<String> parts) =>
        BigInt.parse(parts[0]) * BigInt.from(10).pow(scale) +
        (parts.length == 2
            ? BigInt.parse(parts[1]) *
                  BigInt.from(10).pow(scale - parts[1].length)
            : BigInt.zero);
    return exact(first).compareTo(exact(second));
  }
}

class OutstandingInvoicePage {
  const OutstandingInvoicePage({
    required this.items,
    required this.total,
    required this.page,
    required this.perPage,
    required this.pages,
    required this.hasNext,
  });

  final List<OutstandingInvoice> items;
  final int total;
  final int page;
  final int perPage;
  final int pages;
  final bool hasNext;

  factory OutstandingInvoicePage.fromEnvelope(
    Map<String, dynamic> envelope, {
    required int expectedClinicId,
    required int requestedPage,
    required int requestedPerPage,
  }) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid outstanding invoice envelope.');
    }
    final data = envelope['data'] as Map<String, dynamic>;
    final raw = data['items'];
    final total = data['total'];
    final page = data['page'];
    final perPage = data['per_page'];
    final pages = data['pages'];
    final hasNext = data['has_next'];
    final hasPrev = data['has_prev'];
    if (raw is! List ||
        total is! int ||
        total < 0 ||
        page != requestedPage ||
        perPage != requestedPerPage ||
        pages is! int ||
        pages != (total + requestedPerPage - 1) ~/ requestedPerPage ||
        hasNext is! bool ||
        hasPrev is! bool ||
        hasNext != (page < pages) ||
        hasPrev != (page > 1) ||
        raw.length > requestedPerPage ||
        (page - 1) * perPage + raw.length > total ||
        (page == 1 && total > 0 && raw.isEmpty)) {
      throw const FormatException('Conflicting invoice pagination.');
    }
    final ids = <int>{};
    final invoices = <OutstandingInvoice>[];
    for (final item in raw) {
      if (item is! Map<String, dynamic>) {
        throw const FormatException('Malformed invoice row.');
      }
      final invoice = OutstandingInvoice.fromJson(
        item,
        expectedClinicId: expectedClinicId,
      );
      if (!ids.add(invoice.id)) {
        throw const FormatException('Duplicate invoice on page.');
      }
      invoices.add(invoice);
    }
    return OutstandingInvoicePage(
      items: List<OutstandingInvoice>.unmodifiable(invoices),
      total: total,
      page: page,
      perPage: perPage,
      pages: pages,
      hasNext: hasNext,
    );
  }
}

int _id(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! int || value <= 0 || value > 2147483647) {
    throw FormatException('Invalid invoice identifier: $key');
  }
  return value;
}

int? _nullableId(Map<String, dynamic> json, String key) =>
    json[key] == null ? null : _id(json, key);

String _requiredText(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value is! String ||
      value.trim().isEmpty ||
      value != value.trim() ||
      value.length > 255) {
    throw FormatException('Invalid invoice text: $key');
  }
  return value;
}

String? _nullableText(Map<String, dynamic> json, String key) =>
    json[key] == null ? null : _requiredText(json, key);

String _decimal(Map<String, dynamic> json, String key) {
  final raw = json[key];
  if (raw is! String ||
      raw.length > 64 ||
      !RegExp(r'^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$').hasMatch(raw)) {
    throw FormatException('Invalid exact invoice decimal: $key');
  }
  return raw;
}

String? _nullableDate(Map<String, dynamic> json, String key) {
  final value = json[key];
  if (value == null) return null;
  if (value is! String || !RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(value)) {
    throw FormatException('Invalid invoice date: $key');
  }
  final parsed = DateTime.tryParse(value);
  if (parsed == null || parsed.toIso8601String().substring(0, 10) != value) {
    throw FormatException('Invalid calendar date: $key');
  }
  return value;
}

String _timestamp(Map<String, dynamic> json, String key) {
  final value = _requiredText(json, key);
  if (DateTime.tryParse(value) == null) {
    throw FormatException('Invalid invoice timestamp: $key');
  }
  return value;
}
