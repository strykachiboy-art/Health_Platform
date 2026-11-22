import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:clinic_system_pro/features/patient_portal/data/patient_portal_remote_data_source.dart';

/// Each view reads only the server-owned, enrolled Patient's records.
/// This is not a generic staff chart, invoice editor or internal case chat.
class PatientPrescriptionDetailPage extends StatefulWidget {
  const PatientPrescriptionDetailPage({
    super.key,
    required this.actions,
    required this.recordId,
  });
  final PatientPortalServiceActions actions;
  final int recordId;

  @override
  State<PatientPrescriptionDetailPage> createState() =>
      _PatientPrescriptionDetailPageState();
}

class _PatientPrescriptionDetailPageState
    extends State<PatientPrescriptionDetailPage> {
  Map<String, dynamic>? _data;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    try {
      final data = await widget.actions.prescriptionDetail(widget.recordId);
      if (mounted) {
        setState(() {
          _data = data;
          _loading = false;
        });
      }
    } on Object {
      if (mounted) {
        setState(() {
          _data = null;
          _loading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final details = _data;
    final raw = details?['items'];
    final items = raw is List
        ? raw.whereType<Map<String, dynamic>>().toList()
        : const <Map<String, dynamic>>[];
    return Scaffold(
      appBar: AppBar(title: const Text('My prescription details')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : details == null
          ? const Center(
              child: Text(
                'Prescription details unavailable. Refresh your session.',
              ),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Prescription #${details['id']} · ${details['status']}',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 12),
                Text('Issued: ${details['issued_at'] ?? 'Not specified'}'),
                Text('Expires: ${details['expires_at'] ?? 'Not specified'}'),
                const SizedBox(height: 16),
                if (items.isEmpty)
                  const Text(
                    'No medicine items returned for this prescription.',
                  ),
                for (final item in items)
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(12),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            '${item['medicine'] ?? 'Medicine unavailable'}',
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          Text(
                            'Strength: ${item['strength'] ?? 'Not specified'}',
                          ),
                          Text('Dosage: ${item['dosage'] ?? 'Not specified'}'),
                          Text(
                            'Frequency: ${item['frequency'] ?? 'Not specified'}',
                          ),
                          Text(
                            'Duration: ${item['duration'] ?? 'Not specified'}',
                          ),
                          Text('Route: ${item['route'] ?? 'Not specified'}'),
                          Text(
                            'Quantity: ${item['quantity'] ?? 'Not specified'}',
                          ),
                          Text(
                            'Instructions: ${item['instructions'] ?? 'Not specified'}',
                          ),
                        ],
                      ),
                    ),
                  ),
                const Text(
                  'Follow your prescribing clinician’s instructions. This view does not change a medication order.',
                ),
              ],
            ),
    );
  }
}

class PatientInvoiceDetailPage extends StatefulWidget {
  const PatientInvoiceDetailPage({
    super.key,
    required this.actions,
    required this.recordId,
  });
  final PatientPortalServiceActions actions;
  final int recordId;

  @override
  State<PatientInvoiceDetailPage> createState() =>
      _PatientInvoiceDetailPageState();
}

class _PatientInvoiceDetailPageState extends State<PatientInvoiceDetailPage> {
  Map<String, dynamic>? _data;
  bool _loading = true;
  bool _checkoutAttempted = false;
  bool _saving = false;
  String? _notice;
  String? _checkoutUrl;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    try {
      final data = await widget.actions.invoiceDetail(widget.recordId);
      if (mounted) {
        setState(() {
          _data = data;
          _loading = false;
        });
      }
    } on Object {
      if (mounted) {
        setState(() {
          _data = null;
          _loading = false;
        });
      }
    }
  }

  String _key() {
    final random = Random.secure();
    final bytes = List<int>.generate(24, (_) => random.nextInt(256));
    return base64UrlEncode(bytes).replaceAll('=', '');
  }

  Future<void> _checkout() async {
    if (_saving ||
        _checkoutAttempted ||
        _data?['sandbox_checkout_available'] != true) {
      return;
    }
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Start Paystack TEST checkout?'),
        content: const Text(
          'This is a sandbox-only checkout. It does not confirm payment. The hospital must verify the provider result.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Back'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Start test checkout'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) {
      return;
    }
    setState(() {
      _checkoutAttempted = true;
      _saving = true;
      _notice = null;
    });
    try {
      final result = await widget.actions.checkout(widget.recordId, _key());
      if (!mounted) {
        return;
      }
      final link = result['checkout_url'];
      final uri = link is String ? Uri.tryParse(link) : null;
      final valid =
          uri != null &&
          uri.scheme == 'https' &&
          uri.host == 'checkout.paystack.com' &&
          uri.userInfo.isEmpty;
      setState(() {
        _saving = false;
        _checkoutUrl = valid ? link : null;
        _notice = valid
            ? 'Test checkout initialized. Payment is still pending. Copy the link to your browser.'
            : 'Payment outcome remains pending. Check invoice status with the clinic; do not submit again blindly.';
      });
    } on Object {
      if (!mounted) {
        return;
      }
      setState(() {
        _saving = false;
        _notice = 'Checkout result uncertain. Do not retry. Check the invoice and contact the clinic.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final data = _data;
    final items = data?['items'] is List
        ? (data!['items'] as List).whereType<Map<String, dynamic>>().toList()
        : const <Map<String, dynamic>>[];
    final payments = data?['payments'] is List
        ? (data!['payments'] as List).whereType<Map<String, dynamic>>().toList()
        : const <Map<String, dynamic>>[];
    return Scaffold(
      appBar: AppBar(title: const Text('My invoice details')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : data == null
          ? const Center(
              child: Text('Invoice details unavailable. Refresh your session.'),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'Invoice ${data['invoice_number']}',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                Text('Status: ${data['status']}'),
                Text(
                  'Total: ${data['total_amount']} ${data['currency'] ?? ''}',
                ),
                Text('Paid: ${data['amount_paid']} ${data['currency'] ?? ''}'),
                Text(
                  'Outstanding: ${data['outstanding_balance']} ${data['currency'] ?? ''}',
                ),
                const SizedBox(height: 12),
                Text(
                  'Services charged',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                if (items.isEmpty)
                  const Text('No invoice line items returned.'),
                for (final item in items)
                  ListTile(
                    title: Text('${item['description']}'),
                    subtitle: Text(
                      'Quantity ${item['quantity']} · Unit price ${item['unit_price']}',
                    ),
                    trailing: Text('${item['subtotal']}'),
                  ),
                const SizedBox(height: 12),
                Text(
                  'Payment history',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                if (payments.isEmpty)
                  const Text('No payments recorded for this invoice.'),
                for (final payment in payments)
                  ListTile(
                    title: Text(
                      '${payment['amount']} ${payment['currency'] ?? ''}',
                    ),
                    subtitle: Text(
                      '${payment['method']} · ${payment['status']}',
                    ),
                  ),
                const SizedBox(height: 12),
                if (data['sandbox_checkout_available'] == true)
                  FilledButton(
                    onPressed: _checkoutAttempted || _saving ? null : _checkout,
                    child: const Text('Paystack TEST checkout'),
                  )
                else
                  const Text(
                    'Online payment is unavailable until the clinic enables verified sandbox checkout.',
                  ),
                if (_notice != null) Text(_notice!),
                if (_checkoutUrl != null) ...[
                  const SizedBox(height: 8),
                  SelectableText(
                    _checkoutUrl!,
                    key: const ValueKey('s13-checkout-link'),
                  ),
                  OutlinedButton.icon(
                    onPressed: () =>
                        Clipboard.setData(ClipboardData(text: _checkoutUrl!)),
                    icon: const Icon(Icons.copy_outlined),
                    label: const Text('Copy test checkout link'),
                  ),
                ],
              ],
            ),
    );
  }
}

class PatientCareMessagesPage extends StatefulWidget {
  const PatientCareMessagesPage({super.key, required this.actions});
  final PatientPortalServiceActions actions;

  @override
  State<PatientCareMessagesPage> createState() =>
      _PatientCareMessagesPageState();
}

class _PatientCareMessagesPageState extends State<PatientCareMessagesPage> {
  List<Map<String, dynamic>>? _conversations;
  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    try {
      final result = await widget.actions.careConversations();
      if (mounted) {
        setState(() {
          _conversations = result;
        });
      }
    } on Object {
      if (mounted) {
        setState(() {
          _conversations = const [];
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('My care messages')),
      body: _conversations == null
          ? const Center(child: CircularProgressIndicator())
          : _conversations!.isEmpty
          ? const Center(
              child: Padding(
                padding: EdgeInsets.all(16),
                child: Text(
                  'No approved patient-care conversation is available. Ask your clinic to invite you; internal staff conversations remain private.',
                ),
              ),
            )
          : ListView(
              children: [
                for (final conversation in _conversations!)
                  ListTile(
                    key: ValueKey(
                      's13-care-conversation-${conversation['id']}',
                    ),
                    title: Text('${conversation['title']}'),
                    trailing: const Icon(Icons.chat_outlined),
                    onTap: conversation['id'] is int
                        ? () => Navigator.of(context).push<void>(
                            MaterialPageRoute(
                              builder: (_) => _CareThread(
                                actions: widget.actions,
                                id: conversation['id'] as int,
                              ),
                            ),
                          )
                        : null,
                  ),
              ],
            ),
    );
  }
}

class _CareThread extends StatefulWidget {
  const _CareThread({required this.actions, required this.id});
  final PatientPortalServiceActions actions;
  final int id;
  @override
  State<_CareThread> createState() => _CareThreadState();
}

class _CareThreadState extends State<_CareThread> {
  final _draft = TextEditingController();
  List<Map<String, dynamic>>? _items;
  bool _sending = false;
  bool _sendAttempted = false;
  String? _notice;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void dispose() {
    _draft.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final data = await widget.actions.careMessages(widget.id);
      if (mounted) {
        setState(() {
          _items = data;
        });
      }
    } on Object {
      if (mounted) {
        setState(() {
          _items = const [];
          _notice = 'Care conversation unavailable.';
        });
      }
    }
  }

  Future<void> _send() async {
    if (_sending || _sendAttempted) {
      return;
    }
    final content = _draft.text.trim();
    if (content.isEmpty || content.length > 2000) {
      return;
    }
    setState(() {
      _sendAttempted = true;
      _sending = true;
      _notice = null;
    });
    try {
      await widget.actions.sendCareMessage(widget.id, content);
      if (!mounted) {
        return;
      }
      _draft.clear();
      await _load();
      if (mounted) {
        setState(() {
          _sending = false;
          _notice = 'Message accepted by the server.';
        });
      }
    } on Object {
      if (mounted) {
        setState(() {
          _sending = false;
          _notice = 'Message delivery uncertain. Do not retry blindly; ask the clinic to verify.';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Care conversation')),
    body: Column(
      children: [
        Expanded(
          child: _items == null
              ? const Center(child: CircularProgressIndicator())
              : ListView(
                  children: [
                    for (final item in _items!)
                      ListTile(
                        title: Text('${item['content']}'),
                        subtitle: Text(
                          item['sender_is_me'] == true ? 'You' : 'Care team',
                        ),
                      ),
                  ],
                ),
        ),
        if (_notice != null) Text(_notice!),
        Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _draft,
                  maxLength: 2000,
                  decoration: const InputDecoration(
                    labelText: 'Message your care team',
                  ),
                ),
              ),
              IconButton(
                key: const ValueKey('s13-send-message'),
                onPressed: _sending || _sendAttempted
                    ? null
                    : () => unawaited(_send()),
                icon: const Icon(Icons.send_outlined),
                tooltip: 'Send message once',
              ),
            ],
          ),
        ),
      ],
    ),
  );
}
