import 'dart:async';

import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/chat/data/clinic_chat_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/chat/data/models/clinic_chat_read_models.dart';

import 'clinic_chat_conversation_screen.dart';

/// CHAT-B: authenticated, in-memory, read-only inbox. Navigation wiring is
/// reserved for CHAT-F, after text-write and realtime phases pass their gates.
class ClinicChatInboxScreen extends StatefulWidget {
  const ClinicChatInboxScreen({super.key, required this.dataSource});

  final ClinicChatReadRemoteDataSource dataSource;

  @override
  State<ClinicChatInboxScreen> createState() => _ClinicChatInboxScreenState();
}

class _ClinicChatInboxScreenState extends State<ClinicChatInboxScreen> {
  final _search = TextEditingController();
  ClinicChatPage<ClinicChatConversation>? _page;
  String? _error;
  String? _appliedSearch;
  bool _loading = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChange);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant ClinicChatInboxScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.dataSource, widget.dataSource)) {
      oldWidget.dataSource.sessionNavigationGuard.removeListener(
        _onSessionChange,
      );
      widget.dataSource.sessionNavigationGuard.addListener(_onSessionChange);
      _clear();
      unawaited(_load());
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onSessionChange);
    _search.dispose();
    super.dispose();
  }

  void _clear() {
    _generation++;
    _page = null;
    _error = null;
    _appliedSearch = null;
    _loading = false;
    _search.clear();
  }

  void _onSessionChange() {
    if (!mounted) {
      return;
    }
    setState(_clear);
    if (widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes) {
      unawaited(_load());
    }
  }

  bool _valid(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.dataSource.sessionNavigationGuard.revision == revision;

  Future<void> _load({int page = 1, String? search}) async {
    final guard = widget.dataSource.sessionNavigationGuard;
    final generation = ++_generation;
    final revision = guard.revision;
    if (!mounted) {
      return;
    }
    setState(() {
      _loading = true;
      _page = null;
      _error = null;
    });
    if (!guard.canAccessProtectedRoutes) {
      if (mounted && generation == _generation) {
        setState(() => _loading = false);
      }
      return;
    }
    try {
      final result = await widget.dataSource.listConversations(
        page: page,
        search: search,
      );
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _page = result;
        _appliedSearch = search;
        _loading = false;
      });
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _page = null;
        _loading = false;
        _error = 'Messages unavailable. Refresh to try again.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final allowed =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;
    final page = _page;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Clinic messages'),
        actions: [
          IconButton(
            key: const ValueKey('chat-b-refresh'),
            tooltip: 'Refresh conversations',
            onPressed: allowed && !_loading
                ? () => _load(page: page?.page ?? 1, search: _appliedSearch)
                : null,
            icon: const Icon(Icons.refresh_outlined),
          ),
        ],
      ),
      body: !allowed
          ? const Center(
              key: ValueKey('chat-b-locked'),
              child: Text('Clinic messages unavailable. Sign in again.'),
            )
          : ListView(
              key: const ValueKey('chat-b-inbox'),
              padding: const EdgeInsets.all(16),
              children: [
                const Text(
                  'Read-only preview · server-authorized conversations',
                ),
                TextField(
                  key: const ValueKey('chat-b-search-field'),
                  controller: _search,
                  maxLength: 200,
                  decoration: const InputDecoration(
                    labelText: 'Search conversations',
                  ),
                ),
                OutlinedButton.icon(
                  key: const ValueKey('chat-b-search'),
                  onPressed: !_loading
                      ? () => _load(search: _search.text.trim())
                      : null,
                  icon: const Icon(Icons.search),
                  label: const Text('Search'),
                ),
                if (_loading)
                  const LinearProgressIndicator(
                    key: ValueKey('chat-b-loading'),
                  ),
                if (_error != null)
                  Text(_error!, key: const ValueKey('chat-b-error')),
                if (!_loading && page != null && page.items.isEmpty)
                  const Text('No authorized conversations found.'),
                if (page != null) ...[
                  for (final convo in page.items)
                    ListTile(
                      key: ValueKey('chat-b-conversation-${convo.id}'),
                      leading: Icon(
                        convo.kind == ClinicChatKind.patient
                            ? Icons.local_hospital_outlined
                            : Icons.forum_outlined,
                      ),
                      title: Text(convo.displayTitle),
                      subtitle: Text(
                        '${convo.kind.name} · ${convo.status.name}',
                      ),
                      onTap: () => Navigator.of(context).push<void>(
                        MaterialPageRoute<void>(
                          builder: (_) => ClinicChatConversationScreen(
                            dataSource: widget.dataSource,
                            conversationId: convo.id,
                          ),
                        ),
                      ),
                    ),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      IconButton(
                        key: const ValueKey('chat-b-previous'),
                        tooltip: 'Previous conversations',
                        onPressed: page.hasPrevious && !_loading
                            ? () => _load(
                                page: page.page - 1,
                                search: _appliedSearch,
                              )
                            : null,
                        icon: const Icon(Icons.chevron_left),
                      ),
                      Text('Page ${page.page} · ${page.total} conversations'),
                      IconButton(
                        key: const ValueKey('chat-b-next'),
                        tooltip: 'Next conversations',
                        onPressed: page.hasNext && !_loading
                            ? () => _load(
                                page: page.page + 1,
                                search: _appliedSearch,
                              )
                            : null,
                        icon: const Icon(Icons.chevron_right),
                      ),
                    ],
                  ),
                ],
              ],
            ),
    );
  }
}
