import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

import '../data/branding_remote.dart';
import '../data/branding_studio.dart';

const _labels = <String, String>{
  'primary_color': 'Primary colour',
  'secondary_color': 'Secondary colour',
  'text_color': 'Document text colour',
  'warning_color': 'Warning text colour',
  'background_color': 'Paper background colour',
};
const _titles = <String, String>{
  'invoice': 'Invoice',
  'appointment': 'Appointment',
  'lab_order': 'Lab Order',
  'released_lab_result': 'Laboratory Results',
  'radiology_draft': 'Radiology Draft',
  'staged_radiology_copy': 'Radiology Report Copy',
};
const _modeLabels = {
  'full': 'Full branding',
  'ink_saving': 'Ink saving',
  'monochrome': 'Monochrome',
};
Color _color(String hex) =>
    Color(0xff000000 | int.parse(hex.substring(1), radix: 16));
String _hex(Color color) =>
    '#${(color.toARGB32() & 0xffffff).toRadixString(16).padLeft(6, '0').toUpperCase()}';

/// Draft only. Every authoritative write is owned by the parent screen.
class BrandingStudioEditor extends StatefulWidget {
  const BrandingStudioEditor({
    super.key,
    required this.view,
    required this.image,
    required this.organizationName,
    required this.enabled,
    required this.canCustomize,
    required this.onSave,
  });
  final BrandingView view;
  final ImageProvider? image;
  final String organizationName;
  final bool enabled, canCustomize;
  final ValueChanged<Map<String, String>> onSave;
  @override
  State<BrandingStudioEditor> createState() => _BrandingStudioEditorState();
}

class _BrandingStudioEditorState extends State<BrandingStudioEditor> {
  late Map<String, String> _local;
  late final TextEditingController _hexInput;
  late HSVColor _hsv;
  String _selected = 'primary_color';
  String _document = 'invoice';
  int? _boardPointer;
  BrandingStudioState get _studio => widget.view.studio!;
  Map<String, String> get _palette => _studio.draft(_local);
  bool get _edit => widget.enabled && widget.canCustomize;
  @override
  void initState() {
    super.initState();
    _local = Map.of(_studio.local);
    _hexInput = TextEditingController(text: _palette[_selected]);
    _hsv = HSVColor.fromColor(_color(_palette[_selected]!));
  }

  @override
  void dispose() {
    _hexInput.dispose();
    super.dispose();
  }

  void _sync() {
    _hexInput.text = _palette[_selected]!;
    _hsv = HSVColor.fromColor(_color(_palette[_selected]!));
  }

  void _choose(String field) {
    setState(() {
      _selected = field;
      _sync();
    });
  }

  void _changeHsv(HSVColor hsv) {
    if (!_edit) {
      return;
    }
    setState(() {
      _hsv = hsv;
      final hex = _hex(hsv.toColor());
      _local[_selected] = hex;
      _hexInput.text = hex;
    });
  }

  void _changeHex(String raw) {
    if (!_edit) {
      return;
    }
    final value = raw.trim().toUpperCase();
    setState(() {
      if (studioHex(value)) {
        _local[_selected] = value;
        _hsv = HSVColor.fromColor(_color(value));
      }
    });
  }

  void _reset(String key) {
    if (!widget.enabled) {
      return;
    }
    setState(() {
      _local.remove(key);
      _sync();
    });
  }

  void _resetAll() {
    if (!widget.enabled) {
      return;
    }
    setState(() {
      _local.clear();
      _sync();
    });
  }

  void _discard() {
    if (!widget.enabled) {
      return;
    }
    setState(() {
      _local = Map.of(_studio.local);
      _sync();
    });
  }

  Widget _board() => LayoutBuilder(
    builder: (context, constraints) {
      final w = constraints.maxWidth;
      if (w < 16 || !w.isFinite) {
        return const SizedBox.shrink();
      }
      const h = 160.0;
      void select(Offset offset) {
        _changeHsv(
          _hsv
              .withSaturation((offset.dx / w).clamp(0.0, 1.0).toDouble())
              .withValue((1 - offset.dy / h).clamp(0.0, 1.0).toDouble()),
        );
      }

      return Semantics(
        label: 'Saturation and brightness colour board. Drag or use the sliders below.',
        enabled: _edit,
        child: RawGestureDetector(
          behavior: HitTestBehavior.opaque,
          gestures: _edit
              ? <Type, GestureRecognizerFactory>{
                  EagerGestureRecognizer:
                      GestureRecognizerFactoryWithHandlers<
                        EagerGestureRecognizer
                      >(() => EagerGestureRecognizer(), (_) {}),
                }
              : const <Type, GestureRecognizerFactory>{},
          child: Listener(
            key: const ValueKey('studio-colour-board'),
            behavior: HitTestBehavior.opaque,
            onPointerDown: _edit
                ? (d) {
                    if (_boardPointer == null &&
                        (d.buttons & kPrimaryButton) != 0) {
                      _boardPointer = d.pointer;
                      select(d.localPosition);
                    }
                  }
                : null,
            onPointerMove: _edit
                ? (d) {
                    if (_boardPointer == d.pointer) {
                      select(d.localPosition);
                    }
                  }
                : null,
            onPointerUp: (d) {
              if (_boardPointer == d.pointer) {
                _boardPointer = null;
              }
            },
            onPointerCancel: (d) {
              if (_boardPointer == d.pointer) {
                _boardPointer = null;
              }
            },
            child: SizedBox(
              height: h,
              width: w,
              child: Stack(
                clipBehavior: Clip.hardEdge,
                children: [
                  Positioned.fill(
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        gradient: LinearGradient(
                          colors: [
                            Colors.white,
                            HSVColor.fromAHSV(1, _hsv.hue, 1, 1).toColor(),
                          ],
                        ),
                      ),
                    ),
                  ),
                  const Positioned.fill(
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        gradient: LinearGradient(
                          begin: Alignment.topCenter,
                          end: Alignment.bottomCenter,
                          colors: [Colors.transparent, Colors.black],
                        ),
                      ),
                    ),
                  ),
                  Positioned(
                    left: (_hsv.saturation * w - 8)
                        .clamp(0.0, w >= 16 ? w - 16 : 0.0)
                        .toDouble(),
                    top: ((1 - _hsv.value) * h - 8)
                        .clamp(0.0, h - 16)
                        .toDouble(),
                    child: IgnorePointer(
                      child: Container(
                        width: 16,
                        height: 16,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: _hsv.toColor(),
                          border: Border.all(color: Colors.white, width: 2),
                          boxShadow: const [
                            BoxShadow(color: Colors.black, blurRadius: 2),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    },
  );
  Widget _slider(
    String label,
    String key,
    double value,
    double max,
    ValueChanged<double> change,
  ) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text('$label: ${value.round()}${max == 360 ? '°' : '%'}'),
      Slider(
        key: ValueKey('studio-$key'),
        value: value.clamp(0, max).toDouble(),
        max: max,
        divisions: max.round(),
        label: '${value.round()}',
        semanticFormatterCallback: (v) =>
            '$label ${v.round()}${max == 360 ? ' degrees' : ' percent'}',
        onChanged: _edit ? change : null,
      ),
    ],
  );
  List<String> get _presets => switch (_selected) {
    'background_color' => [
      '#FFFFFF',
      '#FFF8E7',
      '#F0F7FF',
      '#F0F8F0',
      '#FFF1F5',
      '#F2F2F2',
    ],
    'warning_color' => ['#842020', '#991B1B', '#92400E', '#7C2D12', '#831843'],
    _ => [
      '#17354A',
      '#136228',
      '#1D4ED8',
      '#8A4B26',
      '#000000',
      '#263D36',
      '#475569',
    ],
  };
  Widget _controls() {
    final palette = _palette;
    final validation = studioValidation(palette);
    final validHex = studioHex(_hexInput.text.trim().toUpperCase());
    final changed = !studioEqual(_local, _studio.local);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          'Document branding studio',
          style: Theme.of(context).textTheme.titleLarge,
        ),
        const Text(
          'Select a colour, then click or drag on the board. The HEX field and preview follow your changes.',
        ),
        if (!widget.canCustomize)
          const Text(
            'Customization is not entitled. Existing local overrides may only be reset.',
          ),
        const SizedBox(height: 8),
        for (final entry in _labels.entries)
          Row(
            children: [
              Expanded(
                child: ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  key: ValueKey('studio-select-${entry.key}'),
                  selected: _selected == entry.key,
                  onTap: widget.enabled ? () => _choose(entry.key) : null,
                  leading: Container(
                    width: 32,
                    height: 32,
                    decoration: BoxDecoration(
                      color: _color(palette[entry.key]!),
                      border: Border.all(color: Colors.grey),
                      borderRadius: BorderRadius.circular(4),
                    ),
                  ),
                  title: Text(entry.value),
                  subtitle: Text(
                    '${palette[entry.key]} · ${_local.containsKey(entry.key) ? (_studio.clinicId != null && !_studio.allowed ? 'Dormant local override' : 'Local override') : 'Inherited/default'}',
                  ),
                ),
              ),
              TextButton(
                key: ValueKey('studio-reset-${entry.key}'),
                onPressed: widget.enabled && _local.containsKey(entry.key)
                    ? () => _reset(entry.key)
                    : null,
                child: const Text('Reset'),
              ),
            ],
          ),
        Text(
          'Editing ${_labels[_selected]}',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 8),
        _board(),
        const SizedBox(height: 6),
        Container(
          height: 10,
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              colors: [
                Colors.red,
                Colors.yellow,
                Colors.green,
                Colors.cyan,
                Colors.blue,
                Colors.purple,
                Colors.red,
              ],
            ),
          ),
        ),
        _slider(
          'Hue',
          'hue',
          _hsv.hue,
          360,
          (v) => _changeHsv(_hsv.withHue(v == 360 ? 0 : v)),
        ),
        _slider(
          'Saturation',
          'saturation',
          _hsv.saturation * 100,
          100,
          (v) => _changeHsv(_hsv.withSaturation(v / 100)),
        ),
        _slider(
          'Brightness',
          'brightness',
          _hsv.value * 100,
          100,
          (v) => _changeHsv(_hsv.withValue(v / 100)),
        ),
        TextField(
          key: const ValueKey('studio-hex'),
          controller: _hexInput,
          enabled: _edit,
          maxLength: 7,
          onChanged: _changeHex,
          decoration: InputDecoration(
            labelText: '${_labels[_selected]} HEX',
            border: const OutlineInputBorder(),
            errorText: validHex ? null : 'Use #RRGGBB',
          ),
        ),
        const Text('Suggested presets (custom HEX values are also supported)'),
        Wrap(
          spacing: 6,
          runSpacing: 4,
          children: [
            for (final color in _presets)
              Semantics(
                label: 'Preset $color',
                button: true,
                child: InkWell(
                  key: ValueKey('studio-preset-$color'),
                  onTap: _edit
                      ? () => _changeHsv(HSVColor.fromColor(_color(color)))
                      : null,
                  child: Container(
                    width: 42,
                    height: 42,
                    decoration: BoxDecoration(
                      color: _color(color),
                      border: Border.all(color: Colors.grey),
                      shape: BoxShape.circle,
                    ),
                    child: palette[_selected] == color
                        ? Icon(
                            Icons.check,
                            color: studioLuminance(color) > .5
                                ? Colors.black
                                : Colors.white,
                          )
                        : null,
                  ),
                ),
              ),
          ],
        ),
        const SizedBox(height: 12),
        const Text('Document output mode'),
        DropdownButton<String>(
          key: const ValueKey('studio-mode'),
          isExpanded: true,
          value: palette['output_mode'],
          items: [
            for (final entry in _modeLabels.entries)
              DropdownMenuItem(value: entry.key, child: Text(entry.value)),
          ],
          onChanged: _edit
              ? (mode) {
                  if (mode != null) {
                    setState(() => _local['output_mode'] = mode);
                  }
                }
              : null,
        ),
        TextButton(
          key: const ValueKey('studio-reset-output_mode'),
          onPressed: widget.enabled && _local.containsKey('output_mode')
              ? () => _reset('output_mode')
              : null,
          child: const Text('Reset output mode'),
        ),
        Text(
          validation ?? 'Palette meets the configured contrast requirements.',
          key: const ValueKey('studio-validation'),
          style: TextStyle(
            color: validation == null
                ? null
                : Theme.of(context).colorScheme.error,
          ),
        ),
        const Text(
          'Server validates the complete effective palette, including inherited clinic settings.',
        ),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            FilledButton(
              key: const ValueKey('studio-save'),
              onPressed:
                  widget.enabled &&
                      changed &&
                      validHex &&
                      _studio.accepts(_local)
                  ? () => widget.onSave(Map.of(_local))
                  : null,
              child: const Text('Save branding studio'),
            ),
            OutlinedButton(
              key: const ValueKey('studio-reset-all'),
              onPressed: widget.enabled && _local.isNotEmpty ? _resetAll : null,
              child: const Text('Reset all colours and mode'),
            ),
            OutlinedButton(
              key: const ValueKey('studio-discard'),
              onPressed: widget.enabled && (changed || !validHex)
                  ? _discard
                  : null,
              child: const Text('Discard drafts'),
            ),
          ],
        ),
      ],
    );
  }

  Widget _preview() {
    final p = _palette;
    final mode = p['output_mode']!;
    Color paint(String v) => _color(studioPaint(v, mode));
    final primary = paint(p['primary_color']!),
        secondary = paint(p['secondary_color']!),
        ink = paint(p['text_color']!),
        warning = paint(p['warning_color']!),
        paper = paint(mode == 'full' ? p['background_color']! : '#FFFFFF'),
        critical = paint('#842020');
    Widget label(
      String content,
      Color c, {
      String? key,
      bool bold = false,
      double size = 12,
    }) => Text(
      content,
      key: key == null ? null : ValueKey('studio-preview-$key'),
      style: TextStyle(
        color: c,
        fontWeight: bold ? FontWeight.bold : FontWeight.normal,
        fontSize: size,
      ),
    );
    Widget stripe(String key, Color c, double height) => Container(
      key: ValueKey('studio-preview-$key'),
      height: height,
      color: c,
    );
    final table = const {
      'invoice',
      'lab_order',
      'released_lab_result',
    }.contains(_document);
    final section = switch (_document) {
      'invoice' => 'Invoice Items',
      'lab_order' => 'Ordered Tests',
      'released_lab_result' => 'Report Text',
      'appointment' => 'Appointment details',
      _ => 'Report Text',
    };
    final heads = switch (_document) {
      'invoice' => ['Description', 'Qty', 'Subtotal'],
      'lab_order' => ['Test', 'Specimen', 'Priority'],
      _ => ['Test', 'Result', 'Flag'],
    };
    final rows = switch (_document) {
      'invoice' => [
        ['Consultation', '1', '10,000.00'],
        ['Lab service', '1', '7,500.00'],
      ],
      'lab_order' => [
        ['Full blood count', 'Blood', 'Routine'],
        ['Glucose', 'Blood', 'Routine'],
      ],
      _ => [
        ['Haemoglobin', '13.5 g/dL', 'Normal'],
        ['Potassium', '6.8 mmol/L', 'CRITICAL'],
      ],
    };
    Widget row(
      List<String> values, {
      required Color bg,
      required bool heading,
      required int index,
    }) => Container(
      key: ValueKey(
        heading ? 'studio-preview-table' : 'studio-preview-row-$index',
      ),
      color: bg,
      padding: const EdgeInsets.all(8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (var i = 0; i < values.length; i++)
            Expanded(
              flex: i == 0 ? 2 : 1,
              child: label(
                values[i],
                heading
                    ? Colors.white
                    : values[i] == 'CRITICAL'
                    ? critical
                    : ink,
                key: values[i] == 'CRITICAL'
                    ? 'critical'
                    : !heading && index == 0 && i == 0
                    ? 'table-body'
                    : null,
                bold: heading || values[i] == 'CRITICAL',
                size: 11,
              ),
            ),
        ],
      ),
    );
    final logo = widget.image;
    Widget? image;
    if (logo != null) {
      image = Image(
        image: logo,
        width: 60,
        height: 48,
        fit: BoxFit.contain,
        errorBuilder: (_, _, _) =>
            const Icon(Icons.broken_image_outlined, color: Colors.black),
      );
      if (mode == 'monochrome') {
        image = ColorFiltered(
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
          child: image,
        );
      }
    }
    final notice = switch (_document) {
      'invoice' => 'BILLING DOCUMENT COPY - NOT A PAYMENT RECEIPT',
      'lab_order' => 'SPECIMEN / LAB ORDER ONLY - NO VERIFIED RESULT',
      'appointment' => 'APPOINTMENT COPY - NOT PROOF OF ATTENDANCE',
      'released_lab_result' =>
        'PATIENT COPY OF RELEASED LAB REPORT - NOT A SIGNED ORIGINAL',
      'radiology_draft' =>
        'UNSIGNED WORKING NOTE - NOT FOR DIAGNOSIS OR CHART RELEASE',
      _ => 'SYNTHETIC TEST COPY - NOT FOR DIAGNOSIS OR CHART RELEASE',
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          'Live document sample',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const Text(
          'Illustrative layout with synthetic patient data. Actual PDFs are generated by the backend.',
        ),
        DropdownButton<String>(
          key: const ValueKey('studio-document'),
          isExpanded: true,
          value: _document,
          items: [
            for (final e in _titles.entries)
              DropdownMenuItem(value: e.key, child: Text(e.value)),
          ],
          onChanged: widget.enabled
              ? (v) {
                  if (v != null) {
                    setState(() => _document = v);
                  }
                }
              : null,
        ),
        Container(
          key: const ValueKey('studio-preview-paper'),
          color: paper,
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              stripe('stripe', primary, 4),
              const SizedBox(height: 12),
              Row(
                children: [
                  if (image != null) ...[image, const SizedBox(width: 10)],
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        label(
                          widget.organizationName,
                          primary,
                          key: 'organization',
                          bold: true,
                          size: 20,
                        ),
                        label(
                          widget.view.state.scope == BrandingScope.clinic
                              ? widget.view.state.name.split(' · ').last
                              : 'Sample clinic',
                          secondary,
                          key: 'clinic',
                          bold: true,
                        ),
                        label('Sample address', ink),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              Divider(height: 1, color: paint('#D6DFE6')),
              const SizedBox(height: 10),
              label(
                _titles[_document]!,
                primary,
                key: 'title',
                bold: true,
                size: 24,
              ),
              const SizedBox(height: 8),
              Container(
                color: paint('#FCF2F1'),
                padding: const EdgeInsets.all(8),
                child: label(notice, warning, key: 'warning', bold: true),
              ),
              const SizedBox(height: 10),
              label('CONTROLLED DOCUMENT COPY', ink, bold: true),
              const SizedBox(height: 8),
              label(
                'Patient name: Synthetic Patient',
                ink,
                key: 'body',
                bold: true,
              ),
              label('Record reference: 001', ink),
              const SizedBox(height: 16),
              label(section, secondary, key: 'section', bold: true),
              const SizedBox(height: 4),
              stripe('divider', secondary, 1),
              const SizedBox(height: 6),
              if (table) ...[
                row(heads, bg: secondary, heading: true, index: -1),
                for (var i = 0; i < rows.length; i++)
                  row(
                    rows[i],
                    bg: i.isEven ? paint('#F1F5F7') : paper,
                    heading: false,
                    index: i,
                  ),
              ] else
                label(
                  _document == 'appointment'
                      ? 'Department: General outpatient clinic\nScheduled time: Sample appointment'
                      : 'Synthetic report content for layout testing only.',
                  ink,
                  key: 'report',
                ),
              if (_document == 'invoice') ...[
                const SizedBox(height: 8),
                Container(
                  color: paint('#F1F5F7'),
                  padding: const EdgeInsets.all(8),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      label('Balance Due', ink),
                      label(
                        'NGN 17,500.00',
                        critical,
                        key: 'balance',
                        bold: true,
                        size: 20,
                      ),
                    ],
                  ),
                ),
              ],
              const SizedBox(height: 16),
              stripe('footer', primary, 1),
              const SizedBox(height: 8),
              label('SOURCE SHA-256: sample only', ink, size: 10),
              label(
                'This copy does not prove payment, clinical verification, diagnosis, signature, delivery, or chart release.',
                warning,
                key: 'disclaimer',
                size: 10,
              ),
              label('Powered by Stryka-Max', ink, bold: true),
            ],
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: LayoutBuilder(
        builder: (context, c) => c.maxWidth >= 780
            ? Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(child: _controls()),
                  const SizedBox(width: 16),
                  Expanded(child: _preview()),
                ],
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [_controls(), const SizedBox(height: 16), _preview()],
              ),
      ),
    ),
  );
}
