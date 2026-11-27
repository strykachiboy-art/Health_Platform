import 'dart:async';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';

import '../data/branding_remote.dart';

/// Staged branding only. A role link is not permission: edit controls appear
/// only after the server returns an authorized, current branding snapshot.
class BrandingScreen extends StatefulWidget {
  const BrandingScreen({
    super.key,
    required this.source,
    required this.platform,
  });
  final BrandingRemote source;
  final bool platform;
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
        oldWidget.platform != widget.platform) {
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
      final result = await widget.source.read(
        scope,
        organization: target,
        active: active,
      );
      if (!active()) {
        result.logo?.fillRange(0, result.logo!.length, 0);
        return;
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
      final result = chooseLogo
          ? await widget.source.replaceLogo(state, fileBytes!, active)
          : await change(state, active);
      if (!active()) {
        result.logo?.fillRange(0, result.logo!.length, 0);
        return;
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
  Widget _paletteField(
    TextEditingController controller,
    String label,
    bool enabled,
  ) => TextField(
    controller: controller,
    enabled: enabled,
    maxLength: 7,
    decoration: InputDecoration(
      labelText: label,
      hintText: '#176B65',
      border: const OutlineInputBorder(),
    ),
    onChanged: (_) => setState(() {}),
  );

  Widget _sample(BrandingState state) {
    final a = _primary.text.trim();
    final b = _secondary.text.trim();
    final primary = _color(validBrandingColor(a) ? a : state.primary!);
    final secondary = _color(validBrandingColor(b) ? b : state.secondary!);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('Branding sample — not an actual clinical PDF'),
            const SizedBox(height: 12),
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
                      errorBuilder: (_, _, _) =>
                          const Icon(Icons.broken_image_outlined),
                    ),
                  ),
                Expanded(
                  child: Text(
                    state.name,
                    style: TextStyle(
                      color: primary,
                      fontSize: 20,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Container(height: 4, color: primary),
            const SizedBox(height: 8),
            Container(
              color: secondary,
              padding: const EdgeInsets.all(12),
              child: const Text(
                'Sample document table heading',
                style: TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.bold,
                ),
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
                                    (prior, active) => widget.source.setColors(
                                      prior,
                                      null,
                                      null,
                                      active,
                                    ),
                                  ),
                                ),
                          child: const Text('Reset colours'),
                        ),
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
