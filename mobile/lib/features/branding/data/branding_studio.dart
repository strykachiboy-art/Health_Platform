import 'dart:math' as math;

/// Exact staged Flask contract; no storage or authorization on the client.
const studioDefaults = <String, String>{
  'primary_color': '#17354A',
  'secondary_color': '#475569',
  'text_color': '#142A3A',
  'warning_color': '#842020',
  'background_color': '#FFFFFF',
  'output_mode': 'full',
  'logo_size': 'medium',
};
const studioModes = ['full', 'ink_saving', 'monochrome'];
const studioLogoSizes = ['small', 'medium', 'large'];
bool studioValue(String key, String value) => key == 'output_mode'
    ? studioModes.contains(value)
    : key == 'logo_size'
    ? studioLogoSizes.contains(value)
    : studioHex(value);
final _hex = RegExp(r'^#[0-9A-F]{6}$');
bool studioHex(String value) => value.length == 7 && _hex.hasMatch(value);
List<int> studioRgb(String value) => [
  for (final i in [1, 3, 5]) int.parse(value.substring(i, i + 2), radix: 16),
];
double studioLuminance(String value) {
  final rgb = studioRgb(value).map((n) {
    final x = n / 255.0;
    return x <= .04045
        ? x / 12.92
        : math.pow((x + .055) / 1.055, 2.4).toDouble();
  }).toList();
  return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2];
}

double studioContrast(String a, String b) {
  final x = studioLuminance(a), y = studioLuminance(b);
  return (math.max(x, y) + .05) / (math.min(x, y) + .05);
}

bool studioWarningHue(String value) {
  final rgb = studioRgb(value).map((n) => n / 255.0).toList();
  final high = rgb.reduce((a, b) => math.max(a, b));
  final low = rgb.reduce((a, b) => math.min(a, b));
  final d = high - low;
  if (high == 0 || d == 0 || d / high < .35) {
    return false;
  }
  final h =
      60 *
      (high == rgb[0]
          ? ((rgb[1] - rgb[2]) / d) % 6
          : high == rgb[1]
          ? (rgb[2] - rgb[0]) / d + 2
          : (rgb[0] - rgb[1]) / d + 4);
  return h <= 50 || h >= 330;
}

String? studioValidation(Map<String, String> p) {
  if (p.length != studioDefaults.length ||
      !studioDefaults.keys.every(p.containsKey) ||
      p.entries.any((e) => !studioValue(e.key, e.value))) {
    return 'Enter complete six-digit HEX colours.';
  }
  final paper = p['background_color']!;
  bool enough(String key, List<String> backgrounds, double min) =>
      backgrounds.every((b) => studioContrast(p[key]!, b) >= min);
  if (studioLuminance(paper) < .80) {
    return 'Paper background must remain light.';
  }
  for (final key in ['primary_color', 'secondary_color']) {
    if (!enough(key, [paper, '#FFFFFF'], 4.5)) {
      return 'Primary and secondary need 4.5:1 contrast.';
    }
  }
  if (!enough('text_color', [paper, '#FFFFFF', '#F1F5F7'], 7)) {
    return 'Document text needs 7:1 contrast.';
  }
  if (!studioWarningHue(p['warning_color']!)) {
    return 'Warning colour must be recognizable as caution.';
  }
  if (!enough('warning_color', [paper, '#FFFFFF', '#FCF2F1'], 4.5)) {
    return 'Warning text needs 4.5:1 contrast.';
  }
  return null;
}

bool studioEqual(Map<String, String> a, Map<String, String> b) =>
    a.length == b.length && a.entries.every((e) => b[e.key] == e.value);
String studioPaint(String value, String mode) {
  if (mode != 'monochrome') {
    return value;
  }
  final rgb = studioRgb(value);
  var n = (299 * rgb[0] + 587 * rgb[1] + 114 * rgb[2] + 500) ~/ 1000;
  if (n < 128) {
    n = 0;
  }
  final h = n.toRadixString(16).padLeft(2, '0').toUpperCase();
  return '#$h$h$h';
}

class BrandingStudioState {
  BrandingStudioState._(
    this.organizationId,
    this.clinicId,
    this.version,
    this.organizationVersion,
    this.allowed,
    this.local,
    this.inherited,
    this.effective,
    this.sources,
  );
  final String organizationId;
  final int? clinicId;
  final int version, organizationVersion;
  final bool allowed;
  final Map<String, String> local, inherited, effective, sources;
  static Map<String, String> _map(
    Object? raw, {
    bool complete = false,
    bool source = false,
  }) {
    if (raw is! Map<String, dynamic> ||
        (complete && raw.length != studioDefaults.length)) {
      throw const FormatException('Invalid studio palette.');
    }
    final result = <String, String>{};
    for (final e in raw.entries) {
      final v = e.value;
      if (!studioDefaults.containsKey(e.key) ||
          v is! String ||
          (source
              ? !const ['default', 'organization', 'clinic'].contains(v)
              : !studioValue(e.key, v))) {
        throw const FormatException('Invalid studio value.');
      }
      result[e.key] = v;
    }
    return Map.unmodifiable(result);
  }

  factory BrandingStudioState.parse(
    Map<String, dynamic> data, {
    required String organizationId,
    required int? clinicId,
  }) {
    const keys = {
      'organization_id',
      'clinic_id',
      'version',
      'organization_version',
      'clinic_override_allowed',
      'local',
      'inherited',
      'effective',
      'sources',
    };
    final v = data['version'],
        ov = data['organization_version'],
        allowed = data['clinic_override_allowed'];
    if (data.length != keys.length ||
        !keys.every(data.containsKey) ||
        data['organization_id'] != organizationId ||
        data['clinic_id'] != clinicId ||
        v is! int ||
        v < 0 ||
        v > 2147483647 ||
        ov is! int ||
        ov < 0 ||
        ov > 2147483647 ||
        allowed is! bool ||
        (clinicId == null && v != ov)) {
      throw const FormatException('Studio identity or version mismatch.');
    }
    final local = _map(data['local']);
    final inherited = _map(data['inherited'], complete: true);
    final effective = _map(data['effective'], complete: true);
    final sources = _map(data['sources'], complete: true, source: true);
    final applied = clinicId == null || allowed;
    if (studioValidation(inherited) != null ||
        studioValidation(effective) != null ||
        !studioEqual(effective, {...inherited, if (applied) ...local}) ||
        (clinicId == null && !studioEqual(inherited, studioDefaults))) {
      throw const FormatException('Studio inheritance mismatch.');
    }
    for (final k in studioDefaults.keys) {
      final actual = sources[k];
      if (clinicId == null) {
        if (actual != (local.containsKey(k) ? 'organization' : 'default')) {
          throw const FormatException('Studio source mismatch.');
        }
      } else if ((actual == 'clinic') != (allowed && local.containsKey(k))) {
        throw const FormatException('Studio source mismatch.');
      }
    }
    return BrandingStudioState._(
      organizationId,
      clinicId,
      v,
      ov,
      allowed,
      local,
      inherited,
      effective,
      sources,
    );
  }
  Map<String, String> draft(Map<String, String> overrides) => {
    ...inherited,
    if (clinicId == null || allowed) ...overrides,
  };
  bool accepts(Map<String, String> overrides) =>
      overrides.entries.every(
        (e) => studioDefaults.containsKey(e.key) && studioValue(e.key, e.value),
      ) &&
      (clinicId == null ||
          allowed ||
          overrides.entries.every((e) => local[e.key] == e.value)) &&
      studioValidation(draft(overrides)) == null;
}
