// P23I.S08 — admin-only completed-trip invoice linking. No auto replay.
// Billing exposes an outstanding-invoice list, not arbitrary invoice detail.
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_invoice_link.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_read.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/billing/data/billing_outstanding_remote_data_source.dart';
import 'package:clinic_system_pro/features/billing/data/models/outstanding_invoice.dart';

class AmbulanceInvoiceLinkUnauthorized implements Exception {
  const AmbulanceInvoiceLinkUnauthorized();
}

class AmbulanceInvoiceLinkEvidenceChanged implements Exception {
  const AmbulanceInvoiceLinkEvidenceChanged();
}

class AmbulanceInvoiceLinkAlreadyAttempted implements Exception {
  const AmbulanceInvoiceLinkAlreadyAttempted();
}

class AmbulanceInvoiceLinkOutcomeUnconfirmed implements Exception {
  const AmbulanceInvoiceLinkOutcomeUnconfirmed();
}

class AmbulanceInvoiceLinkRemoteDataSource {
  AmbulanceInvoiceLinkRemoteDataSource({
    required this.ambulanceReader,
    required this.billingReader,
  }) {
    if (!identical(
          ambulanceReader.sessionNavigationGuard,
          billingReader.sessionNavigationGuard,
        ) ||
        !identical(ambulanceReader.apiClient, billingReader.apiClient)) {
      throw ArgumentError(
        'Billing and ambulance readers must share an API client and guard.',
      );
    }
  }

  final AmbulanceReadRemoteDataSource ambulanceReader;
  final BillingOutstandingRemoteDataSource billingReader;
  bool _writeReserved = false;
  bool _writeAttempted = false;
  AmbulanceInvoiceLinkInspection? _lastInspection;

  bool get writeAttempted => _writeAttempted;
  bool get _active =>
      ambulanceReader.sessionNavigationGuard.canAccessProtectedRoutes;
  int get _revision => ambulanceReader.sessionNavigationGuard.revision;

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.clinicContextId == b.clinicContextId &&
      a.role.trim().toLowerCase() == b.role.trim().toLowerCase() &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;

  Future<AuthTokenResponse> _admin(int revision) async {
    if (!_active || _revision != revision) {
      throw const AmbulanceInvoiceLinkUnauthorized();
    }
    try {
      final a = await ambulanceReader.loadSession();
      final b = await billingReader.loadSession();
      final sa = a.session;
      final sb = b.session;
      if (a.disposition != AuthSessionDisposition.ready ||
          b.disposition != AuthSessionDisposition.ready ||
          sa == null ||
          sb == null ||
          sa.userId <= 0 ||
          sa.clinicContextId == null ||
          sa.clinicContextId! <= 0 ||
          sa.role.trim().toLowerCase() != 'admin' ||
          sa.accessToken.isEmpty ||
          sa.accessToken.trim() != sa.accessToken ||
          !_same(sa, sb) ||
          !_active ||
          _revision != revision) {
        throw const AmbulanceInvoiceLinkUnauthorized();
      }
      return sa;
    } on AmbulanceInvoiceLinkUnauthorized {
      rethrow;
    } on Object {
      throw const AmbulanceInvoiceLinkUnauthorized();
    }
  }

  static void _eligible(AmbulanceTripRead trip, OutstandingInvoice invoice) {
    if (trip.status != 'completed' ||
        trip.completedAt == null ||
        trip.invoiceId != null ||
        trip.patientId == null ||
        trip.patientId != invoice.patientId ||
        trip.clinicId != invoice.clinicId) {
      throw const AmbulanceInvoiceLinkEvidenceChanged();
    }
  }

  Future<AmbulanceInvoiceLinkInspection> _fetch(
    AmbulanceInvoiceLinkCommand command,
    int revision,
  ) async {
    command.validate();
    final before = await _admin(revision);
    final trip = await ambulanceReader.fetchTripDetail(command.tripId);
    final invoices = await billingReader.fetchOutstanding(
      page: command.invoicePage,
      perPage: 20,
    );
    final after = await _admin(revision);
    if (!_same(before, after) ||
        !_active ||
        _revision != revision ||
        trip.clinicId != before.clinicContextId) {
      throw const AmbulanceInvoiceLinkUnauthorized();
    }
    OutstandingInvoice? invoice;
    for (final item in invoices.items) {
      if (item.id == command.invoiceId) {
        invoice = item;
        break;
      }
    }
    if (invoice == null) {
      throw const AmbulanceInvoiceLinkEvidenceChanged();
    }
    _eligible(trip, invoice);
    return AmbulanceInvoiceLinkInspection(
      command: command,
      revision: revision,
      trip: trip,
      invoice: invoice,
    );
  }

  Future<AmbulanceInvoiceLinkInspection> inspect(
    AmbulanceInvoiceLinkCommand command,
  ) async {
    if (_writeReserved || _writeAttempted) {
      throw const AmbulanceInvoiceLinkAlreadyAttempted();
    }
    final result = await _fetch(command, _revision);
    _lastInspection = result;
    return result;
  }

  Future<AmbulanceInvoiceLinkConfirmation> linkOnce({
    required AmbulanceInvoiceLinkCommand command,
    required AmbulanceInvoiceLinkInspection inspection,
  }) async {
    if (_writeReserved || _writeAttempted) {
      throw const AmbulanceInvoiceLinkAlreadyAttempted();
    }
    command.validate();
    if (!identical(inspection, _lastInspection) ||
        inspection.command.tripId != command.tripId ||
        inspection.command.invoiceId != command.invoiceId ||
        inspection.command.invoicePage != command.invoicePage ||
        inspection.revision != _revision) {
      throw const AmbulanceInvoiceLinkEvidenceChanged();
    }
    _writeReserved = true;
    late final AuthTokenResponse before;
    try {
      before = await _admin(inspection.revision);
      final fresh = await _fetch(command, inspection.revision);
      if (!inspection.matches(fresh) ||
          !_same(before, await _admin(inspection.revision))) {
        throw const AmbulanceInvoiceLinkEvidenceChanged();
      }
    } on Object {
      _writeReserved = false;
      rethrow;
    }
    _writeAttempted = true;
    try {
      final envelope = await ambulanceReader.apiClient.postJson(
        'api/v1/ambulance/trips/${command.tripId}/invoice',
        bearerToken: before.accessToken,
        body: <String, dynamic>{'invoice_id': command.invoiceId},
      );
      if (!_same(before, await _admin(inspection.revision))) {
        throw const AmbulanceInvoiceLinkOutcomeUnconfirmed();
      }
      if (envelope['success'] != true ||
          envelope['data'] is! Map<String, dynamic>) {
        throw const FormatException(
          'Missing server invoice-link confirmation.',
        );
      }
      final trip = AmbulanceTripRead.fromJson(
        envelope['data'] as Map<String, dynamic>,
        expectedClinicId: before.clinicContextId!,
      );
      final old = inspection.trip;
      if (trip.id != old.id ||
          trip.status != 'completed' ||
          trip.completedAt != old.completedAt ||
          trip.patientId != old.patientId ||
          trip.invoiceId != command.invoiceId ||
          trip.vehicleId != old.vehicleId ||
          trip.driverId != old.driverId ||
          trip.paramedicId != old.paramedicId ||
          trip.admissionId != old.admissionId ||
          trip.tripType != old.tripType) {
        throw const FormatException('Invoice linkage was not confirmed.');
      }
      return AmbulanceInvoiceLinkConfirmation(
        trip: trip,
        invoice: inspection.invoice,
      );
    } on Object {
      // Even a timeout or malformed success may have persisted. Do not replay.
      throw const AmbulanceInvoiceLinkOutcomeUnconfirmed();
    }
  }
}
