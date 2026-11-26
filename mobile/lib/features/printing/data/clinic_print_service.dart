import 'dart:async';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'rad7_print_contract.dart';

/// Exactly the six canonical PDF paths; patient copies are not staff paths.
enum ClinicPrintSource {
  invoice, appointment, labOrder, radiologyDraft, releasedLab, stagedRadiology;

  String get label => switch (this) {
    invoice => Rad7PrintKind.invoice.label,
    appointment => Rad7PrintKind.appointment.label,
    labOrder => Rad7PrintKind.labOrder.label,
    radiologyDraft => Rad7PrintKind.radiologyDraft.label,
    releasedLab => 'Released laboratory patient copy (unsigned)',
    stagedRadiology => 'Synthetic radiology patient copy (not diagnostic)',
  };

  bool allowsRole(String role) => switch (this) {
    invoice => Rad7PrintKind.invoice.allowsRole(role),
    appointment => Rad7PrintKind.appointment.allowsRole(role),
    labOrder => Rad7PrintKind.labOrder.allowsRole(role),
    radiologyDraft => Rad7PrintKind.radiologyDraft.allowsRole(role),
    releasedLab || stagedRadiology => role.trim().toLowerCase() == 'patient',
  };

  String path(int id) {
    if (id <= 0 || id > 2147483647) {
      throw const ClinicPrintUnavailable();
    }
    return switch (this) {
      invoice => 'api/v1/printing/invoice/$id.pdf',
      appointment => 'api/v1/printing/appointment/$id.pdf',
      labOrder => 'api/v1/printing/lab_order/$id.pdf',
      radiologyDraft => 'api/v1/printing/radiology_draft/$id.pdf',
      releasedLab => 'api/v1/lab/me/released-results/$id/pdf',
      stagedRadiology => 'api/v1/radiology/me/staged-reports/$id/pdf',
    };
  }

  bool validDisposition(String value, int id) {
    if (!value.toLowerCase().startsWith('attachment;')) return false;
    final filename = switch (this) {
      invoice => 'invoice-$id.pdf',
      appointment => 'appointment-$id.pdf',
      labOrder => 'lab_order-$id.pdf',
      radiologyDraft => 'radiology_draft-$id.pdf',
      stagedRadiology => 'staged-radiology-copy.pdf',
      releasedLab => null,
    };
    final match = RegExp(r'(?:^|;)\s*filename\s*=\s*"?([a-zA-Z0-9_.-]+)"?\s*(?:;|$)').firstMatch(value);
    final actual = match?.group(1);
    return filename != null ? actual == filename :
        actual != null && RegExp('^released-lab-$id-v[1-9][0-9]*\\.pdf\$').hasMatch(actual);
  }
}

class ClinicPrintUnavailable implements Exception {
  const ClinicPrintUnavailable();
}

/// The device gets only the server-rendered bytes. No local PDF generation,
/// local files, print queues, automatic replay or arbitrary URL downloads.
abstract interface class ClinicPrintDevice {
  Future<bool> available();
  Future<bool> present(Future<Uint8List> Function() layout);
}

class ClinicPrintService {
  ClinicPrintService({required this.api, required this.loadSession,
    required this.guard, required this.device, http.Client Function()? clientFactory,
    this.timeout = const Duration(seconds: 25)}) : _clientFactory = clientFactory ?? http.Client.new;

  final ApiClient api;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard guard;
  final ClinicPrintDevice device;
  final Duration timeout;
  final http.Client Function() _clientFactory;
  static const maxBytes = 3 * 1024 * 1024;
  // Prevent simultaneous jobs from different screens under the same app session.
  static final Expando<bool> _busy = Expando<bool>('clinic-print-job');

  Future<AuthTokenResponse> _session(ClinicPrintSource source, int revision, bool Function() active) async {
    if (!active() || !guard.canAccessProtectedRoutes || guard.revision != revision) {
      throw const ClinicPrintUnavailable();
    }
    final state = await loadSession();
    final actor = state.session;
    if (!active() || !guard.canAccessProtectedRoutes || guard.revision != revision ||
        state.disposition != AuthSessionDisposition.ready || actor == null ||
        actor.userId <= 0 || actor.clinicContextId == null || actor.clinicContextId! <= 0 ||
        actor.accessToken.isEmpty || actor.accessToken.trim() != actor.accessToken ||
        actor.refreshToken.isEmpty || actor.tenantId == null || actor.tenantId!.isEmpty ||
        !source.allowsRole(actor.role)) {
      throw const ClinicPrintUnavailable();
    }
    return actor;
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId && a.tenantId == b.tenantId &&
      a.clinicContextId == b.clinicContextId && a.role == b.role &&
      a.accessToken == b.accessToken && a.refreshToken == b.refreshToken;

  Future<bool> printCurrent({required ClinicPrintSource source, required int id,
      required bool Function() active}) async {
    final path = source.path(id);
    if (_busy[guard] == true) throw const ClinicPrintUnavailable();
    _busy[guard] = true;
    final revision = guard.revision;
    Uint8List? bytes;
    var handedOff = false;
    var closed = false;
    try {
      final actor = await _session(source, revision, active);
      if (!await device.available()) throw const ClinicPrintUnavailable();
      Future<void> recheck() async {
        if (closed || !_same(actor, await _session(source, revision, active))) {
          throw const ClinicPrintUnavailable();
        }
      }
      await recheck();
      final client = _clientFactory();
      try {
        final request = http.Request('GET', api.config.endpoint(path))
          ..followRedirects = false
          ..headers['Authorization'] = 'Bearer ${actor.accessToken}'
          ..headers['Accept'] = 'application/pdf'
          ..headers['Cache-Control'] = 'no-store';
        final watch = Stopwatch()..start();
        final response = await client.send(request).timeout(timeout);
        if (response.statusCode == 401) api.onAuthenticationRejected?.call();
        final headers = response.headers;
        final length = response.contentLength;
        if (response.statusCode != 200 ||
            headers['content-type']?.split(';').first.trim().toLowerCase() != 'application/pdf' ||
            !RegExp(r'(?:^|,)\s*no-store\s*(?:,|$)', caseSensitive: false).hasMatch(headers['cache-control'] ?? '') ||
            headers['x-content-type-options']?.toLowerCase() != 'nosniff' ||
            !RegExp(r'^[a-f0-9]{64}$').hasMatch(headers['x-source-sha256'] ?? '') ||
            !source.validDisposition(headers['content-disposition'] ?? '', id) ||
            (source == ClinicPrintSource.stagedRadiology
              ? headers['x-radiology-scope'] != 'synthetic-nondiagnostic-only'
              : headers['x-clinical-release'] != (source == ClinicPrintSource.releasedLab ? 'patient-copy' : 'none')) ||
            (length != null && (length <= 0 || length > maxBytes))) {
          throw const ClinicPrintUnavailable();
        }
        final buffer = BytesBuilder(copy: true);
        await for (final chunk in response.stream.timeout(timeout)) {
          if (!active() || guard.revision != revision || watch.elapsed > timeout || buffer.length + chunk.length > maxBytes) {
            throw const ClinicPrintUnavailable();
          }
          buffer.add(chunk);
        }
        final received = buffer.takeBytes();
        bytes = received;
        if (received.length < 12 || (length != null && received.length != length) ||
            String.fromCharCodes(received.take(5)) != '%PDF-' ||
            !String.fromCharCodes(received.skip(received.length > 32 ? received.length - 32 : 0)).trimRight().endsWith('%%EOF')) {
          throw const ClinicPrintUnavailable();
        }
      } finally {
        client.close();
      }
      await recheck();
      final result = await device.present(() async {
        await recheck();
        // The OS can ask for layout more than once; never repeat the HTTP GET.
        final data = bytes;
        if (closed || data == null) throw const ClinicPrintUnavailable();
        handedOff = true;
        return Uint8List.fromList(data);
      });
      await recheck();
      if (result && !handedOff) throw const ClinicPrintUnavailable();
      return result;
    } finally {
      closed = true;
      final data = bytes;
      if (data != null) data.fillRange(0, data.length, 0);
      _busy[guard] = false;
    }
  }
}
