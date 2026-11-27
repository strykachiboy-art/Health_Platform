import 'dart:convert';

import 'package:cryptography/cryptography.dart';
import 'package:http/http.dart' as http;
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/branding/data/branding_remote.dart';

/// Staged historical verification and thermal stream validation only.
/// No spooler, PDF upload, token logging, file write or automatic retry.
class PrintToolsRemote {
  const PrintToolsRemote({
    required this.api,
    required this.guard,
    required this.loadSession,
    this.staging = brandingStaged,
  });
  final ApiClient api;
  final SessionNavigationGuard guard;
  final Future<AuthSessionState> Function() loadSession;
  final bool staging;
  static final _token = RegExp(r'^[0-9a-f]{32}$');
  static final _hash = RegExp(r'^[0-9a-f]{64}$');
  bool _active(int revision, BrandingActive active) =>
      staging &&
      active() &&
      guard.canAccessProtectedRoutes &&
      guard.revision == revision;
  Future<AuthTokenResponse> _actor(int revision, BrandingActive active) async {
    if (!_active(revision, active)) {
      throw const BrandingUnavailable();
    }
    final state = await loadSession();
    final actor = state.session;
    if (!_active(revision, active) ||
        state.disposition != AuthSessionDisposition.ready ||
        actor == null ||
        actor.userId <= 0 ||
        actor.accessToken.isEmpty ||
        actor.accessToken.trim() != actor.accessToken ||
        actor.refreshToken.isEmpty) {
      throw const BrandingUnavailable();
    }
    return actor;
  }

  static bool _same(AuthTokenResponse a, AuthTokenResponse b) =>
      a.userId == b.userId &&
      a.role == b.role &&
      a.clinicContextId == b.clinicContextId &&
      a.tenantId == b.tenantId &&
      a.accessToken == b.accessToken &&
      a.refreshToken == b.refreshToken;
  static bool thermalRole(AuthTokenResponse a) =>
      const {'admin', 'accountant'}.contains(a.role) &&
      (a.clinicContextId ?? 0) > 0 &&
      a.tenantId != null &&
      validBrandingUuid(a.tenantId!);
  Future<bool> canValidateThermal(BrandingActive active) async =>
      thermalRole(await _actor(guard.revision, active));
  Future<HistoricalVerification> verify(
    String token,
    String? hash,
    BrandingActive active,
  ) async {
    if (!_token.hasMatch(token) ||
        token.length != 32 ||
        (hash != null && (hash.length != 64 || !_hash.hasMatch(hash)))) {
      throw const FormatException(
        'Enter the verification reference and optional PDF SHA-256.',
      );
    }
    final revision = guard.revision;
    final actor = await _actor(revision, active);
    final req =
        http.Request(
            hash == null ? 'GET' : 'POST',
            api.config.endpoint('api/v1/printing/verify/$token'),
          )
          ..followRedirects = false
          ..headers['Accept'] = 'application/json'
          ..headers['Cache-Control'] = 'no-store';
    if (hash != null) {
      req.headers['Content-Type'] = 'application/json';
      req.body = jsonEncode({'pdf_sha256': hash});
    }
    final bytes = await api.sendPrivateBranding(
      req,
      image: false,
      active: () => _active(revision, active),
    );
    if (!_same(actor, await _actor(revision, active))) {
      throw const BrandingUnavailable();
    }
    final env = jsonDecode(utf8.decode(bytes));
    if (env is! Map<String, dynamic> ||
        env.length != 1 ||
        env['data'] is! Map<String, dynamic>) {
      throw const FormatException('Invalid verification response.');
    }
    final data = env['data'] as Map<String, dynamic>;
    final match = data['file_hash_match'];
    if (data.length != 4 ||
        !data.containsKey('file_hash_match') ||
        data['issuance_record_found'] != true ||
        data['status'] != 'historical_issuance_only' ||
        data['notice'] != HistoricalVerification.notice ||
        (hash == null ? match != null : match is! bool)) {
      throw const FormatException('Invalid verification receipt.');
    }
    return HistoricalVerification(match as bool?);
  }

  Future<ThermalValidation> thermal(
    int id,
    int paper,
    BrandingActive active,
  ) async {
    if (id <= 0 || id > 2147483647 || !const {58, 80}.contains(paper)) {
      throw const FormatException('Invalid payment reference or paper size.');
    }
    final revision = guard.revision;
    final actor = await _actor(revision, active);
    if (!thermalRole(actor)) {
      throw const BrandingUnavailable();
    }
    final req =
        http.Request(
            'GET',
            api.config.endpoint(
              'api/v1/printing/thermal/$paper/payment/$id.escpos',
            ),
          )
          ..followRedirects = false
          ..headers['Authorization'] = 'Bearer ${actor.accessToken}'
          ..headers['Accept'] = 'application/octet-stream'
          ..headers['Cache-Control'] = 'no-store';
    final bytes = await api.sendPrivateBranding(
      req,
      image: false,
      active: () => _active(revision, active),
    );
    try {
      if (!_same(actor, await _actor(revision, active))) {
        throw const BrandingUnavailable();
      }
      if (bytes.length < 10 ||
          bytes[0] != 27 ||
          bytes[1] != 64 ||
          bytes.skip(2).any((b) => b != 10 && (b < 32 || b > 126))) {
        throw const FormatException('Unsafe thermal command stream.');
      }
      final text = ascii.decode(bytes.skip(2).toList());
      final lines = text.split('\n');
      if (!text.endsWith('\n\n\n') ||
          lines.any((s) => s.length > (paper == 58 ? 32 : 48)) ||
          !text.startsWith('STRYKA-MAX\nPAYMENT RECORD COPY\n') ||
          !text
              .replaceAll('\n', ' ')
              .contains('NOT A BANK SETTLEMENT CERTIFICATE') ||
          !lines.contains('Payment reference: $id') ||
          !lines.contains('Clinic reference: ${actor.clinicContextId}') ||
          !lines.contains('Payment status: successful')) {
        throw const FormatException(
          'Thermal receipt does not match the authorized request.',
        );
      }
      final hash = (await Sha256().hash(bytes)).bytes
          .map((b) => b.toRadixString(16).padLeft(2, '0'))
          .join();
      if (!_same(actor, await _actor(revision, active))) {
        throw const BrandingUnavailable();
      }
      return ThermalValidation(bytes.length, hash, paper);
    } finally {
      bytes.fillRange(0, bytes.length, 0);
    }
  }
}

class HistoricalVerification {
  const HistoricalVerification(this.hashMatch);
  final bool? hashMatch;
  static const notice =
      'Historical issuance only; does not establish current clinical validity, signature, delivery or payment.';
}

class ThermalValidation {
  const ThermalValidation(this.length, this.sha256, this.paper);
  final int length, paper;
  final String sha256;
}
