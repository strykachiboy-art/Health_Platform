import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../data/auth_remote_data_source.dart';
import '../data/password_reset_link_parser.dart';

/// Public recovery flow. No bearer tokens, offline queue, local storage,
/// logging of credentials, silent retry or implicit login.
class ForgotPasswordScreen extends StatefulWidget {
  const ForgotPasswordScreen({super.key, required this.authRemoteDataSource});

  final AuthRemoteDataSource authRemoteDataSource;

  @override
  State<ForgotPasswordScreen> createState() => _ForgotPasswordScreenState();
}

class _ForgotPasswordScreenState extends State<ForgotPasswordScreen> {
  final _email = TextEditingController();
  bool _busy = false;
  bool _submitted = false;
  String? _message;

  @override
  void dispose() {
    _email.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    if (_busy || _submitted) return;
    final email = _email.text.trim();
    if (email.isEmpty || email.length > 254 || !email.contains('@')) {
      setState(() => _message = 'Enter a valid email address.');
      return;
    }
    setState(() {
      _busy = true;
      _submitted = true;
      _message = null;
    });
    try {
      await widget.authRemoteDataSource.requestPasswordReset(email: email);
      if (!mounted) return;
      setState(
        () => _message = 'If an eligible account exists, reset instructions will be emailed.',
      );
    } catch (_) {
      if (!mounted) return;
      setState(
        () => _message = 'The request could not be confirmed. Check your inbox before starting a new request.',
      );
    } finally {
      if (mounted) {
        _email.clear();
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Forgot password')),
    body: Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 460),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.mark_email_read_outlined, size: 48),
              const SizedBox(height: 16),
              const Text(
                'Enter the email used by your password account. '
                'Google-only accounts should use Google sign-in.',
              ),
              const SizedBox(height: 16),
              TextField(
                key: const ValueKey('auth-r01-forgot-email'),
                controller: _email,
                enabled: !_submitted,
                keyboardType: TextInputType.emailAddress,
                autofillHints: const [AutofillHints.email],
                textInputAction: TextInputAction.done,
                autocorrect: false,
                decoration: const InputDecoration(labelText: 'Email address'),
                onSubmitted: (_) => _send(),
              ),
              const SizedBox(height: 16),
              FilledButton(
                key: const ValueKey('auth-r01-forgot-submit'),
                onPressed: _busy || _submitted ? null : _send,
                child: Text(_busy ? 'Sending...' : 'Send reset instructions'),
              ),
              if (_message != null) ...[
                const SizedBox(height: 16),
                Text(_message!, key: const ValueKey('auth-r01-forgot-result')),
              ],
              const SizedBox(height: 12),
              TextButton(
                key: const ValueKey('auth-r01-open-reset'),
                onPressed: () => context.go('/reset-password'),
                child: const Text('I have my reset link'),
              ),
              TextButton(
                onPressed: () => context.go('/login'),
                child: const Text('Back to sign in'),
              ),
            ],
          ),
        ),
      ),
    ),
  );
}

class ResetPasswordScreen extends StatefulWidget {
  const ResetPasswordScreen({super.key, required this.authRemoteDataSource});

  final AuthRemoteDataSource authRemoteDataSource;

  @override
  State<ResetPasswordScreen> createState() => _ResetPasswordScreenState();
}

class _ResetPasswordScreenState extends State<ResetPasswordScreen> {
  final _link = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  final _factor = TextEditingController();
  bool _busy = false;
  bool _attempted = false;
  bool _success = false;
  bool _showPasswords = false;
  String? _message;

  @override
  void dispose() {
    _link.dispose();
    _password.dispose();
    _confirm.dispose();
    _factor.dispose();
    super.dispose();
  }

  Future<void> _reset() async {
    if (_busy || _attempted) return;
    final PasswordResetCredential credential;
    try {
      credential = PasswordResetLinkParser.parse(_link.text);
    } on FormatException {
      setState(
        () =>
            _message = 'Paste the secure reset link or token from your email.',
      );
      return;
    }
    final newPassword = _password.text;
    final confirmation = _confirm.text;
    final recoveryFactor = _factor.text.trim();
    if (newPassword.length < 8 ||
        newPassword.length > 128 ||
        confirmation != newPassword ||
        newPassword.trim().isEmpty) {
      setState(
        () => _message = 'Enter matching passwords of 8–128 characters.',
      );
      return;
    }
    if (credential.additionalVerificationRequired && recoveryFactor.isEmpty) {
      setState(() => _message = 'Enter your additional recovery verification.');
      return;
    }
    setState(() {
      _busy = true;
      _attempted = true;
      _message = null;
    });
    try {
      await widget.authRemoteDataSource.confirmPasswordReset(
        token: credential.token,
        newPassword: newPassword,
        confirmPassword: confirmation,
        recoveryFactor: recoveryFactor.isEmpty ? null : recoveryFactor,
      );
      if (!mounted) return;
      setState(() {
        _success = true;
        _message = 'Password updated. Sign in with your new password.';
      });
    } catch (_) {
      if (!mounted) return;
      setState(
        () => _message = 'Reset could not be confirmed. Try signing in, or request a new reset link.',
      );
    } finally {
      if (mounted) {
        _link.clear();
        _password.clear();
        _confirm.clear();
        _factor.clear();
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Reset password')),
    body: Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 460),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.password_outlined, size: 48),
              const SizedBox(height: 16),
              const Text(
                'Copy the HTTPS reset link from your email and paste '
                'it here. The credential stays on this screen and is never saved.',
              ),
              const SizedBox(height: 16),
              TextField(
                key: const ValueKey('auth-r01-reset-link'),
                controller: _link,
                enabled: !_attempted,
                obscureText: true,
                autocorrect: false,
                enableSuggestions: false,
                decoration: const InputDecoration(
                  labelText: 'Reset link or token',
                  helperText: 'Paste a secure link; do not share it.',
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                key: const ValueKey('auth-r01-reset-password'),
                controller: _password,
                enabled: !_attempted,
                obscureText: !_showPasswords,
                autocorrect: false,
                enableSuggestions: false,
                decoration: const InputDecoration(labelText: 'New password'),
              ),
              const SizedBox(height: 12),
              TextField(
                key: const ValueKey('auth-r01-reset-confirm'),
                controller: _confirm,
                enabled: !_attempted,
                obscureText: !_showPasswords,
                autocorrect: false,
                enableSuggestions: false,
                decoration: const InputDecoration(
                  labelText: 'Confirm new password',
                ),
              ),
              const SizedBox(height: 8),
              SwitchListTile(
                key: const ValueKey('auth-r01-reset-show-password'),
                contentPadding: EdgeInsets.zero,
                title: const Text('Show passwords'),
                value: _showPasswords,
                onChanged: _attempted
                    ? null
                    : (value) {
                        setState(() => _showPasswords = value);
                      },
              ),
              const SizedBox(height: 12),
              TextField(
                key: const ValueKey('auth-r01-reset-factor'),
                controller: _factor,
                enabled: !_attempted,
                obscureText: true,
                autocorrect: false,
                enableSuggestions: false,
                decoration: const InputDecoration(
                  labelText: 'Additional recovery verification (if required)',
                ),
              ),
              const SizedBox(height: 16),
              FilledButton(
                key: const ValueKey('auth-r01-reset-submit'),
                onPressed: _busy || _attempted ? null : _reset,
                child: Text(_busy ? 'Confirming...' : 'Reset password'),
              ),
              if (_message != null) ...[
                const SizedBox(height: 16),
                Text(_message!, key: const ValueKey('auth-r01-reset-result')),
              ],
              const SizedBox(height: 12),
              TextButton(
                key: const ValueKey('auth-r01-reset-back-login'),
                onPressed: () => context.go('/login'),
                child: Text(_success ? 'Sign in' : 'Back to sign in'),
              ),
              if (!_success)
                TextButton(
                  onPressed: () => context.go('/forgot-password'),
                  child: const Text('Request a new reset link'),
                ),
            ],
          ),
        ),
      ),
    ),
  );
}
