// P23I.S08 — cross-domain, backend-authoritative ambulance invoice link.
// Only admin can read the existing billing list; no invoice-detail GET exists.
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_read.dart';
import 'package:clinic_system_pro/features/billing/data/models/outstanding_invoice.dart';

class AmbulanceInvoiceLinkCommand {
  const AmbulanceInvoiceLinkCommand({
    required this.tripId,
    required this.invoiceId,
    this.invoicePage = 1,
  });

  final int tripId;
  final int invoiceId;
  final int invoicePage;

  void validate() {
    if (tripId <= 0 ||
        tripId > 2147483647 ||
        invoiceId <= 0 ||
        invoiceId > 2147483647 ||
        invoicePage <= 0 ||
        invoicePage > 2147483647) {
      throw const FormatException('Invalid trip, invoice or invoice page.');
    }
  }
}

class AmbulanceInvoiceLinkInspection {
  const AmbulanceInvoiceLinkInspection({
    required this.command,
    required this.revision,
    required this.trip,
    required this.invoice,
  });

  final AmbulanceInvoiceLinkCommand command;
  final int revision;
  final AmbulanceTripRead trip;
  final OutstandingInvoice invoice;

  bool matches(AmbulanceInvoiceLinkInspection other) =>
      command.tripId == other.command.tripId &&
      command.invoiceId == other.command.invoiceId &&
      command.invoicePage == other.command.invoicePage &&
      revision == other.revision &&
      trip.id == other.trip.id &&
      trip.clinicId == other.trip.clinicId &&
      trip.patientId == other.trip.patientId &&
      trip.status == other.trip.status &&
      trip.vehicleId == other.trip.vehicleId &&
      trip.driverId == other.trip.driverId &&
      trip.paramedicId == other.trip.paramedicId &&
      trip.admissionId == other.trip.admissionId &&
      trip.tripType == other.trip.tripType &&
      trip.invoiceId == other.trip.invoiceId &&
      trip.completedAt == other.trip.completedAt &&
      invoice.id == other.invoice.id &&
      invoice.clinicId == other.invoice.clinicId &&
      invoice.patientId == other.invoice.patientId &&
      invoice.invoiceNumber == other.invoice.invoiceNumber &&
      invoice.currency == other.invoice.currency &&
      invoice.totalAmount == other.invoice.totalAmount &&
      invoice.amountPaid == other.invoice.amountPaid &&
      invoice.status == other.invoice.status &&
      invoice.updatedAt == other.invoice.updatedAt;
}

class AmbulanceInvoiceLinkConfirmation {
  const AmbulanceInvoiceLinkConfirmation({
    required this.trip,
    required this.invoice,
  });
  final AmbulanceTripRead trip;
  final OutstandingInvoice invoice;
}
