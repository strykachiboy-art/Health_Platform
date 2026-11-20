import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/home/presentation/wd04_clinical_workflow_hub.dart';

/// Separate offline-only Windows layout preview. Deliberately does NOT import
/// application bootstrap, router, backend, session, token storage or writers.
/// Selecting a module shows a notice, never an authenticated clinical route.
void main() => runApp(const Wd04OfflinePreviewApp());

class Wd04OfflinePreviewApp extends StatelessWidget {
  const Wd04OfflinePreviewApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Clinic System Pro - Offline UI Preview',
    theme: ThemeData(colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal)),
    home: const Wd04OfflinePreviewHome(),
  );
}

class Wd04OfflinePreviewHome extends StatefulWidget {
  const Wd04OfflinePreviewHome({super.key});

  @override
  State<Wd04OfflinePreviewHome> createState() => _Wd04OfflinePreviewHomeState();
}

class _Wd04OfflinePreviewHomeState extends State<Wd04OfflinePreviewHome> {
  String _role = 'doctor';

  static const _roles = <String>[
    'doctor',
    'nurse',
    'admin',
    'pharmacist',
    'lab_technician',
    'receptionist',
    'paramedic',
    'ambulance_dispatcher',
    'ambulance_coordinator',
    'driver',
    'emt',
    'patient',
  ];

  @override
  Widget build(BuildContext context) {
    // Synthetic clinic ID powers only a pure list generator, never an HTTP
    // request, session object, role claim or token.
    final links = wd04ClinicalWorkflowLinks(role: _role, clinicContextId: 1);
    return Scaffold(
      appBar: AppBar(title: const Text('Clinic System Pro - UI Preview')),
      body: ListView(
        key: const ValueKey('wd04-offline-preview'),
        padding: const EdgeInsets.all(20),
        children: [
          const Text(
            'OFFLINE UI PREVIEW - SAMPLE NAVIGATION ONLY',
            key: ValueKey('wd04-preview-warning'),
          ),
          const SizedBox(height: 8),
          const Text(
            'No login, backend connection, patient data, clinical '
            'operations, scanner capture or printing are available here.',
          ),
          const SizedBox(height: 16),
          DropdownButton<String>(
            key: const ValueKey('wd04-preview-role'),
            value: _role,
            isExpanded: true,
            items: [
              for (final role in _roles)
                DropdownMenuItem(value: role, child: Text(role)),
            ],
            onChanged: (role) {
              if (role != null) setState(() => _role = role);
            },
          ),
          const SizedBox(height: 12),
          for (final link in links)
            Card(
              child: ListTile(
                key: ValueKey('wd04-preview-${link.id}'),
                leading: Icon(link.icon),
                title: Text(link.title),
                subtitle: Text(link.description),
                onTap: () => ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text(
                      'Offline preview only - clinical access requires real backend authentication.',
                    ),
                  ),
                ),
              ),
            ),
          if (links.isEmpty)
            const Text('No clinical module suggestions for this sample role.'),
        ],
      ),
    );
  }
}
