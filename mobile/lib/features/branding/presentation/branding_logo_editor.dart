import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../data/branding_logo_pixels.dart';

/// The dialog works on a draft. It returns bytes, not a server mutation.
class BrandingLogoEditor extends StatefulWidget {
  const BrandingLogoEditor({
    super.key,
    required this.original,
    required this.raw,
    required this.paper,
    required this.size,
    required this.active,
    required this.session,
    this.mode = 'full',
    this.primary = const Color(0xff17354a),
    this.secondary = const Color(0xff475569),
  });
  final LogoPixels original;
  final Uint8List raw;
  final Color paper;
  final String size, mode;
  final Color primary, secondary;
  final bool Function() active;
  final Listenable session;
  @override
  State<BrandingLogoEditor> createState() => _BrandingLogoEditorState();
}

class _BrandingLogoEditorState extends State<BrandingLogoEditor> {
  bool _remove = false, _crop = true;
  int _background = 0xffffff, _tolerance = 30;
  Uint8List? _edited;
  MemoryImage? _editedImage;
  late final MemoryImage _originalImage;
  LogoPixels? _pixels;
  String? _error;
  late final Uint8List _originalPng;
  bool _invalid = false;
  @override
  void initState() {
    super.initState();
    _originalPng = widget.original.png();
    _originalImage = MemoryImage(_originalPng);
    widget.session.addListener(_sessionChanged);
    _rebuild();
  }

  void _sessionChanged() {
    if (widget.active() || !mounted || _invalid) {
      return;
    }
    setState(() {
      _invalid = true;
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && ModalRoute.of(context)?.isCurrent == true) {
        Navigator.of(context).pop();
      }
    });
  }

  void _rebuild() {
    final old = _editedImage;
    if (old != null) {
      unawaited(old.evict());
    }
    _editedImage = null;
    _edited = null;
    _pixels = null;
    _error = null;
    try {
      final pixels = widget.original.edit(
        remove: _remove,
        background: _background,
        tolerance: _tolerance,
        crop: _crop,
      );
      _pixels = pixels;
      _edited = pixels.png();
      _editedImage = MemoryImage(_edited!);
    } on FormatException catch (e) {
      _error = e.message;
    }
  }

  void _update(VoidCallback change) {
    if (_invalid || !widget.active()) {
      return;
    }
    setState(() {
      change();
      _rebuild();
    });
  }

  @override
  void dispose() {
    widget.session.removeListener(_sessionChanged);
    unawaited(_originalImage.evict());
    final image = _editedImage;
    if (image != null) {
      unawaited(image.evict());
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_invalid || !widget.active()) {
      return const AlertDialog(
        content: Text('Session changed. Logo editing closed.'),
      );
    }
    final original = widget.original;
    final pixels = _pixels;
    final edited = _edited;
    final fit = pixels == null
        ? null
        : logoFit(pixels.width, pixels.height, widget.size);
    final sampleSize = logoFit(original.width, original.height, 'large');
    Widget? editedPreview;
    if (edited != null && fit != null) {
      editedPreview = Image(
        image: _editedImage!,
        key: const ValueKey('logo-edited-preview'),
        width: fit.width,
        height: fit.height,
        fit: BoxFit.contain,
      );
      if (widget.mode == 'monochrome') {
        editedPreview = ColorFiltered(
          colorFilter: const ColorFilter.matrix([
            .299,
            .587,
            .114,
            0,
            0,
            .299,
            .587,
            .114,
            0,
            0,
            .299,
            .587,
            .114,
            0,
            0,
            0,
            0,
            0,
            1,
            0,
          ]),
          child: editedPreview,
        );
      }
    }
    return AlertDialog(
      title: const Text('Edit hospital logo'),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                'Tap the original image to sample its background. Only matching pixels connected to the edge are removed. Review the result before uploading.',
              ),
              const SizedBox(height: 12),
              LayoutBuilder(
                builder: (context, constraints) {
                  final width = constraints.maxWidth >= 450
                      ? (constraints.maxWidth - 12) / 2
                      : constraints.maxWidth;
                  return Wrap(
                    spacing: 12,
                    runSpacing: 12,
                    children: [
                      SizedBox(
                        width: width,
                        child: Column(
                          children: [
                            const Text('Original — tap to sample'),
                            const SizedBox(height: 8),
                            GestureDetector(
                              key: const ValueKey('logo-sample'),
                              onTapDown: (details) {
                                final x =
                                    (details.localPosition.dx /
                                            sampleSize.width *
                                            original.width)
                                        .floor()
                                        .clamp(0, original.width - 1)
                                        .toInt();
                                final y =
                                    (details.localPosition.dy /
                                            sampleSize.height *
                                            original.height)
                                        .floor()
                                        .clamp(0, original.height - 1)
                                        .toInt();
                                _update(() {
                                  _background = original.rgbAt(x, y);
                                  _remove = true;
                                });
                              },
                              child: SizedBox(
                                width: sampleSize.width,
                                height: sampleSize.height,
                                child: Image(
                                  image: _originalImage,
                                  fit: BoxFit.fill,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                      SizedBox(
                        width: width,
                        child: Column(
                          children: [
                            const Text('Edited — paper preview'),
                            const SizedBox(height: 8),
                            Container(
                              key: const ValueKey('logo-paper-preview'),
                              color: widget.paper,
                              padding: const EdgeInsets.all(12),
                              child: Row(
                                children: [
                                  ?editedPreview,
                                  const SizedBox(width: 12),
                                  Expanded(
                                    child: Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      children: [
                                        Text(
                                          'Hospital name',
                                          style: TextStyle(
                                            color: widget.primary,
                                            fontWeight: FontWeight.bold,
                                          ),
                                        ),
                                        Text(
                                          'Clinic address',
                                          style: TextStyle(
                                            color: widget.secondary,
                                            fontSize: 11,
                                          ),
                                        ),
                                      ],
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  );
                },
              ),
              CheckboxListTile(
                key: const ValueKey('logo-remove-background'),
                contentPadding: EdgeInsets.zero,
                title: const Text('Remove solid-colour background'),
                value: _remove,
                onChanged: (v) => _update(() => _remove = v ?? false),
              ),
              Wrap(
                crossAxisAlignment: WrapCrossAlignment.center,
                spacing: 8,
                children: [
                  Container(
                    width: 24,
                    height: 24,
                    decoration: BoxDecoration(
                      color: Color(0xff000000 | _background),
                      border: Border.all(color: Colors.grey),
                    ),
                  ),
                  Text(
                    '#${_background.toRadixString(16).padLeft(6, '0').toUpperCase()}',
                  ),
                  TextButton(
                    onPressed: () => _update(() {
                      _background = 0xffffff;
                      _remove = true;
                    }),
                    child: const Text('White'),
                  ),
                  TextButton(
                    onPressed: () => _update(() {
                      _background = 0;
                      _remove = true;
                    }),
                    child: const Text('Black'),
                  ),
                ],
              ),
              Text('Removal tolerance: $_tolerance'),
              Slider(
                key: const ValueKey('logo-tolerance'),
                value: _tolerance.toDouble(),
                min: 0,
                max: 100,
                divisions: 100,
                onChanged: _remove
                    ? (v) => _update(() => _tolerance = v.round())
                    : null,
              ),
              CheckboxListTile(
                key: const ValueKey('logo-crop'),
                contentPadding: EdgeInsets.zero,
                title: const Text('Crop transparent margins'),
                value: _crop,
                onChanged: (v) => _update(() => _crop = v ?? false),
              ),
              if (_error != null)
                Text(
                  _error!,
                  key: const ValueKey('logo-edit-error'),
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              const Text(
                'Complex backgrounds may need manual editing. Restore original before saving if details disappear.',
              ),
              TextButton(
                key: const ValueKey('logo-restore'),
                onPressed: () => _update(() {
                  _remove = false;
                  _crop = false;
                  _background = 0xffffff;
                  _tolerance = 30;
                }),
                child: const Text('Restore original'),
              ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          key: const ValueKey('logo-use-edited'),
          onPressed: edited == null
              ? null
              : () {
                  if (!widget.active()) {
                    Navigator.pop(context);
                    return;
                  }
                  Navigator.pop(
                    context,
                    Uint8List.fromList(
                      !_remove && !_crop ? widget.raw : edited,
                    ),
                  );
                },
          child: const Text('Use logo'),
        ),
      ],
    );
  }
}

/// Normalized PNG preview: crop transparent margins the same way as Flask.
/// The preview is illustrative; the server's PDF is the authority.
class StudioLogoPreview extends StatefulWidget {
  const StudioLogoPreview({super.key, required this.bytes, required this.size});
  final Uint8List bytes;
  final String size;
  @override
  State<StudioLogoPreview> createState() => _StudioLogoPreviewState();
}

class _StudioLogoPreviewState extends State<StudioLogoPreview> {
  MemoryImage? _image;
  int _width = 0, _height = 0, _generation = 0;
  bool _failed = false;
  @override
  void initState() {
    super.initState();
    unawaited(_prepare());
  }

  @override
  void didUpdateWidget(covariant StudioLogoPreview oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.bytes, widget.bytes)) {
      unawaited(_prepare());
    }
  }

  void _clear() {
    final old = _image;
    _image = null;
    if (old != null) {
      unawaited(old.evict());
    }
  }

  Future<void> _prepare() async {
    final gen = ++_generation;
    _clear();
    _failed = false;
    try {
      final original = await LogoPixels.decode(widget.bytes);
      if (!mounted || gen != _generation) {
        return;
      }
      final cropped = original.edit(
        remove: false,
        background: 0,
        tolerance: 0,
        crop: true,
      );
      setState(() {
        _width = cropped.width;
        _height = cropped.height;
        _image = MemoryImage(cropped.png());
      });
    } on Object {
      if (mounted && gen == _generation) {
        setState(() => _failed = true);
      }
    }
  }

  @override
  void dispose() {
    _generation++;
    _clear();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final image = _image;
    if (image == null) {
      return _failed
          ? const Icon(Icons.broken_image_outlined)
          : const SizedBox.shrink();
    }
    final fit = logoFit(_width, _height, widget.size);
    return Image(
      key: const ValueKey('studio-logo-image'),
      image: image,
      width: fit.width,
      height: fit.height,
      fit: BoxFit.contain,
      errorBuilder: (_, _, _) => const Icon(Icons.broken_image_outlined),
    );
  }
}
