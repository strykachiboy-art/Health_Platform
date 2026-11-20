import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Windows-only keyboard affordances around existing authenticated routes.
/// These shortcuts never grant access; callers supply guarded callbacks.
class WindowsWorkstationShortcuts extends StatelessWidget {
  const WindowsWorkstationShortcuts({
    super.key,
    required this.child,
    this.onHome,
    this.onPatients,
    this.onSearch,
  });

  final Widget child;
  final VoidCallback? onHome;
  final VoidCallback? onPatients;
  final VoidCallback? onSearch;

  @override
  Widget build(BuildContext context) {
    if (kIsWeb || defaultTargetPlatform != TargetPlatform.windows) {
      return child;
    }
    final shortcuts = <ShortcutActivator, VoidCallback>{
      const SingleActivator(LogicalKeyboardKey.digit1, control: true): ?onHome,
      const SingleActivator(LogicalKeyboardKey.digit2, control: true):
          ?onPatients,
      const SingleActivator(LogicalKeyboardKey.keyF, control: true): ?onSearch,
    };
    if (shortcuts.isEmpty) return child;
    return CallbackShortcuts(
      bindings: shortcuts,
      child: Focus(autofocus: true, child: child),
    );
  }
}
