import 'package:flutter/material.dart';

import '../data/blood_bank_contract.dart';
import '../data/blood_bank_forms.dart';

class BbFields extends StatefulWidget {
  const BbFields({
    super.key,
    required this.fields,
    required this.values,
    required this.enabled,
  });
  final List<BbField> fields;
  final BbJson values;
  final bool enabled;
  @override
  State<BbFields> createState() => _BbFieldsState();
}

class _BbFieldsState extends State<BbFields> {
  Widget _field(BbField f) {
    final v = widget.values[f.key];
    final label = f.title + (f.required ? ' *' : '');
    if (f.kind == 'rows' || f.kind == 'panel') {
      final rows =
          widget.values.putIfAbsent(f.key, () => <BbJson>[]) as List<BbJson>;
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: Theme.of(context).textTheme.titleMedium),
          for (final row in rows)
            Card(
              key: ObjectKey(row),
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(
                  children: [
                    BbFields(
                      fields: f.children,
                      values: row,
                      enabled: widget.enabled,
                    ),
                    TextButton(
                      onPressed: widget.enabled
                          ? () => setState(() => rows.remove(row))
                          : null,
                      child: const Text('Remove row'),
                    ),
                  ],
                ),
              ),
            ),
          OutlinedButton(
            onPressed:
                widget.enabled && rows.length < (f.kind == 'panel' ? 50 : f.max)
                ? () => setState(() => rows.add(<String, dynamic>{}))
                : null,
            child: Text(
              'Add ${f.kind == 'panel' ? 'screening marker' : 'component'}',
            ),
          ),
        ],
      );
    }
    if (f.kind == 'object') {
      final row =
          widget.values.putIfAbsent(f.key, () => <String, dynamic>{}) as BbJson;
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label),
          BbFields(fields: f.children, values: row, enabled: widget.enabled),
        ],
      );
    }
    if (f.kind == 'choice' || f.kind == 'bool' || f.kind == 'optionalBool') {
      final options = f.kind == 'choice' ? f.options : ['yes', 'no'];
      final initial = f.kind == 'bool'
          ? (v == null
                ? null
                : v == true
                ? 'yes'
                : 'no')
          : v as String?;
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 5),
        child: DropdownButtonFormField<String>(
          key: ValueKey(f.key),
          initialValue: initial,
          isExpanded: true,
          decoration: InputDecoration(
            labelText: label,
            border: const OutlineInputBorder(),
          ),
          items: [
            if (!f.required)
              const DropdownMenuItem(value: '', child: Text('Not supplied')),
            for (final o in options)
              DropdownMenuItem(value: o, child: Text(o.replaceAll('_', ' '))),
          ],
          onChanged: widget.enabled
              ? (x) => widget.values[f.key] = f.kind == 'bool' ? x == 'yes' : x
              : null,
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: TextFormField(
        key: ValueKey(f.key),
        initialValue: v?.toString() ?? '',
        enabled: widget.enabled,
        autocorrect: false,
        enableSuggestions: false,
        keyboardType: f.kind == 'int' || f.kind == 'count'
            ? TextInputType.number
            : f.kind == 'decimal'
            ? const TextInputType.numberWithOptions(decimal: true)
            : TextInputType.text,
        decoration: InputDecoration(
          labelText: label,
          helperText: f.kind == 'time'
              ? 'ISO 8601 timestamp with Z or offset'
              : f.kind == 'date'
              ? 'YYYY-MM-DD'
              : null,
          border: const OutlineInputBorder(),
        ),
        onChanged: (x) => widget.values[f.key] = x,
      ),
    );
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [for (final f in widget.fields) _field(f)],
  );
}

Future<bool> confirmBankAction(
  BuildContext context,
  String title,
  String description,
) async =>
    await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (c) => AlertDialog(
        title: Text(title),
        content: SingleChildScrollView(
          child: Text(
            '$description\n\nConfirm the actual clinical record and intended action. Send once; no automatic retry or offline replay.',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Submit once'),
          ),
        ],
      ),
    ) ??
    false;
String bankError(Object error) => error is FormatException
    ? 'Evidence or input is incomplete, stale, or mismatched. Refresh the server record.'
    : 'Blood Bank access or outcome could not be verified. No action is inferred. If submitted, reconcile with the server before attempting again.';
