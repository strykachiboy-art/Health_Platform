import 'dart:async';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';

import '../data/branding_remote.dart';
import 'branding_studio_editor.dart';

/// Staged branding only. A role link is not permission: edit controls appear
/// only after the server returns an authorized, current branding snapshot.
class BrandingScreen extends StatefulWidget {
  const BrandingScreen({
    super.key,
    required this.source,
    required this.platform,
    this.studio = true,
  });
  final BrandingRemote source;
  final bool platform;
  final bool studio;
  @override
  State<BrandingScreen> createState() => _BrandingScreenState();
}

class _BrandingScreenState extends State<BrandingScreen> {
  BrandingCapabilities? _capabilities;
  BrandingDirectoryPage? _directory;
  BrandingOrganization? _selected;
  final _primary = TextEditingController();
  final _secondary = TextEditingController();
  BrandingView? _view;
  MemoryImage? _image;
  bool _busy = false;
  String? _message;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.source.guard.addListener(_invalidate);
    if (widget.source.staging) {
      unawaited(_discover());
    }
  }

  @override
  void didUpdateWidget(covariant BrandingScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.source, widget.source) ||
        oldWidget.platform != widget.platform ||
        oldWidget.studio != widget.studio) {
      oldWidget.source.guard.removeListener(_invalidate);
      widget.source.guard.addListener(_invalidate);
      _generation++;
      _clear();
      _busy = false;
      _message = null;
      _capabilities = null;
      _directory = null;
      _selected = null;
      if (widget.source.staging) {
        unawaited(_discover());
      }
    }
  }

  void _clear() {
    final bytes = _view?.logo;
    final image = _image;
    if (image != null) {
      unawaited(image.evict());
    }
    if (bytes != null) {
      bytes.fillRange(0, bytes.length, 0);
    }
    _image = null;
    _view = null;
    _primary.clear();
    _secondary.clear();
  }

  void _invalidate() {
    _generation++;
    if (mounted) {
      setState(() {
        _clear();
        _capabilities = null;
        _directory = null;
        _selected = null;
        _busy = false;
        _message = 'Session context changed. Reopen branding.';
      });
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.source.guard.removeListener(_invalidate);
    _clear();
    _primary.dispose();
    _secondary.dispose();
    super.dispose();
  }

  bool _active(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.source.guard.canAccessProtectedRoutes &&
      widget.source.guard.revision == revision;

  String _error(Object error) {
    if (error is ApiException) {
      if (error.statusCode == 403 ||
          error.statusCode == 401 ||
          error.statusCode == 404) {
        return 'Branding is unavailable for this session or target. Backend permission and staging gates apply.';
      }
      if (error.statusCode == 409) {
        return 'Branding changed on the server. Refresh before editing.';
      }
    }
    if (error is FormatException) {
      return error.message;
    }
    return 'Branding could not be verified. Refresh the server state before any further write.';
  }

  void _install(BrandingView view) {
    _clear();
    _view = view;
    final logo = view.logo;
    if (logo != null) {
      _image = MemoryImage(logo);
    }
    _primary.text = view.state.primary ?? '';
    _secondary.text = view.state.secondary ?? '';
  }

  Future<void> _discover({String? after}) async {
    if (_busy || !widget.source.staging) {
      return;
    }
    final generation = ++_generation;
    final revision = widget.source.guard.revision;
    bool active() => _active(generation, revision);
    setState(() {
      _clear();
      _capabilities = null;
      _directory = null;
      _selected = null;
      _busy = true;
      _message = null;
    });
    try {
      if (widget.platform) {
        final result = await widget.source.directory(
          after: after,
          active: active,
        );
        if (active()) {
          setState(() => _directory = result);
        }
      } else {
        final result = await widget.source.capabilities(active: active);
        if (active()) {
          setState(() => _capabilities = result);
        }
      }
    } on Object catch (error) {
      if (active()) {
        setState(() => _message = _error(error));
      }
    } finally {
      if (active()) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _load(BrandingScope scope) async {
    if (_busy || !widget.source.staging) {
      return;
    }
    final target = widget.platform ? _selected?.id : null;
    if (widget.platform
        ? target == null
        : (scope == BrandingScope.organization
              ? _capabilities?.organization != true
              : _capabilities?.clinic != true)) {
      return;
    }
    final generation = ++_generation;
    final revision = widget.source.guard.revision;
    bool active() => _active(generation, revision);
    setState(() {
      _clear();
      _busy = true;
      _message = null;
    });
    try {
      if (target != null) {
        final detail = await widget.source.organizationDetail(
          target,
          active: active,
        );
        if (!active()) {
          return;
        }
        setState(() => _selected = detail);
      } else {
        final caps = await widget.source.capabilities(active: active);
        if (!active()) {
          return;
        }
        setState(() => _capabilities = caps);
        if (scope == BrandingScope.organization
            ? !caps.organization
            : !caps.clinic) {
          throw const BrandingUnavailable();
        }
      }
      var result = await widget.source.read(
        scope,
        organization: target,
        active: active,
      );
      if (!active()) {
        result.logo?.fillRange(0, result.logo!.length, 0);
        return;
      }
      if (widget.studio && !widget.platform) {
        result = await widget.source.attachStudio(result, active);
        if (!active()) {
          result.logo?.fillRange(0, result.logo!.length, 0);
          return;
        }
      }
      setState(() => _install(result));
    } on Object catch (error) {
      if (active()) {
        setState(() => _message = _error(error));
      }
    } finally {
      if (active()) {
        setState(() => _busy = false);
      }
    }
  }

  Future<bool> _confirm(String description) async =>
      await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Confirm branding change'),
          content: Text(
            '$description\nOne online request. No automatic retry or offline queue.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Confirm'),
            ),
          ],
        ),
      ) ==
      true;

  Future<void> _write(
    String description,
    Future<BrandingView> Function(BrandingState, BrandingActive) change, {
    bool chooseLogo = false,
  }) async {
    final state = _view?.state;
    if (state == null || _busy || !widget.source.staging) {
      return;
    }
    final generation = ++_generation;
    final revision = widget.source.guard.revision;
    bool active() => _active(generation, revision);
    Uint8List? fileBytes;
    var attempted = false;
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      if (chooseLogo) {
        final file = await FilePicker.pickFile(
          type: FileType.custom,
          allowedExtensions: ['png', 'jpg', 'jpeg'],
        );
        if (!active() || file == null) {
          return;
        }
        final length = await file.length();
        if (length == null || length <= 0 || length > BrandingRemote.maxLogo) {
          throw const FormatException(
            'Logo size must be known, non-empty and at most 2 MiB.',
          );
        }
        if (!active()) {
          return;
        }
        fileBytes = await file.readAsBytes();
        BrandingRemote.logoMime(fileBytes);
      }
      if (!active()) {
        return;
      }
      final approved = await _confirm(
        '$description\n${state.name} · version ${state.version}',
      );
      if (!approved || !active()) {
        return;
      }
      attempted = true;
      var result = chooseLogo
          ? await widget.source.replaceLogo(state, fileBytes!, active)
          : await change(state, active);
      if (!active()) {
        result.logo?.fillRange(0, result.logo!.length, 0);
        return;
      }
      if (widget.studio && !widget.platform && result.studio == null) {
        result = await widget.source.attachStudio(result, active);
        if (!active()) {
          result.logo?.fillRange(0, result.logo!.length, 0);
          return;
        }
      }
      setState(() {
        _install(result);
        _message = 'Server confirmed and read back the branding change.';
      });
    } on Object catch (error) {
      if (active()) {
        setState(() {
          if (attempted) {
            _clear();
          } // No stale or ambiguous write may be resubmitted.
          _message = _error(error);
        });
      }
    } finally {
      final bytes = fileBytes;
      if (bytes != null) {
        bytes.fillRange(0, bytes.length, 0);
      }
      if (active()) {
        setState(() => _busy = false);
      }
    }
  }

  void _colors() {
    final primary = _primary.text.trim().toUpperCase();
    final secondary = _secondary.text.trim().toUpperCase();
    if (!validBrandingColor(primary) || !validBrandingColor(secondary)) {
      setState(
        () => _message = 'Both colours must be #RRGGBB and have at least 4.5:1 contrast against white.',
      );
      return;
    }
    unawaited(
      _write(
        'Apply $primary and $secondary.',
        (state, active) =>
            widget.source.setColors(state, primary, secondary, active),
      ),
    );
  }

  Color _color(String value) =>
      Color(0xff000000 | int.parse(value.substring(1), radix: 16));
  Future<void> _chooseColour(
    TextEditingController controller,
    String label,
  ) async {
    final state = _view?.state;
    if (_busy ||
        widget.platform ||
        !widget.source.staging ||
        state == null ||
        (state.scope == BrandingScope.clinic &&
            (!state.overrideAllowed || _capabilities?.customize != true))) {
      return;
    }
    final guard = widget.source.guard;
    final generation = _generation;
    final revision = guard.revision;
    bool active() => _active(generation, revision) && !_busy;
    if (!active()) {
      return;
    }
    final current = controller.text.trim().toUpperCase();
    final fallback = identical(controller, _primary)
        ? state.primary!
        : state.secondary!;
    var hsv = HSVColor.fromColor(
      _color(validBrandingColor(current) ? current : fallback),
    );
    String hex() =>
        '#${(hsv.toColor().toARGB32() & 0xffffff).toRadixString(16).padLeft(6, '0').toUpperCase()}';
    const presets = <String, String>{
      'Hospital teal': '#176B65',
      'Slate navy': '#243D54',
      'Royal blue': '#1D4ED8',
      'Forest green': '#166534',
      'Deep cyan': '#155E75',
      'Plum': '#6B21A8',
      'Burgundy': '#881337',
      'Warm brown': '#8A4B26',
      'Charcoal': '#374151',
      'Black': '#000000',
    };
    final chosen = await showDialog<String>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, update) => ListenableBuilder(
          listenable: guard,
          builder: (context, child) {
            if (!active()) {
              return AlertDialog(
                title: const Text('Colour selection unavailable'),
                content: const Text(
                  'Session context changed. Reopen branding.',
                ),
                actions: [
                  TextButton(
                    onPressed: () => Navigator.pop(dialogContext),
                    child: const Text('Close'),
                  ),
                ],
              );
            }
            final value = hex();
            final valid = validBrandingColor(value);
            return AlertDialog(
              title: Text('Choose ${label.toLowerCase()}'),
              scrollable: true,
              content: SizedBox(
                width: 360,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text('Professional colours'),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final entry in presets.entries)
                          OutlinedButton(
                            key: ValueKey('branding-swatch-${entry.value}'),
                            onPressed: () => update(
                              () =>
                                  hsv = HSVColor.fromColor(_color(entry.value)),
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(
                                  Icons.circle,
                                  color: _color(entry.value),
                                  size: 18,
                                ),
                                const SizedBox(width: 4),
                                Text(entry.key),
                              ],
                            ),
                          ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    const Text('Custom colour'),
                    Text('Hue: ${hsv.hue.round()}°'),
                    Slider(
                      key: const ValueKey('branding-hue'),
                      value: hsv.hue,
                      max: 360,
                      activeColor: HSVColor.fromAHSV(
                        1,
                        hsv.hue,
                        1,
                        1,
                      ).toColor(),
                      semanticFormatterCallback: (v) =>
                          '${v.round()} degrees hue',
                      onChanged: (v) => update(() => hsv = hsv.withHue(v)),
                    ),
                    Text('Saturation: ${(hsv.saturation * 100).round()}%'),
                    Slider(
                      key: const ValueKey('branding-saturation'),
                      value: hsv.saturation,
                      semanticFormatterCallback: (v) =>
                          '${(v * 100).round()} percent saturation',
                      onChanged: (v) =>
                          update(() => hsv = hsv.withSaturation(v)),
                    ),
                    Text('Brightness: ${(hsv.value * 100).round()}%'),
                    Slider(
                      key: const ValueKey('branding-brightness'),
                      value: hsv.value,
                      semanticFormatterCallback: (v) =>
                          '${(v * 100).round()} percent brightness',
                      onChanged: (v) => update(() => hsv = hsv.withValue(v)),
                    ),
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: hsv.toColor(),
                        border: Border.all(color: Colors.grey),
                      ),
                      child: Text(
                        value,
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          color: valid ? Colors.white : Colors.black,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      valid ? 'Readable on white (4.5:1 or higher).' : 'Too light for document headings. Reduce brightness.',
                      key: const ValueKey('branding-picker-contrast'),
                    ),
                  ],
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(dialogContext),
                  child: const Text('Cancel'),
                ),
                FilledButton(
                  key: const ValueKey('branding-use-colour'),
                  onPressed: valid
                      ? () => Navigator.pop(dialogContext, value)
                      : null,
                  child: const Text('Use colour'),
                ),
              ],
            );
          },
        ),
      ),
    );
    if (chosen == null || !active() || !validBrandingColor(chosen)) {
      return;
    }
    setState(() {
      controller.text = chosen;
      _message = null;
    });
  }

  Widget _paletteField(
    TextEditingController controller,
    String label,
    bool enabled,
  ) {
    final value = controller.text.trim().toUpperCase();
    final valid = validBrandingColor(value);
    return TextField(
      controller: controller,
      enabled: enabled,
      maxLength: 7,
      decoration: InputDecoration(
        labelText: label,
        hintText: '#176B65',
        border: const OutlineInputBorder(),
        suffixIcon: TextButton.icon(
          key: ValueKey('branding-picker-$label'),
          onPressed: enabled
              ? () => unawaited(_chooseColour(controller, label))
              : null,
          icon: Icon(
            Icons.palette_outlined,
            color: enabled && valid ? _color(value) : null,
          ),
          label: const Text('Pick'),
        ),
      ),
      onChanged: (_) => setState(() {}),
    );
  }

  Widget _sample(BrandingState state) {
    final a = _primary.text.trim();
    final b = _secondary.text.trim();
    final primary = _color(validBrandingColor(a) ? a : state.primary!);
    final secondary = _color(validBrandingColor(b) ? b : state.secondary!);
    final clinic = state.scope == BrandingScope.clinic;
    final organizationName = clinic
        ? (_capabilities?.name ?? 'Sample organisation')
        : state.name;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('Branding sample — not an actual clinical PDF'),
            const SizedBox(height: 8),
            const Text(
              'Primary: letterhead, document title and footer. Secondary: clinic name, section headings, dividers and table headers.',
            ),
            const SizedBox(height: 8),
            Container(
              key: const ValueKey('branding-paper-sample'),
              color: Colors.white,
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Container(
                    key: const ValueKey('branding-sample-stripe'),
                    height: 4,
                    color: primary,
                  ),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      if (_image != null)
                        Padding(
                          padding: const EdgeInsets.only(right: 12),
                          child: Image(
                            image: _image!,
                            width: 64,
                            height: 64,
                            fit: BoxFit.contain,
                            errorBuilder: (_, _, _) => const Icon(
                              Icons.broken_image_outlined,
                              color: Color(0xff536575),
                            ),
                          ),
                        ),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              organizationName,
                              key: const ValueKey(
                                'branding-sample-organization',
                              ),
                              style: TextStyle(
                                color: primary,
                                fontSize: 20,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                            Text(
                              clinic ? state.name : 'Sample clinic',
                              key: const ValueKey('branding-sample-clinic'),
                              style: TextStyle(
                                color: secondary,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  const Divider(color: Color(0xffd6dfe6), height: 1),
                  const SizedBox(height: 8),
                  Text(
                    'Sample document title',
                    key: const ValueKey('branding-sample-title'),
                    style: TextStyle(
                      color: primary,
                      fontSize: 20,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 6),
                  const ColoredBox(
                    color: Color(0xfffcf2f1),
                    child: Padding(
                      padding: EdgeInsets.all(8),
                      child: Text(
                        'Safety warning — fixed colour',
                        key: ValueKey('branding-sample-warning'),
                        style: TextStyle(
                          color: Color(0xff842020),
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Sample section heading',
                    key: const ValueKey('branding-sample-section'),
                    style: TextStyle(
                      color: secondary,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Container(
                    key: const ValueKey('branding-sample-divider'),
                    height: 1,
                    color: secondary,
                  ),
                  const SizedBox(height: 6),
                  Container(
                    key: const ValueKey('branding-sample-table'),
                    color: secondary,
                    padding: const EdgeInsets.all(8),
                    child: const Text(
                      'Sample document table heading',
                      style: TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                  const ColoredBox(
                    color: Color(0xfff1f5f7),
                    child: Padding(
                      padding: EdgeInsets.all(8),
                      child: Text(
                        'Sample document content',
                        key: ValueKey('branding-sample-body'),
                        style: TextStyle(color: Color(0xff142a3a)),
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Container(
                    key: const ValueKey('branding-sample-footer'),
                    height: 1,
                    color: primary,
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = _view?.state;
    final allowed =
        state != null &&
        (state.scope != BrandingScope.clinic ||
            (state.overrideAllowed && _capabilities?.customize == true));
    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.platform ? 'Branding entitlements' : 'Document branding',
        ),
      ),
      body: !widget.source.staging
          ? const Center(
              child: Text('Branding is locked pending release certification.'),
            )
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                const Text(
                  'Staged only. The backend verifies membership, clinic grants and every change.',
                ),
                const SizedBox(height: 12),
                if (widget.platform) ...[
                  OutlinedButton(
                    onPressed: _busy ? null : () => unawaited(_discover()),
                    child: const Text('Refresh organisations'),
                  ),
                  if (_directory != null) ...[
                    for (final org in _directory!.items)
                      ListTile(
                        key: ValueKey('branding-org-${org.id}'),
                        title: Text(org.name),
                        subtitle: Text(
                          org.allowed
                              ? 'Customization enabled'
                              : 'Customization not entitled',
                        ),
                        selected: _selected?.id == org.id,
                        onTap: _busy
                            ? null
                            : () {
                                setState(() => _selected = org);
                                unawaited(_load(BrandingScope.entitlement));
                              },
                      ),
                    if (_directory!.next != null)
                      OutlinedButton(
                        onPressed: _busy
                            ? null
                            : () =>
                                  unawaited(_discover(after: _directory!.next)),
                        child: const Text('Next organisations'),
                      ),
                  ],
                ] else ...[
                  OutlinedButton(
                    onPressed: _busy ? null : () => unawaited(_discover()),
                    child: const Text('Refresh permissions'),
                  ),
                  if (_capabilities != null)
                    Text(
                      '${_capabilities!.name} · ${_capabilities!.membershipRole}',
                    ),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      OutlinedButton(
                        key: const ValueKey('branding-read-organization'),
                        onPressed: _busy || _capabilities?.organization != true
                            ? null
                            : () =>
                                  unawaited(_load(BrandingScope.organization)),
                        child: const Text('Organisation defaults'),
                      ),
                      OutlinedButton(
                        key: const ValueKey('branding-read-clinic'),
                        onPressed: _busy || _capabilities?.clinic != true
                            ? null
                            : () => unawaited(_load(BrandingScope.clinic)),
                        child: const Text('Current clinic'),
                      ),
                    ],
                  ),
                  const Text(
                    'Organisation defaults require a verified organisation-admin membership, not just the Admin role.',
                  ),
                ],
                if (_busy) const LinearProgressIndicator(),
                if (_message != null)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 10),
                    child: Text(
                      _message!,
                      key: const ValueKey('branding-notice'),
                    ),
                  ),
                if (state != null) ...[
                  const Divider(),
                  Text(
                    widget.platform
                        ? (_selected?.name ?? state.name)
                        : state.name,
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  Text('Verified version ${state.version}'),
                  Text(
                    state.overrideAllowed
                        ? 'Clinic customization enabled'
                        : 'Clinic customization not entitled',
                  ),
                  if (widget.platform) ...[
                    FilledButton(
                      onPressed: _busy
                          ? null
                          : () => unawaited(
                              _write(
                                state.overrideAllowed
                                    ? 'Revoke clinic customization.'
                                    : 'Enable clinic customization.',
                                (prior, active) => widget.source.setEntitlement(
                                  prior,
                                  !prior.overrideAllowed,
                                  active,
                                ),
                              ),
                            ),
                      child: Text(
                        state.overrideAllowed
                            ? 'Revoke customization'
                            : 'Enable customization',
                      ),
                    ),
                    const Text(
                      'Platform entitlement only. No logo access or paid-plan enforcement.',
                    ),
                  ] else ...[
                    Text(
                      'Logo: ${state.logoSource} · Colours: ${state.paletteSource}',
                    ),
                    if (widget.studio) ...[
                      if (_view?.studio != null)
                        BrandingStudioEditor(
                          key: ObjectKey(_view),
                          view: _view!,
                          image: _image,
                          organizationName: state.scope == BrandingScope.clinic
                              ? (_capabilities?.name ?? 'Sample organisation')
                              : state.name,
                          enabled: !_busy,
                          canCustomize: allowed,
                          onSave: (draft) {
                            final prior = _view!;
                            unawaited(
                              _write(
                                'Apply the complete branding studio palette and output mode.',
                                (_, active) => widget.source.setStudio(
                                  prior,
                                  draft,
                                  active,
                                ),
                              ),
                            );
                          },
                        ),
                    ] else ...[
                      _sample(state),
                      _paletteField(
                        _primary,
                        'Primary colour',
                        !_busy && allowed,
                      ),
                      _paletteField(
                        _secondary,
                        'Secondary colour',
                        !_busy && allowed,
                      ),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          FilledButton(
                            key: const ValueKey('branding-save-colors'),
                            onPressed: _busy || !allowed ? null : _colors,
                            child: const Text('Save colours'),
                          ),
                          OutlinedButton(
                            onPressed: _busy
                                ? null
                                : () => unawaited(
                                    _write(
                                      'Reset this palette to inherited or default colours.',
                                      (prior, active) => widget.source
                                          .setColors(prior, null, null, active),
                                    ),
                                  ),
                            child: const Text('Reset colours'),
                          ),
                        ],
                      ),
                    ],
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        FilledButton(
                          onPressed: _busy || !allowed
                              ? null
                              : () => unawaited(
                                  _write(
                                    'Replace this scope’s logo.',
                                    (_, _) => throw const BrandingUnavailable(),
                                    chooseLogo: true,
                                  ),
                                ),
                          child: const Text('Choose and upload logo'),
                        ),
                        OutlinedButton(
                          onPressed: _busy
                              ? null
                              : () => unawaited(
                                  _write(
                                    'Remove this scope’s logo and inherit where applicable.',
                                    (prior, active) =>
                                        widget.source.clearLogo(prior, active),
                                  ),
                                ),
                          child: const Text('Remove / inherit logo'),
                        ),
                      ],
                    ),
                    const Text(
                      'Reset remains available after entitlement revocation. Actual clinical PDFs are produced by Flask/Rust, never by this sample.',
                    ),
                  ],
                ],
              ],
            ),
    );
  }
}
