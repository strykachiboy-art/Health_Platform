import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/administration/presentation/wd05_administration_hub.dart';

/// Separate offline-only catalog. Never builds the production router,
/// remote client, auth coordinator, token store, payment or export actions.
void main() => runApp(const Wd05OfflinePreviewApp());

class Wd05OfflinePreviewApp extends StatelessWidget {
  const Wd05OfflinePreviewApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Clinic System Pro - WD05 Offline Preview',
    theme: ThemeData(colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal)),
    home: const Wd05OfflinePreviewHome(),
  );
}

class Wd05OfflinePreviewHome extends StatefulWidget {
  const Wd05OfflinePreviewHome({super.key});

  @override
  State<Wd05OfflinePreviewHome> createState() => _Wd05OfflinePreviewHomeState();
}

class _Wd05OfflinePreviewHomeState extends State<Wd05OfflinePreviewHome> {
  String _role = 'admin';

  @override
  Widget build(BuildContext context) {
    final links = wd05AdministrationLinks(role: _role, clinicContextId: 1);
    return Scaffold(
      appBar: AppBar(title: const Text('Clinic System Pro - WD05 UI Preview')),
      body: ListView(
        key: const ValueKey('wd05-offline-preview'),
        padding: const EdgeInsets.all(20),
        children: [
          const Text(
            'OFFLINE UI PREVIEW - SYNTHETIC NAVIGATION ONLY',
            key: ValueKey('wd05-preview-warning'),
          ),
          const Text(
            'No login, backend calls, financial data, reports, file exports or mutations.',
          ),
          const SizedBox(height: 16),
          DropdownButton<String>(
            key: const ValueKey('wd05-preview-role'),
            value: _role,
            isExpanded: true,
            items: const [
              DropdownMenuItem(value: 'admin', child: Text('admin')),
              DropdownMenuItem(value: 'accountant', child: Text('accountant')),
              DropdownMenuItem(value: 'pharmacist', child: Text('pharmacist')),
              DropdownMenuItem(value: 'patient', child: Text('patient')),
            ],
            onChanged: (value) {
              if (value != null) setState(() => _role = value);
            },
          ),
          for (final link in links)
            Card(
              child: ListTile(
                key: ValueKey('wd05-preview-${link.id}'),
                title: Text(link.title),
                subtitle: Text(link.description),
                onTap: () => ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text(
                      'Preview only. Open the real authenticated Windows client to use a server-authorized workflow.',
                    ),
                  ),
                ),
              ),
            ),
          const Divider(),
          for (final capability in wd05PendingAdministrationCapabilities)
            ListTile(
              enabled: false,
              leading: const Icon(Icons.lock_outline),
              title: Text(capability),
            ),
        ],
      ),
    );
  }
}
