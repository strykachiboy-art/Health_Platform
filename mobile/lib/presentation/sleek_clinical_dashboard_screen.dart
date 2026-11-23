import 'package:flutter/material.dart';

/// Enterprise Hospital-Grade Doctor Workstation, Staff Profile & Dual Theme System.
/// Optimized with smooth borders, shaded light mode, AI Co-Pilot & 120 FPS rendering.
class SleekClinicalDashboardScreen extends StatefulWidget {
  const SleekClinicalDashboardScreen({super.key});

  @override
  State<SleekClinicalDashboardScreen> createState() =>
      _SleekClinicalDashboardScreenState();
}

class _SleekClinicalDashboardScreenState
    extends State<SleekClinicalDashboardScreen> {
  final ValueNotifier<bool> _emergencyModeNotifier = ValueNotifier<bool>(false);
  final ValueNotifier<int> _activeTabNotifier = ValueNotifier<int>(0);
  final ValueNotifier<int> _unreadNotificationsCount = ValueNotifier<int>(3);
  final ValueNotifier<ThemeMode> _themeModeNotifier =
      ValueNotifier<ThemeMode>(ThemeMode.dark);

  @override
  void dispose() {
    _emergencyModeNotifier.dispose();
    _activeTabNotifier.dispose();
    _unreadNotificationsCount.dispose();
    _themeModeNotifier.dispose();
    super.dispose();
  }

  void _toggleEmergencyMode() {
    _emergencyModeNotifier.value = !_emergencyModeNotifier.value;
    final active = _emergencyModeNotifier.value;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(active
            ? 'CRITICAL: Emergency Break-Glass Mode Enabled. Audit Log #BG-99412 created.'
            : 'Break-Glass emergency mode deactivated.'),
        backgroundColor:
            active ? const Color(0xFFEF4444) : const Color(0xFF10B981),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  void _openNotificationsModal(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Theme.of(context).cardColor,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) {
        return _NotificationsDrawerModal(
          unreadNotifier: _unreadNotificationsCount,
        );
      },
    );
  }

  void _openStaffProfileModal(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Theme.of(context).cardColor,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (context) {
        return _StaffProfileModal(
          themeModeNotifier: _themeModeNotifier,
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<ThemeMode>(
      valueListenable: _themeModeNotifier,
      builder: (context, themeMode, _) {
        final isDark = themeMode == ThemeMode.dark;
        final backgroundColor = isDark ? const Color(0xFF090D16) : const Color(0xFFEBF1F5);

        return Theme(
          data: isDark ? _buildDarkTheme() : _buildLightTheme(),
          child: Scaffold(
            backgroundColor: backgroundColor,
            body: SafeArea(
              child: Column(
                children: [
                  // 1. Emergency Top Banner
                  ValueListenableBuilder<bool>(
                    valueListenable: _emergencyModeNotifier,
                    builder: (context, isEmergency, _) {
                      return isEmergency
                          ? const RepaintBoundary(child: _EmergencyTopBanner())
                          : const SizedBox.shrink();
                    },
                  ),

                  // 2. Doctor Header Bar
                  RepaintBoundary(
                    child: _DoctorHeaderBar(
                      unreadNotificationsNotifier: _unreadNotificationsCount,
                      themeModeNotifier: _themeModeNotifier,
                      onOpenNotifications: () => _openNotificationsModal(context),
                      onOpenStaffProfile: () => _openStaffProfileModal(context),
                    ),
                  ),

                  // 3. Main Workstation Body
                  Expanded(
                    child: LayoutBuilder(
                      builder: (context, constraints) {
                        final isDesktop = constraints.maxWidth >= 900;
                        return Padding(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 20.0, vertical: 12.0),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              // Hero Banner
                              const RepaintBoundary(child: _DoctorHeroBanner()),
                              const SizedBox(height: 12),

                              // Clinical KPI Overview Cards
                              RepaintBoundary(
                                child: _DoctorFullMetricsGrid(isDesktop: isDesktop),
                              ),
                              const SizedBox(height: 12),

                              // Segmented Workspace Tab Bar
                              _DoctorWorkspaceSegmentedBar(
                                  activeTabNotifier: _activeTabNotifier),
                              const SizedBox(height: 12),

                              // Indexed Workspace Views
                              Expanded(
                                child: ValueListenableBuilder<int>(
                                  valueListenable: _activeTabNotifier,
                                  builder: (context, activeTab, _) {
                                    return IndexedStack(
                                      index: activeTab,
                                      children: const [
                                        // Tab 0: Consultations Queue
                                        RepaintBoundary(
                                            child: _DoctorConsultationQueueView()),
                                        // Tab 1: AI Clinical Co-Pilot
                                        RepaintBoundary(
                                            child: _AiClinicalCoPilotView()),
                                        // Tab 2: Triage Alerts Inbox
                                        RepaintBoundary(
                                            child: _ClinicalAlertsInboxView()),
                                        // Tab 3: CPOE Orders & Prescriptions
                                        RepaintBoundary(
                                            child: _CpoeOrdersPrescriptionsView()),
                                        // Tab 4: Active Ward Admissions
                                        RepaintBoundary(
                                            child: _ActiveAdmissionsWardView()),
                                      ],
                                    );
                                  },
                                ),
                              ),
                            ],
                          ),
                        );
                      },
                    ),
                  ),
                ],
              ),
            ),

            // Break-Glass Emergency Floating Action Button
            floatingActionButton: ValueListenableBuilder<bool>(
              valueListenable: _emergencyModeNotifier,
              builder: (context, isEmergency, _) {
                return FloatingActionButton.extended(
                  onPressed: _toggleEmergencyMode,
                  backgroundColor: isEmergency
                      ? const Color(0xFFEF4444)
                      : (isDark ? const Color(0xFF1E293B) : const Color(0xFFCBD5E1)),
                  elevation: 2,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(16),
                  ),
                  icon: Icon(
                    Icons.shield_outlined,
                    color: isEmergency
                        ? Colors.white
                        : (isDark ? const Color(0xFFEF4444) : const Color(0xFFDC2626)),
                  ),
                  label: Text(
                    isEmergency
                        ? 'DEACTIVATE BREAK-GLASS'
                        : 'BREAK-GLASS EMERGENCY ACCESS',
                    style: TextStyle(
                      fontWeight: FontWeight.bold,
                      color: isEmergency
                          ? Colors.white
                          : (isDark ? const Color(0xFFEF4444) : const Color(0xFFDC2626)),
                      letterSpacing: 0.5,
                    ),
                  ),
                );
              },
            ),
          ),
        );
      },
    );
  }

  ThemeData _buildDarkTheme() {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: const Color(0xFF090D16),
      cardColor: const Color(0xFF0F172A),
      dividerColor: const Color(0xFF1E293B),
      colorScheme: const ColorScheme.dark(
        primary: Color(0xFF0EA5E9),
        surface: Color(0xFF0F172A),
        background: Color(0xFF090D16),
        error: Color(0xFFEF4444),
        onPrimary: Colors.white,
        onSurface: Colors.white,
        onBackground: Colors.white,
      ),
    );
  }

  ThemeData _buildLightTheme() {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.light,
      scaffoldBackgroundColor: const Color(0xFFEBF1F5), // Soft Slate-Blue Tint
      cardColor: const Color(0xFFF8FAFC), // Soft Slate-100 Shaded Card
      dividerColor: const Color(0xFFCBD5E1), // Smooth Slate Border
      colorScheme: const ColorScheme.light(
        primary: Color(0xFF0284C7),
        surface: Color(0xFFF8FAFC),
        background: Color(0xFFEBF1F5),
        error: Color(0xFFDC2626),
        onPrimary: Colors.white,
        onSurface: Color(0xFF0F172A),
        onBackground: Color(0xFF0F172A),
      ),
    );
  }
}

class _EmergencyTopBanner extends StatelessWidget {
  const _EmergencyTopBanner();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      color: const Color(0xFFEF4444),
      padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 20),
      child: Row(
        children: const [
          Icon(Icons.warning_rounded, color: Colors.white, size: 20),
          SizedBox(width: 10),
          Expanded(
            child: Text(
              'EMERGENCY "BREAK-GLASS" OVERRIDE ACTIVE — Log ID #BG-99412. Unrestricted patient chart & consultation access granted.',
              style: TextStyle(
                color: Colors.white,
                fontWeight: FontWeight.bold,
                fontSize: 12,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _DoctorHeaderBar extends StatelessWidget {
  final ValueNotifier<int> unreadNotificationsNotifier;
  final ValueNotifier<ThemeMode> themeModeNotifier;
  final VoidCallback onOpenNotifications;
  final VoidCallback onOpenStaffProfile;

  const _DoctorHeaderBar({
    required this.unreadNotificationsNotifier,
    required this.themeModeNotifier,
    required this.onOpenNotifications,
    required this.onOpenStaffProfile,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final textColor = theme.colorScheme.onSurface;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
      decoration: BoxDecoration(
        color: isDark ? const Color(0xFF0F172A) : const Color(0xFFF1F5F9),
        border: Border(bottom: BorderSide(color: theme.dividerColor)),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: isDark ? const Color(0x260EA5E9) : const Color(0x1F0284C7),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(
                      color: isDark ? const Color(0x660EA5E9) : const Color(0x400284C7)),
                ),
                child: Icon(
                  Icons.medical_services_rounded,
                  color: isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7),
                  size: 20,
                ),
              ),
              const SizedBox(width: 12),
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'CLINIC SYSTEM PRO',
                    style: TextStyle(
                      color: textColor,
                      fontSize: 15,
                      fontWeight: FontWeight.w900,
                      letterSpacing: 1,
                    ),
                  ),
                  Text(
                    'St. Jude Hospital — Cardiology & ICU Ward 4A',
                    style: TextStyle(
                      color: isDark ? const Color(0xFF94A3B8) : const Color(0xFF475569),
                      fontSize: 11,
                    ),
                  ),
                ],
              ),
            ],
          ),
          Row(
            children: [
              // Theme Switcher (Dark / Light Mode)
              ValueListenableBuilder<ThemeMode>(
                valueListenable: themeModeNotifier,
                builder: (context, mode, _) {
                  final isCurrentlyDark = mode == ThemeMode.dark;
                  return IconButton(
                    tooltip: isCurrentlyDark ? 'Switch to Light Mode' : 'Switch to Dark Mode',
                    onPressed: () {
                      themeModeNotifier.value =
                          isCurrentlyDark ? ThemeMode.light : ThemeMode.dark;
                    },
                    icon: Icon(
                      isCurrentlyDark ? Icons.light_mode_outlined : Icons.dark_mode_outlined,
                      color: isCurrentlyDark ? const Color(0xFFF59E0B) : const Color(0xFF0284C7),
                      size: 22,
                    ),
                  );
                },
              ),
              const SizedBox(width: 4),

              // Notification Bell Icon
              ValueListenableBuilder<int>(
                valueListenable: unreadNotificationsNotifier,
                builder: (context, count, _) {
                  return Stack(
                    clipBehavior: Clip.none,
                    children: [
                      IconButton(
                        onPressed: onOpenNotifications,
                        icon: Icon(
                          Icons.notifications_none_rounded,
                          color: textColor,
                          size: 22,
                        ),
                      ),
                      if (count > 0)
                        Positioned(
                          right: 6,
                          top: 6,
                          child: Container(
                            padding: const EdgeInsets.all(4),
                            decoration: const BoxDecoration(
                              color: Color(0xFFEF4444),
                              shape: BoxShape.circle,
                            ),
                            constraints: const BoxConstraints(
                              minWidth: 16,
                              minHeight: 16,
                            ),
                            child: Text(
                              '$count',
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 10,
                                fontWeight: FontWeight.bold,
                              ),
                              textAlign: TextAlign.center,
                            ),
                          ),
                        ),
                    ],
                  );
                },
              ),
              const SizedBox(width: 8),

              // AI Co-Pilot Badge
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                decoration: BoxDecoration(
                  color: isDark ? const Color(0x268B5CF6) : const Color(0x268B5CF6),
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(
                      color: isDark ? const Color(0x668B5CF6) : const Color(0x408B5CF6)),
                ),
                child: Row(
                  children: [
                    Icon(Icons.auto_awesome,
                        color: isDark ? const Color(0xFFA78BFA) : const Color(0xFF7C3AED),
                        size: 12),
                    const SizedBox(width: 6),
                    Text(
                      'AI Co-Pilot Active',
                      style: TextStyle(
                        color: isDark ? const Color(0xFFA78BFA) : const Color(0xFF7C3AED),
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),

              // Network Online Badge
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                decoration: BoxDecoration(
                  color: isDark ? const Color(0x2610B981) : const Color(0x2610B981),
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(
                      color: isDark ? const Color(0x6610B981) : const Color(0x4010B981)),
                ),
                child: Row(
                  children: [
                    CircleAvatar(
                        radius: 4,
                        backgroundColor:
                            isDark ? const Color(0xFF10B981) : const Color(0xFF059669)),
                    const SizedBox(width: 6),
                    Text(
                      'Clinic Network Online',
                      style: TextStyle(
                        color: isDark ? const Color(0xFF10B981) : const Color(0xFF059669),
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 12),

              // Interactive Staff Profile Badge
              GestureDetector(
                onTap: onOpenStaffProfile,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                  decoration: BoxDecoration(
                    color: isDark ? const Color(0xFF1E293B) : Colors.white,
                    borderRadius: BorderRadius.circular(24),
                    border: Border.all(color: theme.dividerColor),
                  ),
                  child: Row(
                    children: [
                      const CircleAvatar(
                        radius: 13,
                        backgroundColor: Color(0xFF0EA5E9),
                        child: Text(
                          'AM',
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 10,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                      const SizedBox(width: 6),
                      Text(
                        'Dr. Alex Mercer, MD',
                        style: TextStyle(
                          color: textColor,
                          fontSize: 12,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _DoctorHeroBanner extends StatelessWidget {
  const _DoctorHeroBanner();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final textColor = theme.colorScheme.onSurface;
    final subTextColor = isDark ? const Color(0xFF94A3B8) : const Color(0xFF475569);

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: isDark ? const Color(0xFF0F172A) : const Color(0xFFF1F5F9),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: isDark ? const Color(0xFF0EA5E9) : const Color(0xFF0284C7)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: isDark ? const Color(0x260EA5E9) : const Color(0x1F0284C7),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(
                      color: isDark ? const Color(0x660EA5E9) : const Color(0x400284C7)),
                ),
                child: Text(
                  'DOCTOR CLINICAL WORKSTATION',
                  style: TextStyle(
                    color: isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7),
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    letterSpacing: 1.2,
                  ),
                ),
              ),
              Text(
                'Shift: On-Call Chief Cardiologist',
                style: TextStyle(color: subTextColor, fontSize: 11),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            'Doctor Consultation & AI-Assisted Clinical Workstation',
            style: TextStyle(
              color: textColor,
              fontSize: 18,
              fontWeight: FontWeight.w900,
              letterSpacing: -0.5,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            'Manage patient consultations, review real-time vitals, acknowledge clinical triage alerts, issue STAT CPOE orders & review notifications.',
            style: TextStyle(color: subTextColor, fontSize: 12),
          ),
        ],
      ),
    );
  }
}

class _DoctorFullMetricsGrid extends StatelessWidget {
  final bool isDesktop;

  const _DoctorFullMetricsGrid({required this.isDesktop});

  @override
  Widget build(BuildContext context) {
    const cards = [
      _DoctorMetricCard(
        title: "Today's Appointments",
        value: '14',
        subtitle: '4 Confirmed & Waiting',
        icon: Icons.event_available_outlined,
        accentColor: Color(0xFF38BDF8),
        lightBgColor: Color(0xFFF0F9FF),
      ),
      _DoctorMetricCard(
        title: 'Pending Consultations',
        value: '05',
        subtitle: '2 Ready for Note',
        icon: Icons.medical_information_outlined,
        accentColor: Color(0xFFF59E0B),
        lightBgColor: Color(0xFFFFFBEB),
      ),
      _DoctorMetricCard(
        title: 'AI High-Risk Patients',
        value: '02',
        subtitle: 'STAT Triage Score > 90%',
        icon: Icons.auto_awesome,
        accentColor: Color(0xFFA78BFA),
        lightBgColor: Color(0xFFF5F3FF),
      ),
      _DoctorMetricCard(
        title: 'Active ICU Admissions',
        value: '18 / 20',
        subtitle: '90% Bed Capacity',
        icon: Icons.hotel_outlined,
        accentColor: Color(0xFF8B5CF6),
        lightBgColor: Color(0xFFF3E8FF),
      ),
      _DoctorMetricCard(
        title: 'STAT CPOE Orders',
        value: '03',
        subtitle: '1 Blood Product STAT',
        icon: Icons.assignment_late_outlined,
        accentColor: Color(0xFFEF4444),
        lightBgColor: Color(0xFFFEF2F2),
      ),
    ];

    if (isDesktop) {
      return Row(
        children: cards
            .map((c) => Expanded(
                  child: Padding(
                    padding: const EdgeInsets.only(right: 8.0),
                    child: c,
                  ),
                ))
            .toList(),
      );
    }

    return Column(
      children: cards
          .map((c) => Padding(
                padding: const EdgeInsets.only(bottom: 8.0),
                child: c,
              ))
          .toList(),
    );
  }
}

class _DoctorMetricCard extends StatelessWidget {
  final String title;
  final String value;
  final String subtitle;
  final IconData icon;
  final Color accentColor;
  final Color lightBgColor;

  const _DoctorMetricCard({
    required this.title,
    required this.value,
    required this.subtitle,
    required this.icon,
    required this.accentColor,
    required this.lightBgColor,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final textColor = theme.colorScheme.onSurface;

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: isDark ? theme.cardColor : lightBgColor,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: isDark ? theme.dividerColor : accentColor.withOpacity(0.3),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Icon(icon, color: accentColor, size: 18),
              Text(
                value,
                style: TextStyle(
                  color: textColor,
                  fontSize: 18,
                  fontWeight: FontWeight.w900,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            title,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              color: textColor,
              fontSize: 11,
              fontWeight: FontWeight.bold,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            subtitle,
            style: TextStyle(
              color: accentColor,
              fontSize: 9,
              fontWeight: FontWeight.bold,
            ),
          ),
        ],
      ),
    );
  }
}

class _DoctorWorkspaceSegmentedBar extends StatelessWidget {
  final ValueNotifier<int> activeTabNotifier;

  const _DoctorWorkspaceSegmentedBar({required this.activeTabNotifier});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final unselectedTextColor = isDark ? const Color(0xFF94A3B8) : const Color(0xFF475569);

    return ValueListenableBuilder<int>(
      valueListenable: activeTabNotifier,
      builder: (context, activeTab, _) {
        return Container(
          padding: const EdgeInsets.all(3),
          decoration: BoxDecoration(
            color: isDark ? const Color(0xFF0F172A) : const Color(0xFFF1F5F9),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: theme.dividerColor),
          ),
          child: Row(
            children: [
              Expanded(
                child: _tabButton(
                  index: 0,
                  icon: Icons.person_search_rounded,
                  label: 'Consultations (5)',
                  isSelected: activeTab == 0,
                  unselectedColor: unselectedTextColor,
                ),
              ),
              Expanded(
                child: _tabButton(
                  index: 1,
                  icon: Icons.auto_awesome,
                  label: 'AI Co-Pilot (3)',
                  isSelected: activeTab == 1,
                  unselectedColor: unselectedTextColor,
                ),
              ),
              Expanded(
                child: _tabButton(
                  index: 2,
                  icon: Icons.warning_amber_rounded,
                  label: 'Triage Alerts (3)',
                  isSelected: activeTab == 2,
                  unselectedColor: unselectedTextColor,
                ),
              ),
              Expanded(
                child: _tabButton(
                  index: 3,
                  icon: Icons.medical_services_rounded,
                  label: 'CPOE Orders (3)',
                  isSelected: activeTab == 3,
                  unselectedColor: unselectedTextColor,
                ),
              ),
              Expanded(
                child: _tabButton(
                  index: 4,
                  icon: Icons.hotel_outlined,
                  label: 'ICU Beds (18)',
                  isSelected: activeTab == 4,
                  unselectedColor: unselectedTextColor,
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _tabButton({
    required int index,
    required IconData icon,
    required String label,
    required bool isSelected,
    required Color unselectedColor,
  }) {
    return GestureDetector(
      onTap: () => activeTabNotifier.value = index,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 8),
        decoration: BoxDecoration(
          color: isSelected
              ? (index == 1 ? const Color(0xFF8B5CF6) : const Color(0xFF0EA5E9))
              : Colors.transparent,
          borderRadius: BorderRadius.circular(10),
        ),
        alignment: Alignment.center,
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              icon,
              size: 14,
              color: isSelected ? Colors.white : unselectedColor,
            ),
            const SizedBox(width: 6),
            Text(
              label,
              style: TextStyle(
                color: isSelected ? Colors.white : unselectedColor,
                fontWeight: isSelected ? FontWeight.bold : FontWeight.w500,
                fontSize: 11,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Tab 0: Doctor Patient Consultation Queue
class _DoctorConsultationQueueView extends StatelessWidget {
  const _DoctorConsultationQueueView();

  static const List<_DoctorConsultationPatient> _patients = [
    _DoctorConsultationPatient(
      id: 'PT-99412',
      name: 'Eleanor Vance',
      age: 48,
      gender: 'F',
      room: 'ICU-Bed 04',
      chiefComplaint: 'Acute Dyspnea, SpO2 (87%), Chest Tightness',
      vitals: 'HR: 138 bpm | BP: 168/104 | Temp: 38.9°C | SpO2: 87%',
      allergies: ['Penicillin', 'Sulfa'],
      triageStatus: 'CRITICAL STAT',
      appointmentTime: '09:00 AM (Waiting 12m)',
    ),
    _DoctorConsultationPatient(
      id: 'PT-44109',
      name: 'Marcus Holloway',
      age: 34,
      gender: 'M',
      room: 'Ward-3A (Bed 12)',
      chiefComplaint: 'Post-Op Hypertensive Spike & Dizziness',
      vitals: 'HR: 82 bpm | BP: 165/100 | Temp: 37.1°C | SpO2: 98%',
      allergies: ['Latex'],
      triageStatus: 'URGENT',
      appointmentTime: '09:30 AM (Waiting 5m)',
    ),
    _DoctorConsultationPatient(
      id: 'PT-12093',
      name: 'Sarah Connor',
      age: 52,
      gender: 'F',
      room: 'Recovery-Bed 02',
      chiefComplaint: 'Post-Op Appendectomy Routine Note',
      vitals: 'HR: 74 bpm | BP: 120/80 | Temp: 36.6°C | SpO2: 99%',
      allergies: [],
      triageStatus: 'ROUTINE',
      appointmentTime: '10:00 AM',
    ),
    _DoctorConsultationPatient(
      id: 'PT-88301',
      name: 'David Zhang',
      age: 61,
      gender: 'M',
      room: 'ICU-Bed 01',
      chiefComplaint: 'Sepsis Protocol & Persistent High Fever',
      vitals: 'HR: 118 bpm | BP: 155/96 | Temp: 38.4°C | SpO2: 91%',
      allergies: ['Aspirin'],
      triageStatus: 'CRITICAL STAT',
      appointmentTime: '10:15 AM',
    ),
  ];

  @override
  Widget build(BuildContext context) {
    return ListView.builder(
      physics: const BouncingScrollPhysics(),
      itemCount: _patients.length,
      itemBuilder: (context, index) {
        final patient = _patients[index];
        return RepaintBoundary(
          child: _DoctorConsultationCard(
            patient: patient,
            onStartConsultation: () {
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(
                  content: Text('Starting consultation for ${patient.name}...'),
                  behavior: SnackBarBehavior.floating,
                ),
              );
            },
          ),
        );
      },
    );
  }
}

class _DoctorConsultationCard extends StatelessWidget {
  final _DoctorConsultationPatient patient;
  final VoidCallback onStartConsultation;

  const _DoctorConsultationCard({
    required this.patient,
    required this.onStartConsultation,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    final textColor = theme.colorScheme.onSurface;
    final subTextColor = isDark ? const Color(0xFF94A3B8) : const Color(0xFF475569);

    final isCritical = patient.triageStatus.contains('CRITICAL');
    final accentColor = isCritical
        ? const Color(0xFFEF4444)
        : (patient.triageStatus.contains('URGENT')
            ? const Color(0xFFF59E0B)
            : const Color(0xFF10B981));

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: theme.cardColor,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: isCritical ? const Color(0x66EF4444) : theme.dividerColor,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: accentColor.withOpacity(0.15),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: accentColor.withOpacity(0.4)),
                    ),
                    child: Text(
                      patient.triageStatus,
                      style: TextStyle(
                        color: accentColor,
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Text(
                    patient.name,
                    style: TextStyle(
                      color: textColor,
                      fontSize: 15,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    '(${patient.gender}, ${patient.age}y)',
                    style: TextStyle(color: subTextColor, fontSize: 12),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    '• ${patient.id}',
                    style: TextStyle(
                      color: isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7),
                      fontSize: 12,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ],
              ),
              Row(
                children: [
                  Icon(Icons.access_time_rounded, size: 13, color: subTextColor),
                  const SizedBox(width: 4),
                  Text(
                    patient.appointmentTime,
                    style: TextStyle(color: subTextColor, fontSize: 11),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            'Chief Complaint: ${patient.chiefComplaint}',
            style: TextStyle(
              color: textColor,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'Vitals Stream: ${patient.vitals}',
            style: TextStyle(color: subTextColor, fontSize: 12),
          ),
          if (patient.allergies.isNotEmpty) ...[
            const SizedBox(height: 4),
            Row(
              children: [
                const Icon(Icons.warning_amber_rounded,
                    size: 13, color: Color(0xFFEF4444)),
                const SizedBox(width: 6),
                Text(
                  'ALLERGIES: ${patient.allergies.join(", ")}',
                  style: const TextStyle(
                    color: Color(0xFFEF4444),
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ],
            ),
          ],
          const SizedBox(height: 12),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  Icon(Icons.meeting_room_outlined, size: 13, color: subTextColor),
                  const SizedBox(width: 4),
                  Text(
                    patient.room,
                    style: TextStyle(color: subTextColor, fontSize: 12),
                  ),
                ],
              ),
              Row(
                children: [
                  OutlinedButton.icon(
                    onPressed: () {},
                    style: OutlinedButton.styleFrom(
                      side: BorderSide(color: theme.dividerColor),
                      padding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 6),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(10),
                      ),
                    ),
                    icon: Icon(Icons.folder_shared_outlined,
                        size: 13, color: textColor),
                    label: Text(
                      'Review Chart',
                      style: TextStyle(color: textColor, fontSize: 11),
                    ),
                  ),
                  const SizedBox(width: 8),
                  ElevatedButton.icon(
                    onPressed: onStartConsultation,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFF0EA5E9),
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(
                          horizontal: 12, vertical: 6),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(10),
                      ),
                    ),
                    icon: const Icon(Icons.play_arrow_rounded, size: 15),
                    label: const Text(
                      'Start Consultation',
                      style: TextStyle(
                        fontWeight: FontWeight.bold,
                        fontSize: 11,
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// Tab 1: AI Clinical Co-Pilot Insights
class _AiClinicalCoPilotView extends StatelessWidget {
  const _AiClinicalCoPilotView();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final textColor = theme.colorScheme.onSurface;

    const aiCards = [
      _AiCoPilotCardData(
        title: 'AI Triage Risk Assistant — Eleanor Vance (PT-89201)',
        riskLevel: 'CRITICAL RISK (94% AI Severity Score)',
        insight:
            'Acute SpO2 drop (87%) & Tachycardia (138 bpm). AI Recommendation: Initiate High-Flow O2 & STAT ABG Blood Gas Protocol.',
        accentColor: Color(0xFFEF4444),
        icon: Icons.auto_awesome,
      ),
      _AiCoPilotCardData(
        title: 'AI Lab Result Interpreter — David Zhang (PT-99412)',
        riskLevel: 'ABNORMAL LAB RANGE (Potassium 6.2 mEq/L)',
        insight:
            'Hyperkalemia detected in blood panel. AI Recommendation: Order STAT EKG & Calcium Gluconate IV protocol.',
        accentColor: Color(0xFFF59E0B),
        icon: Icons.biotech_outlined,
      ),
      _AiCoPilotCardData(
        title: 'AI Drug Interaction Safety Shield — Eleanor Vance',
        riskLevel: 'COUNTER-INDICATION ALERT (Penicillin Allergy)',
        insight:
            'Draft order contains Amoxicillin/Clavulanate. AI Recommendation: Replace with Azithromycin to avoid Anaphylaxis.',
        accentColor: Color(0xFFA78BFA),
        icon: Icons.shield_outlined,
      ),
    ];

    return ListView.builder(
      physics: const BouncingScrollPhysics(),
      itemCount: aiCards.length,
      itemBuilder: (context, index) {
        final item = aiCards[index];
        return RepaintBoundary(
          child: Container(
            margin: const EdgeInsets.only(bottom: 10),
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: theme.cardColor,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: const Color(0x668B5CF6)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(item.icon, color: item.accentColor, size: 18),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        item.title,
                        style: TextStyle(
                          color: textColor,
                          fontSize: 13,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: item.accentColor.withOpacity(0.15),
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: item.accentColor.withOpacity(0.4)),
                  ),
                  child: Text(
                    item.riskLevel,
                    style: TextStyle(
                      color: item.accentColor,
                      fontSize: 10,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  item.insight,
                  style: TextStyle(
                    color: textColor.withOpacity(0.85),
                    fontSize: 12,
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _AiCoPilotCardData {
  final String title;
  final String riskLevel;
  final String insight;
  final Color accentColor;
  final IconData icon;

  const _AiCoPilotCardData({
    required this.title,
    required this.riskLevel,
    required this.insight,
    required this.accentColor,
    required this.icon,
  });
}

/// Tab 2: Clinical Triage Alerts Inbox
class _ClinicalAlertsInboxView extends StatelessWidget {
  const _ClinicalAlertsInboxView();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final textColor = theme.colorScheme.onSurface;

    const alerts = [
      _DoctorAlertData(
        patientName: 'Eleanor Vance (PT-89201)',
        room: 'ICU-Bed 04',
        message: 'SpO2 Desaturation to 87%. High-flow oxygen initiated.',
        severity: 'CRITICAL',
        time: '2m ago',
      ),
      _DoctorAlertData(
        patientName: 'David Zhang (PT-99412)',
        room: 'ICU-Bed 01',
        message: 'Tachycardia HR 135 bpm. Sepsis protocol trigger.',
        severity: 'CRITICAL',
        time: '28m ago',
      ),
      _DoctorAlertData(
        patientName: 'Marcus Holloway (PT-44109)',
        room: 'Ward-3A',
        message: 'Systolic Blood Pressure Spike (165/100 mmHg).',
        severity: 'URGENT',
        time: '45m ago',
      ),
    ];

    return ListView.builder(
      physics: const BouncingScrollPhysics(),
      itemCount: alerts.length,
      itemBuilder: (context, index) {
        final a = alerts[index];
        final isCritical = a.severity == 'CRITICAL';
        return RepaintBoundary(
          child: Container(
            margin: const EdgeInsets.only(bottom: 10),
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: theme.cardColor,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(
                color: isCritical ? const Color(0x66EF4444) : theme.dividerColor,
              ),
            ),
            child: Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: isCritical
                        ? const Color(0x26EF4444)
                        : const Color(0x26F59E0B),
                    shape: BoxShape.circle,
                  ),
                  child: Icon(
                    Icons.warning_amber_rounded,
                    color: isCritical
                        ? const Color(0xFFEF4444)
                        : const Color(0xFFF59E0B),
                    size: 18,
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            a.patientName,
                            style: TextStyle(
                              color: textColor,
                              fontSize: 13,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          Text(
                            a.time,
                            style: const TextStyle(
                              color: Color(0xFF64748B),
                              fontSize: 10,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 2),
                      Text(
                        a.message,
                        style: TextStyle(
                          color: textColor.withOpacity(0.85),
                          fontSize: 12,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

/// Tab 3: STAT CPOE Orders & Prescriptions View
class _CpoeOrdersPrescriptionsView extends StatelessWidget {
  const _CpoeOrdersPrescriptionsView();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final textColor = theme.colorScheme.onSurface;

    const orders = [
      _DoctorOrderData(
        id: 'ORD-7710',
        patient: 'Eleanor Vance',
        order: 'STAT 2 Units Packed Red Blood Cells (O-Negative)',
        category: 'Blood Product',
        status: 'STAT — Pending Lab Match',
        color: Color(0xFFEF4444),
      ),
      _DoctorOrderData(
        id: 'ORD-7709',
        patient: 'David Zhang',
        order: 'CT Chest w/ Contrast (Pulmonary Embolism Protocol)',
        category: 'STAT Radiology',
        status: 'In Progress (Radiology Dept)',
        color: Color(0xFFF59E0B),
      ),
      _DoctorOrderData(
        id: 'ORD-7708',
        patient: 'Marcus Holloway',
        order: 'IV Labetalol 20mg STAT + Q2H Blood Pressure Telemetry',
        category: 'Medication Order',
        status: 'Dispensed by Pharmacy',
        color: Color(0xFF0EA5E9),
      ),
    ];

    return ListView.builder(
      physics: const BouncingScrollPhysics(),
      itemCount: orders.length,
      itemBuilder: (context, index) {
        final o = orders[index];
        return RepaintBoundary(
          child: Container(
            margin: const EdgeInsets.only(bottom: 10),
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: theme.cardColor,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: theme.dividerColor),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      o.category.toUpperCase(),
                      style: TextStyle(
                        color: o.color,
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    Text(
                      o.id,
                      style:
                          const TextStyle(color: Color(0xFF64748B), fontSize: 10),
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  o.patient,
                  style: TextStyle(
                    color: textColor,
                    fontSize: 14,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  o.order,
                  style: TextStyle(color: textColor.withOpacity(0.85), fontSize: 12),
                ),
                const SizedBox(height: 8),
                Text(
                  'Status: ${o.status}',
                  style: TextStyle(
                    color: o.color,
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

/// Tab 4: Active ICU Ward Admissions & Bed Telemetry
class _ActiveAdmissionsWardView extends StatelessWidget {
  const _ActiveAdmissionsWardView();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final textColor = theme.colorScheme.onSurface;

    const beds = [
      _WardBedData(
        bedNumber: 'ICU-Bed 01',
        patientName: 'David Zhang',
        diagnosis: 'Sepsis Protocol',
        vitals: 'HR: 118 | BP: 155/96 | Temp: 38.4°C',
        isOccupied: true,
      ),
      _WardBedData(
        bedNumber: 'ICU-Bed 02',
        patientName: 'Sarah Connor',
        diagnosis: 'Post-Appendectomy',
        vitals: 'HR: 74 | BP: 120/80 | Temp: 36.6°C',
        isOccupied: true,
      ),
      _WardBedData(
        bedNumber: 'ICU-Bed 03',
        patientName: 'AVAILABLE',
        diagnosis: 'Sanitized & Ready for Admission',
        vitals: 'N/A',
        isOccupied: false,
      ),
      _WardBedData(
        bedNumber: 'ICU-Bed 04',
        patientName: 'Eleanor Vance',
        diagnosis: 'Acute Respiratory Distress',
        vitals: 'HR: 138 | BP: 168/104 | SpO2: 87%',
        isOccupied: true,
      ),
    ];

    return ListView.builder(
      physics: const BouncingScrollPhysics(),
      itemCount: beds.length,
      itemBuilder: (context, index) {
        final b = beds[index];
        final isAvailable = !b.isOccupied;

        return RepaintBoundary(
          child: Container(
            margin: const EdgeInsets.only(bottom: 10),
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: theme.cardColor,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(
                color: isAvailable
                    ? const Color(0x6610B981)
                    : theme.dividerColor,
              ),
            ),
            child: Row(
              children: [
                Icon(
                  isAvailable ? Icons.king_bed_outlined : Icons.hotel_rounded,
                  color: isAvailable
                      ? const Color(0xFF10B981)
                      : const Color(0xFF38BDF8),
                  size: 20,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            '${b.bedNumber} — ${b.patientName}',
                            style: TextStyle(
                              color: textColor,
                              fontSize: 13,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          Text(
                            isAvailable ? 'AVAILABLE' : 'OCCUPIED',
                            style: TextStyle(
                              color: isAvailable
                                  ? const Color(0xFF10B981)
                                  : const Color(0xFF38BDF8),
                              fontSize: 10,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 2),
                      Text(
                        b.diagnosis,
                        style: const TextStyle(
                          color: Color(0xFF94A3B8),
                          fontSize: 11,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

/// Modal Sheet for Staff Profile & Account Card
class _StaffProfileModal extends StatelessWidget {
  final ValueNotifier<ThemeMode> themeModeNotifier;

  const _StaffProfileModal({required this.themeModeNotifier});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final textColor = theme.colorScheme.onSurface;
    final isDark = theme.brightness == Brightness.dark;

    return Container(
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const CircleAvatar(
                radius: 26,
                backgroundColor: Color(0xFF0EA5E9),
                child: Text(
                  'AM',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 16,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Dr. Alex Mercer, MD',
                      style: TextStyle(
                        color: textColor,
                        fontSize: 18,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const Text(
                      'Staff ID: STF-8821 • License: MD-LIC-99412',
                      style: TextStyle(color: Color(0xFF94A3B8), fontSize: 12),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      'Chief Cardiologist & Attending Physician',
                      style: TextStyle(
                        color: isDark ? const Color(0xFF38BDF8) : const Color(0xFF0284C7),
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),
          const Divider(),
          const SizedBox(height: 12),

          // Theme Selector Row
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  Icon(
                    isDark ? Icons.dark_mode_outlined : Icons.light_mode_outlined,
                    color: isDark ? const Color(0xFFF59E0B) : const Color(0xFF0284C7),
                  ),
                  const SizedBox(width: 12),
                  Text(
                    'Application Theme Mode',
                    style: TextStyle(
                      color: textColor,
                      fontSize: 14,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ],
              ),
              SegmentedButton<ThemeMode>(
                segments: const [
                  ButtonSegment(
                    value: ThemeMode.dark,
                    label: Text('Dark'),
                    icon: Icon(Icons.dark_mode, size: 14),
                  ),
                  ButtonSegment(
                    value: ThemeMode.light,
                    label: Text('Light'),
                    icon: Icon(Icons.light_mode, size: 14),
                  ),
                ],
                selected: {themeModeNotifier.value},
                onSelectionChanged: (selection) {
                  themeModeNotifier.value = selection.first;
                },
              ),
            ],
          ),
          const SizedBox(height: 16),

          // Active Clinic Details
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.business_outlined, color: Color(0xFF0EA5E9)),
            title: Text('Active Clinic Context',
                style: TextStyle(color: textColor, fontWeight: FontWeight.bold)),
            subtitle: const Text('St. Jude Hospital — ICU Ward 4A',
                style: TextStyle(color: Color(0xFF94A3B8))),
            trailing: OutlinedButton(
              onPressed: () {},
              style: OutlinedButton.styleFrom(
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(10),
                ),
              ),
              child: const Text('Switch Clinic'),
            ),
          ),
        ],
      ),
    );
  }
}

/// Modal Sheet for Unread Doctor Notifications & Alerts
class _NotificationsDrawerModal extends StatelessWidget {
  final ValueNotifier<int> unreadNotifier;

  const _NotificationsDrawerModal({required this.unreadNotifier});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final textColor = theme.colorScheme.onSurface;

    return Container(
      padding: const EdgeInsets.all(20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                'Doctor Notifications Hub',
                style: TextStyle(
                  color: textColor,
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                ),
              ),
              TextButton(
                onPressed: () {
                  unreadNotifier.value = 0;
                  Navigator.pop(context);
                },
                child: const Text(
                  'Mark All Read',
                  style: TextStyle(color: Color(0xFF38BDF8)),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          const ListTile(
            leading: Icon(Icons.warning_amber_rounded, color: Color(0xFFEF4444)),
            title: Text('STAT Lab Match Completed',
                style: TextStyle(fontWeight: FontWeight.bold)),
            subtitle: Text('Packed Red Blood Cells (O-Neg) ready for Eleanor Vance',
                style: TextStyle(color: Color(0xFF94A3B8))),
          ),
          const ListTile(
            leading: Icon(Icons.check_circle_outline, color: Color(0xFF10B981)),
            title: Text('CT Chest Radiology Available',
                style: TextStyle(fontWeight: FontWeight.bold)),
            subtitle: Text('Pulmonary Embolism scan results uploaded for David Zhang',
                style: TextStyle(color: Color(0xFF94A3B8))),
          ),
          const ListTile(
            leading: Icon(Icons.shield_outlined, color: Color(0xFFF59E0B)),
            title: Text('Emergency Access Audit Generated',
                style: TextStyle(fontWeight: FontWeight.bold)),
            subtitle: Text('Break-Glass override audit ID #BG-99412 logged',
                style: TextStyle(color: Color(0xFF94A3B8))),
          ),
        ],
      ),
    );
  }
}

class _DoctorConsultationPatient {
  final String id;
  final String name;
  final int age;
  final String gender;
  final String room;
  final String chiefComplaint;
  final String vitals;
  final List<String> allergies;
  final String triageStatus;
  final String appointmentTime;

  const _DoctorConsultationPatient({
    required this.id,
    required this.name,
    required this.age,
    required this.gender,
    required this.room,
    required this.chiefComplaint,
    required this.vitals,
    required this.allergies,
    required this.triageStatus,
    required this.appointmentTime,
  });
}

class _DoctorAlertData {
  final String patientName;
  final String room;
  final String message;
  final String severity;
  final String time;

  const _DoctorAlertData({
    required this.patientName,
    required this.room,
    required this.message,
    required this.severity,
    required this.time,
  });
}

class _DoctorOrderData {
  final String id;
  final String patient;
  final String order;
  final String category;
  final String status;
  final Color color;

  const _DoctorOrderData({
    required this.id,
    required this.patient,
    required this.order,
    required this.category,
    required this.status,
    required this.color,
  });
}

class _WardBedData {
  final String bedNumber;
  final String patientName;
  final String diagnosis;
  final String vitals;
  final bool isOccupied;

  const _WardBedData({
    required this.bedNumber,
    required this.patientName,
    required this.diagnosis,
    required this.vitals,
    required this.isOccupied,
  });
}
