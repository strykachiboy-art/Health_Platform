import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/radiology/data/rad7_ris_contract.dart';
import 'package:clinic_system_pro/features/radiology/data/rad7_ris_remote_data_source.dart';

/// RAD7 RIS workspace. A report is a private working note, never a signed
/// diagnostic interpretation. OHIF is deliberately NOT launched from here.
class Rad7RadiologyScreen extends StatefulWidget {
  const Rad7RadiologyScreen({
    super.key,
    required this.source,
    required this.guard,
  });
  final Rad7RisRemoteDataSource source;
  final SessionNavigationGuard guard;

  @override
  State<Rad7RadiologyScreen> createState() => _Rad7RadiologyScreenState();
}

class _Rad7RadiologyScreenState extends State<Rad7RadiologyScreen> {
  final _order = TextEditingController();
  final _examId = TextEditingController();
  final _station = TextEditingController();
  final _when = TextEditingController();
  final _note = TextEditingController();
  Rad7Exam? _exam;
  Rad7Worklist? _worklist;
  Rad7Draft? _draft;
  String? _notice;
  bool _busy = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.guard.addListener(_onSession);
  }

  @override
  void didUpdateWidget(covariant Rad7RadiologyScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.guard, widget.guard)) {
      oldWidget.guard.removeListener(_onSession);
      widget.guard.addListener(_onSession);
    }
    if (!identical(oldWidget.source, widget.source) ||
        !identical(oldWidget.guard, widget.guard)) {
      _onSession();
    }
  }

  void _onSession() {
    _generation++;
    _note.clear();
    _order.clear();
    _examId.clear();
    _station.clear();
    _when.clear();
    if (mounted) {
      setState(() {
        _exam = null;
        _worklist = null;
        _draft = null;
        _busy = false;
        _notice = 'Session changed. Local radiology details were cleared.';
      });
    }
  }

  @override
  void dispose() {
    widget.guard.removeListener(_onSession);
    _generation++;
    _note.dispose();
    _order.dispose();
    _examId.dispose();
    _station.dispose();
    _when.dispose();
    super.dispose();
  }

  int? _id(TextEditingController controller) {
    final value = controller.text.trim();
    final parsed = int.tryParse(value);
    if (parsed == null ||
        parsed <= 0 ||
        parsed > 2147483647 ||
        parsed.toString() != value) {
      return null;
    }
    return parsed;
  }

  bool _valid(int gen, int revision) =>
      mounted &&
      gen == _generation &&
      revision == widget.guard.revision &&
      widget.guard.canAccessProtectedRoutes;

  Future<void> _perform(
    Future<void> Function(bool Function()) action, {
    required String success,
  }) async {
    if (_busy || !widget.guard.canAccessProtectedRoutes) return;
    final gen = ++_generation;
    final revision = widget.guard.revision;
    setState(() {
      _busy = true;
      _notice = null;
    });
    try {
      await action(() => _valid(gen, revision));
      if (!_valid(gen, revision)) return;
      setState(() => _notice = success);
    } on Rad7RisUnconfirmed {
      if (!_valid(gen, revision)) return;
      setState(
        () => _notice = 'Write outcome uncertain. Use authenticated GET readback; do not repeat the write.',
      );
    } on Object {
      if (!_valid(gen, revision)) return;
      setState(
        () => _notice = 'Radiology request unavailable or not authorized.',
      );
    } finally {
      if (_valid(gen, revision)) setState(() => _busy = false);
    }
  }

  void _findByOrder() {
    final id = _id(_order);
    if (id == null) {
      setState(() => _notice = 'Enter a positive imaging order ID.');
      return;
    }
    unawaited(
      _perform((current) async {
        final found = await widget.source.byOrder(id);
        if (!current()) return;
        setState(() {
          _exam = found;
          _examId.text = '${found.examId}';
          _draft = null;
          _worklist = null;
          _note.clear();
        });
      }, success: 'RIS examination readback complete.'),
    );
  }

  void _findByExam() {
    final id = _id(_examId);
    if (id == null) {
      setState(() => _notice = 'Enter a positive RIS exam ID.');
      return;
    }
    unawaited(
      _perform((current) async {
        final found = await widget.source.exam(id);
        if (!current()) return;
        setState(() {
          _exam = found;
          _order.text = '${found.orderId}';
          _draft = null;
          _worklist = null;
          _note.clear();
        });
      }, success: 'RIS examination readback complete.'),
    );
  }

  void _createExam() {
    final id = _id(_order);
    if (id == null) {
      setState(() => _notice = 'Enter a positive imaging order ID.');
      return;
    }
    unawaited(
      _perform(
        (current) async {
          final created = await widget.source.createExam(id);
          if (!current()) return;
          setState(() {
            _exam = created;
            _examId.text = '${created.examId}';
            _draft = null;
            _worklist = null;
          });
        },
        success: 'RIS exam created. This does not publish a modality worklist.',
      ),
    );
  }

  void _loadWorklist() {
    final exam = _exam;
    if (exam == null) return;
    unawaited(
      _perform(
        (current) async {
          final worklist = await widget.source.worklist(exam.examId);
          if (!current()) return;
          setState(() => _worklist = worklist);
        },
        success: 'Planning intent readback only; not published to a modality.',
      ),
    );
  }

  void _schedule() {
    final exam = _exam;
    if (exam == null) return;
    unawaited(
      _perform(
        (current) async {
          final updated = await widget.source.schedule(
            exam,
            scheduledAt: _when.text.trim(),
            stationCode: _station.text.trim(),
          );
          if (!current()) return;
          setState(() {
            _exam = updated;
            _worklist = null;
          });
        },
        success:
            'Scheduling intent recorded. MWL publication is NOT confirmed.',
      ),
    );
  }

  void _cancel() {
    final exam = _exam;
    if (exam == null) return;
    unawaited(
      _perform(
        (current) async {
          final updated = await widget.source.cancel(exam);
          if (!current()) return;
          setState(() {
            _exam = updated;
            _worklist = null;
          });
        },
        success: 'Local RIS scheduling intent cancelled. Modality delivery is unverified.',
      ),
    );
  }

  void _loadDraft() {
    final exam = _exam;
    if (exam == null) return;
    unawaited(
      _perform((current) async {
        final draft = await widget.source.draft(exam.examId);
        if (!current()) return;
        setState(() {
          _draft = draft;
          _note.text = draft.content;
        });
      }, success: 'Private draft history readback complete.'),
    );
  }

  void _saveDraft() {
    final exam = _exam;
    if (exam == null) return;
    final content = _note.text;
    final draft = _draft;
    unawaited(
      _perform(
        (current) async {
          if (draft == null) {
            await widget.source.createDraft(exam.examId, content);
          } else {
            await widget.source.reviseDraft(draft, content);
          }
          if (!current()) return;
          setState(() {
            _draft = null;
            _note.clear();
          });
        },
        success:
            'Private draft accepted. Use GET readback before further edits.',
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final exam = _exam;
    final unlocked = widget.guard.canAccessProtectedRoutes && !_busy;
    return Scaffold(
      appBar: AppBar(title: const Text('Radiology planning & private notes')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            const Text(
              'RIS planning only · NOT diagnostic · NOT archived',
              key: ValueKey('rad7-ris-boundary'),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const ValueKey('rad7-order-id'),
              controller: _order,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: 'Imaging order ID'),
            ),
            Wrap(
              spacing: 8,
              children: [
                OutlinedButton(
                  onPressed: unlocked ? _findByOrder : null,
                  child: const Text('Find by order (GET)'),
                ),
                OutlinedButton(
                  onPressed: unlocked ? _createExam : null,
                  child: const Text('Establish exam once'),
                ),
              ],
            ),
            TextField(
              key: const ValueKey('rad7-exam-id'),
              controller: _examId,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: 'Existing RIS exam ID',
              ),
            ),
            OutlinedButton(
              onPressed: unlocked ? _findByExam : null,
              child: const Text('Read exam (GET)'),
            ),
            if (exam != null) ...[
              const Divider(),
              Text(
                'Exam ${exam.examId} · Order ${exam.orderId} · ${exam.status}',
              ),
              Text('Accession: ${exam.accession} · ${exam.issuer}'),
              Text('Planning version: ${exam.version}'),
              const Text(
                'Modality delivery / image archival / signed report: NOT VERIFIED',
              ),
              TextField(
                key: const ValueKey('rad7-station'),
                controller: _station,
                decoration: const InputDecoration(labelText: 'Station code'),
              ),
              TextField(
                key: const ValueKey('rad7-time'),
                controller: _when,
                decoration: const InputDecoration(
                  labelText: 'Schedule ISO 8601, with timezone',
                  hintText: '2026-12-01T09:00:00Z',
                ),
              ),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  OutlinedButton(
                    onPressed: unlocked ? _schedule : null,
                    child: const Text('Record scheduling intent once'),
                  ),
                  OutlinedButton(
                    onPressed: unlocked ? _cancel : null,
                    child: const Text('Cancel intent once'),
                  ),
                  OutlinedButton(
                    onPressed: unlocked ? _loadWorklist : null,
                    child: const Text('Read worklist intent (GET)'),
                  ),
                ],
              ),
              if (_worklist != null) ...[
                Text('Intent version: ${_worklist!.version}'),
                for (final line in _worklist!.steps) Text(line),
              ],
              const Divider(),
              const Text(
                'PRIVATE DRAFT · UNSIGNED · NOT FOR DIAGNOSIS',
                key: ValueKey('rad7-draft-boundary'),
              ),
              OutlinedButton(
                onPressed: unlocked ? _loadDraft : null,
                child: const Text('Read private draft history (GET)'),
              ),
              if (_draft != null) Text('Current revision: ${_draft!.version}'),
              TextField(
                key: const ValueKey('rad7-private-note'),
                controller: _note,
                maxLength: 20000,
                maxLines: 6,
                decoration: const InputDecoration(
                  labelText: 'Private working note',
                ),
              ),
              OutlinedButton(
                onPressed: unlocked ? _saveDraft : null,
                child: const Text('Save one private revision'),
              ),
              const Text(
                'OHIF image viewer unavailable until clinical study-level grants are approved.',
              ),
            ],
            if (_busy) const LinearProgressIndicator(),
            if (_notice != null)
              Text(_notice!, key: const ValueKey('rad7-notice')),
          ],
        ),
      ),
    );
  }
}
