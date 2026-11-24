/// Additive, read-only clinic operating-hours metadata from the staged backend.
/// Never computes access, appointment availability or live status on the client.
class ClinicHoursStatus {
  const ClinicHoursStatus._(
    this.mode,
    this.isOpenNow,
    this.nextOpeningAt,
    this.openingTime,
    this.closingTime,
    this.timezone,
  );

  static const _modes = <String>{'unspecified', 'always_open', 'scheduled'};

  final String mode;
  final bool? isOpenNow;
  final String? nextOpeningAt;
  final String? openingTime;
  final String? closingTime;
  final String? timezone;

  static ClinicHoursStatus? fromJson(Map<String, dynamic> json) {
    final mode = json['operating_mode'];
    final open = json['is_open_now'];
    final next = json['next_opening_at'];
    if (mode == null && open == null && next == null) return null;
    if (mode is! String ||
        !_modes.contains(mode) ||
        (open != null && open is! bool)) {
      throw const FormatException('Invalid clinic hours metadata.');
    }
    if (next != null &&
        (next is! String ||
            !RegExp(
              r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$',
            ).hasMatch(next) ||
            DateTime.tryParse(next) == null)) {
      throw const FormatException('Invalid next opening instant.');
    }
    if ((mode == 'unspecified' && (open != null || next != null)) ||
        (mode == 'always_open' && (open == false || next != null)) ||
        (next != null && open != false)) {
      throw const FormatException('Inconsistent clinic hours metadata.');
    }
    String? optionalText(String key) {
      final value = json[key];
      if (value == null) return null;
      if (value is! String) {
        throw const FormatException('Invalid clinic hours text.');
      }
      return value;
    }

    return ClinicHoursStatus._(
      mode,
      open as bool?,
      next as String?,
      optionalText('opening_time'),
      optionalText('closing_time'),
      optionalText('timezone'),
    );
  }

  String get summary {
    final hours = switch (mode) {
      'always_open' => '24/7 schedule',
      'unspecified' => 'Hours not configured',
      _ => 'Scheduled hours: ${openingTime ?? '—'} – ${closingTime ?? '—'}',
    };
    final state = switch (isOpenNow) {
      true => 'Open at server read',
      false => 'Closed at server read',
      null => 'Current hours status unknown',
    };
    return '$hours · $state'
        '${timezone == null ? '' : ' · $timezone'}'
        '${nextOpeningAt == null ? '' : ' · Next opening (UTC): $nextOpeningAt'}';
  }
}
