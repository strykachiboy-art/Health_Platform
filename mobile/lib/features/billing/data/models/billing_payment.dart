import 'package:clinic_system_pro/features/billing/data/models/outstanding_invoice.dart';

/// P23I.S04 — backend-owned money/status only. Never infer paid from a callback.
enum BillingPaymentFlow { gatewayInitialize, directRecord }

enum BillingPaymentStatus { pending, successful, failed, refunded }

class BillingPaymentDraft {
  const BillingPaymentDraft({
    required this.invoice,
    required this.flow,
    required this.amount,
    required this.method,
    this.gateway,
    this.reference,
  });

  final OutstandingInvoice invoice;
  final BillingPaymentFlow flow;
  final String amount;
  final String method;
  final String? gateway;
  final String? reference;

  static String exactAmount(Object? raw, {bool positive = true}) {
    if (raw is! String ||
        raw.length > 64 ||
        !RegExp(r'^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$').hasMatch(raw) ||
        (positive && BigInt.parse(raw.replaceAll('.', '')) == BigInt.zero)) {
      throw const FormatException('Invalid exact payment amount.');
    }
    return raw;
  }

  static BigInt _units(String raw, int scale) {
    final parts = raw.split('.');
    final fraction = parts.length == 2 ? parts[1] : '';
    return BigInt.parse(parts.first) * BigInt.from(10).pow(scale) +
        (fraction.isEmpty
            ? BigInt.zero
            : BigInt.parse(fraction) *
                  BigInt.from(10).pow(scale - fraction.length));
  }

  static int compare(String a, String b) {
    exactAmount(a, positive: false);
    exactAmount(b, positive: false);
    final scaleA = a.contains('.') ? a.split('.').last.length : 0;
    final scaleB = b.contains('.') ? b.split('.').last.length : 0;
    final scale = scaleA > scaleB ? scaleA : scaleB;
    return _units(a, scale).compareTo(_units(b, scale));
  }

  static bool withinBalance(String amount, OutstandingInvoice invoice) {
    exactAmount(amount);
    exactAmount(invoice.totalAmount, positive: false);
    exactAmount(invoice.amountPaid, positive: false);
    final scale = <String>[amount, invoice.totalAmount, invoice.amountPaid]
        .map((s) => s.contains('.') ? s.split('.').last.length : 0)
        .reduce((a, b) => a > b ? a : b);
    final amountUnits = _units(amount, scale);
    final remaining =
        _units(invoice.totalAmount, scale) - _units(invoice.amountPaid, scale);
    return remaining > BigInt.zero && amountUnits <= remaining;
  }

  Map<String, dynamic> toJson() {
    if (invoice.id <= 0 ||
        invoice.clinicId <= 0 ||
        invoice.patientId <= 0 ||
        !const <OutstandingInvoiceStatus>{
          OutstandingInvoiceStatus.issued,
          OutstandingInvoiceStatus.partiallyPaid,
          OutstandingInvoiceStatus.overdue,
        }.contains(invoice.status) ||
        invoice.currency == null ||
        !RegExp(r'^[A-Z]{3}$').hasMatch(invoice.currency!) ||
        !withinBalance(amount, invoice)) {
      throw const FormatException(
        'Payment exceeds verified invoice balance or scope.',
      );
    }
    if (reference != null &&
        (reference!.isEmpty ||
            reference!.length > 120 ||
            reference != reference!.trim() ||
            reference!.contains('\n') ||
            reference!.contains('\r'))) {
      throw const FormatException('Invalid payment reference.');
    }
    if (flow == BillingPaymentFlow.gatewayInitialize) {
      if (!const <String>{
            'card',
            'bank_transfer',
            'mobile_money',
          }.contains(method) ||
          !const <String>{
            'stripe',
            'paystack',
            'flutterwave',
          }.contains(gateway)) {
        throw const FormatException('Invalid gateway payment combination.');
      }
      return <String, dynamic>{
        'invoice_id': invoice.id,
        'amount': amount,
        'method': method,
        'gateway': gateway,
        'currency': invoice.currency,
        if (reference != null) 'reference': reference,
      };
    }
    // S04 limits direct recording to non-gateway manual methods. Never
    // construct a fake gateway transaction or a self-attested card payment.
    if (!const <String>{'cash', 'insurance'}.contains(method) ||
        gateway != null) {
      throw const FormatException(
        'Direct records cannot claim gateway payments.',
      );
    }
    return <String, dynamic>{
      'invoice_id': invoice.id,
      'amount': amount,
      'method': method,
      if (reference != null) 'reference': reference,
    };
  }
}

class BillingPaymentConfirmation {
  const BillingPaymentConfirmation({
    required this.id,
    required this.invoiceId,
    required this.amount,
    required this.method,
    required this.status,
    required this.gateway,
    required this.reference,
    required this.gatewayTransactionId,
    required this.currency,
    required this.pendingProviderConfirmation,
  });

  final int id;
  final int invoiceId;
  final String amount;
  final String method;
  final BillingPaymentStatus status;
  final String? gateway;
  final String? reference;
  final String? gatewayTransactionId;
  final String? currency;
  final bool pendingProviderConfirmation;

  static int _id(Object? value) {
    if (value is! int || value <= 0 || value > 2147483647) {
      throw const FormatException('Invalid payment identity.');
    }
    return value;
  }

  static String? _optionalText(Object? raw, int limit) {
    if (raw == null) return null;
    if (raw is! String ||
        raw.isEmpty ||
        raw.length > limit ||
        raw.trim() != raw) {
      throw const FormatException('Invalid payment text.');
    }
    return raw;
  }

  factory BillingPaymentConfirmation.fromEnvelope(
    Map<String, dynamic> envelope, {
    required BillingPaymentDraft requested,
  }) {
    if (envelope['success'] != true ||
        envelope['data'] is! Map<String, dynamic>) {
      throw const FormatException('Payment not confirmed by backend.');
    }
    final data = envelope['data'] as Map<String, dynamic>;
    final initialized = requested.flow == BillingPaymentFlow.gatewayInitialize;
    final row = initialized ? data['payment'] : data;
    if (row is! Map<String, dynamic>) {
      throw const FormatException('Invalid server payment record.');
    }
    final id = _id(row['id']);
    final invoiceId = _id(row['invoice_id']);
    final amount = BillingPaymentDraft.exactAmount(row['amount']);
    final method = row['method'];
    final gateway = _optionalText(row['gateway'], 30);
    final reference = _optionalText(row['reference'], 120);
    final transaction = _optionalText(row['gateway_transaction_id'], 255);
    final currency = _optionalText(row['currency'], 3);
    if (invoiceId != requested.invoice.id ||
        BillingPaymentDraft.compare(amount, requested.amount) != 0 ||
        method != requested.method ||
        gateway != requested.gateway ||
        (currency != null && currency != requested.invoice.currency) ||
        (requested.reference != null && reference != requested.reference)) {
      throw const FormatException('Payment response does not match request.');
    }
    final status = switch (row['status']) {
      'pending' => BillingPaymentStatus.pending,
      'successful' => BillingPaymentStatus.successful,
      'failed' => BillingPaymentStatus.failed,
      'refunded' => BillingPaymentStatus.refunded,
      _ => throw const FormatException('Invalid payment status.'),
    };
    if (initialized) {
      final outcome = data['outcome'];
      if ((outcome == 'pending' && status != BillingPaymentStatus.pending) ||
          (outcome == 'successful' &&
              status != BillingPaymentStatus.successful) ||
          !const <String>{'pending', 'successful'}.contains(outcome) ||
          (status == BillingPaymentStatus.successful && transaction == null)) {
        throw const FormatException('Unverified gateway payment outcome.');
      }
    } else if (status != BillingPaymentStatus.successful) {
      throw const FormatException(
        'Direct payment recording was not confirmed.',
      );
    }
    for (final key in <String>['created_at', 'updated_at']) {
      final stamp = _optionalText(row[key], 90);
      if (stamp == null || DateTime.tryParse(stamp) == null) {
        throw const FormatException('Invalid payment audit timestamp.');
      }
    }
    final paidAt = _optionalText(row['paid_at'], 90);
    if (paidAt != null && DateTime.tryParse(paidAt) == null) {
      throw const FormatException('Invalid payment completion timestamp.');
    }
    return BillingPaymentConfirmation(
      id: id,
      invoiceId: invoiceId,
      amount: amount,
      method: method as String,
      status: status,
      gateway: gateway,
      reference: reference,
      gatewayTransactionId: transaction,
      currency: currency,
      pendingProviderConfirmation: status == BillingPaymentStatus.pending,
    );
  }
}
