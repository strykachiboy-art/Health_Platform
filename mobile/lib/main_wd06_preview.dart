import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/coordination/presentation/wd06_coordination_hub.dart';

/// Standalone, synthetic, zero-backend Windows coordination layout preview.
/// Does not instantiate a real session, API client, queue, AI or device calls.
void main() => runApp(const Wd06OfflinePreviewApp());

class Wd06OfflinePreviewApp extends StatelessWidget {
  const Wd06OfflinePreviewApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Clinic System Pro - WD06 Offline Preview',
    theme: ThemeData(colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal)),
    home: const Wd06OfflinePreviewHome(),
  );
}

class Wd06OfflinePreviewHome extends StatefulWidget {
  const Wd06OfflinePreviewHome({super.key});

  @override
  State<Wd06OfflinePreviewHome> createState() => _Wd06OfflinePreviewHomeState();
}

class _Wd06OfflinePreviewHomeState extends State<Wd06OfflinePreviewHome> {
  String _role = 'doctor';

  @override
  Widget build(BuildContext context) {
    final links = wd06CoordinationLinks(role: _role, clinicContextId: 1);
    return Scaffold(
      appBar: AppBar(title: const Text('Clinic System Pro - WD06 UI Preview')),
      body: ListView(
        key: const ValueKey('wd06-offline-preview'),
        padding: const EdgeInsets.all(20),
        children: [
          const Text(
            'OFFLINE UI PREVIEW - SAMPLE NAVIGATION ONLY',
            key: ValueKey('wd06-preview-warning'),
          ),
          const Text(
            'No login, backend checks, patient data, chat delivery, notifications, '
            'AI requests, replay, clinical printing or mutations occur here.',
          ),
          const SizedBox(height: 12),
          DropdownButton<String>(
            key: const ValueKey('wd06-preview-role'),
            isExpanded: true,
            value: _role,
            items: const [
              DropdownMenuItem(value: 'doctor', child: Text('doctor')),
              DropdownMenuItem(value: 'admin', child: Text('admin')),
              DropdownMenuItem(value: 'nurse', child: Text('nurse')),
              DropdownMenuItem(value: 'pharmacist', child: Text('pharmacist')),
              DropdownMenuItem(
                value: 'receptionist',
                child: Text('receptionist'),
              ),
              DropdownMenuItem(value: 'patient', child: Text('patient')),
              DropdownMenuItem(
                value: 'super_admin',
                child: Text('super_admin'),
              ),
            ],
            onChanged: (role) {
              if (role != null) setState(() => _role = role);
            },
          ),
          const Divider(),
          for (final link in links)
            Card(
              child: ListTile(
                key: ValueKey('wd06-preview-${link.id}'),
                title: Text(link.title),
                subtitle: Text(link.description),
                onTap: () => ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text(
                      'Offline layout preview only. A real authenticated session is required.',
                    ),
                  ),
                ),
              ),
            ),
          const ListTile(
            enabled: false,
            title: Text('Clinical printing disabled'),
          ),
        ],
      ),
    );
  }
}
