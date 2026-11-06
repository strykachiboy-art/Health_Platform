import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/home/presentation/authenticated_home_screen.dart';

/// Shared navigation around existing feature routes. It never authenticates a
/// request or grants role/clinic permission; the backend checks every request.
/// The guarded child is not mounted until a current local session is resolved.
class AuthenticatedFeatureFrame extends StatefulWidget {
  const AuthenticatedFeatureFrame({
    super.key,
    required this.child,
    required this.location,
    required this.loadSession,
    this.sessionNavigationGuard,
    this.statusStripBuilder,
  });

  final Widget child;
  final String location;
  final Future<AuthSessionState> Function() loadSession;
  final SessionNavigationGuard? sessionNavigationGuard;
  final Widget Function()? statusStripBuilder;

  @override
  State<AuthenticatedFeatureFrame> createState() =>
      _AuthenticatedFeatureFrameState();
}

class _AuthenticatedFeatureFrameState extends State<AuthenticatedFeatureFrame> {
  String? _role;
  int? _clinicId;
  bool _loading = true;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.sessionNavigationGuard?.addListener(_onSessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant AuthenticatedFeatureFrame oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(
      oldWidget.sessionNavigationGuard,
      widget.sessionNavigationGuard,
    )) {
      oldWidget.sessionNavigationGuard?.removeListener(_onSessionChanged);
      widget.sessionNavigationGuard?.addListener(_onSessionChanged);
    }
    if (!identical(
          oldWidget.sessionNavigationGuard,
          widget.sessionNavigationGuard,
        ) ||
        !identical(oldWidget.loadSession, widget.loadSession)) {
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    widget.sessionNavigationGuard?.removeListener(_onSessionChanged);
    _generation++;
    _role = null;
    _clinicId = null;
    super.dispose();
  }

  void _onSessionChanged() {
    _generation++;
    if (!mounted) return;
    if (widget.sessionNavigationGuard?.canAccessProtectedRoutes == true) {
      unawaited(_load());
    } else {
      setState(() {
        _role = null;
        _clinicId = null;
        _loading = false;
      });
    }
  }

  Future<void> _load() async {
    final generation = ++_generation;
    final guard = widget.sessionNavigationGuard;
    final revision = guard?.revision;
    if (!mounted) return;
    setState(() {
      _loading = true;
      _role = null;
      _clinicId = null;
    });
    if (guard != null && !guard.canAccessProtectedRoutes) {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
      return;
    }
    try {
      final state = await widget.loadSession();
      if (!mounted ||
          generation != _generation ||
          guard?.revision != revision ||
          (guard != null && !guard.canAccessProtectedRoutes)) {
        return;
      }
      final session = state.session;
      if (state.disposition != AuthSessionDisposition.ready ||
          session == null ||
          session.userId <= 0) {
        setState(() => _loading = false);
        return;
      }
      setState(() {
        _role = session.role;
        _clinicId = session.clinicContextId;
        _loading = false;
      });
    } on Object {
      if (!mounted ||
          generation != _generation ||
          guard?.revision != revision) {
        return;
      }
      setState(() => _loading = false);
    }
  }

  void _go(String path) {
    if (_loading ||
        _role == null ||
        widget.sessionNavigationGuard?.canAccessProtectedRoutes == false) {
      return;
    }
    context.go(path);
  }

  @override
  Widget build(BuildContext context) {
    final role = _role;
    final available =
        !_loading &&
        role != null &&
        widget.sessionNavigationGuard?.canAccessProtectedRoutes != false;
    final destinations = available
        ? homeDestinationsFor(role: role, clinicContextId: _clinicId)
        : const <HomeDestination>[];
    final wide = MediaQuery.sizeOf(context).width >= 850;
    final selected = destinations.indexWhere(
      (destination) =>
          widget.location == destination.path ||
          widget.location.startsWith('${destination.path}/'),
    );
    return Scaffold(
      key: const ValueKey('p22-feature-shell'),
      appBar: AppBar(
        title: const Text('Clinic System Pro'),
        leading: IconButton(
          key: const ValueKey('p22-feature-home'),
          tooltip: 'Back to home',
          onPressed: available ? () => _go('/home') : null,
          icon: const Icon(Icons.home_outlined),
        ),
        actions: [
          IconButton(
            key: const ValueKey('p22-s05-open-account-feature'),
            tooltip: 'Profile and clinic',
            onPressed: available ? () => _go('/account') : null,
            icon: const Icon(Icons.person_outline),
          ),
          if (!wide && destinations.isNotEmpty)
            Builder(
              builder: (menuContext) => IconButton(
                key: const ValueKey('p22-feature-menu'),
                tooltip: 'Open navigation menu',
                onPressed: () => Scaffold.of(menuContext).openDrawer(),
                icon: const Icon(Icons.menu),
              ),
            ),
          IconButton(
            key: const ValueKey('p22-feature-session-controls'),
            tooltip: 'Session and sign out',
            onPressed: available ? () => _go('/login?session=1') : null,
            icon: const Icon(Icons.account_circle_outlined),
          ),
        ],
      ),
      drawer: !wide && destinations.isNotEmpty
          ? Drawer(
              child: SafeArea(
                child: ListView(
                  children: [
                    ListTile(
                      key: const ValueKey('p22-feature-drawer-home'),
                      leading: const Icon(Icons.home_outlined),
                      title: const Text('Home'),
                      onTap: () {
                        Navigator.of(context).pop();
                        _go('/home');
                      },
                    ),
                    for (final destination in destinations)
                      ListTile(
                        key: ValueKey('p22-feature-drawer-${destination.id}'),
                        leading: Icon(destination.icon),
                        title: Text(destination.label),
                        onTap: () {
                          Navigator.of(context).pop();
                          _go(destination.path);
                        },
                      ),
                  ],
                ),
              ),
            )
          : null,
      body: Column(
        children: [
          if (widget.statusStripBuilder != null) widget.statusStripBuilder!(),
          Expanded(
            child: _loading
                ? const Center(
                    child: CircularProgressIndicator(
                      key: ValueKey('p22-feature-loading'),
                    ),
                  )
                : !available
                ? const Center(
                    child: Text(
                      'Authenticated session unavailable. Return to sign in.',
                      key: ValueKey('p22-feature-session-unavailable'),
                    ),
                  )
                : Row(
                    children: [
                      if (wide) ...[
                        NavigationRail(
                          selectedIndex: selected < 0 ? null : selected + 1,
                          labelType: NavigationRailLabelType.all,
                          destinations: [
                            const NavigationRailDestination(
                              icon: Icon(Icons.home_outlined),
                              label: Text('Home'),
                            ),
                            for (final destination in destinations)
                              NavigationRailDestination(
                                icon: Icon(destination.icon),
                                label: Text(destination.label),
                              ),
                          ],
                          onDestinationSelected: (index) => _go(
                            index == 0 ? '/home' : destinations[index - 1].path,
                          ),
                        ),
                        const VerticalDivider(width: 1),
                      ],
                      Expanded(child: widget.child),
                    ],
                  ),
          ),
        ],
      ),
    );
  }
}
