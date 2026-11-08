import 'package:flutter/material.dart';

import 'package:clinic_system_pro/features/nursing/data/models/nursing_care_plan_task.dart';
import 'package:clinic_system_pro/features/nursing/data/nursing_care_plan_task_remote_data_source.dart';

/// P23E.D: online nursing care plans/tasks with confirmed single clinical POST.
/// An attempted write locks further writes until the entire screen is left.
class NursingCarePlansTasksScreen extends StatefulWidget {
  const NursingCarePlansTasksScreen({super.key, required this.dataSource});
  final NursingCarePlanTaskRemoteDataSource dataSource;

  @override
  State<NursingCarePlansTasksScreen> createState() =>
      _NursingCarePlansTasksScreenState();
}

class _NursingCarePlansTasksScreenState
    extends State<NursingCarePlansTasksScreen> {
  final _patient = TextEditingController();
  final _title = TextEditingController();
  final _problem = TextEditingController();
  final _goal = TextEditingController();
  final _description = TextEditingController();
  final _instructions = TextEditingController();
  final _cancelReason = TextEditingController();
  NursingTaskPriority _priority = NursingTaskPriority.routine;
  NursingCareSnapshot? _review;
  NursingCarePlan? _taskPlan;
  String? _message;
  bool _busy = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
  }

  @override
  void didUpdateWidget(covariant NursingCarePlansTasksScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.dataSource, widget.dataSource)) {
      oldWidget.dataSource.sessionNavigationGuard.removeListener(
        _onSessionChanged,
      );
      widget.dataSource.sessionNavigationGuard.addListener(_onSessionChanged);
      _clear();
    }
  }

  void _clear() {
    _generation++;
    _patient.clear();
    _title.clear();
    _problem.clear();
    _goal.clear();
    _description.clear();
    _instructions.clear();
    _cancelReason.clear();
    _priority = NursingTaskPriority.routine;
    _review = null;
    _taskPlan = null;
    _message = null;
    _busy = false;
  }

  void _onSessionChanged() {
    if (!mounted) {
      return;
    }
    setState(_clear);
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_onSessionChanged);
    _patient.dispose();
    _title.dispose();
    _problem.dispose();
    _goal.dispose();
    _description.dispose();
    _instructions.dispose();
    _cancelReason.dispose();
    super.dispose();
  }

  bool _valid(int generation, int revision) =>
      mounted &&
      generation == _generation &&
      widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes &&
      widget.dataSource.sessionNavigationGuard.revision == revision;

  void _patientChanged(String _) {
    if (!mounted) {
      return;
    }
    setState(() {
      _generation++;
      _review = null;
      _taskPlan = null;
      _message = 'Patient ID changed. Review live admission again.';
    });
  }

  Future<void> _inspect() async {
    if (_busy) {
      return;
    }
    final patientId = int.tryParse(_patient.text.trim());
    if (patientId == null || patientId <= 0 || patientId > 2147483647) {
      setState(() {
        _review = null;
        _message = 'Enter a valid patient ID.';
      });
      return;
    }
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = ++_generation;
    setState(() {
      _busy = true;
      _review = null;
      _taskPlan = null;
      _message = null;
    });
    try {
      final result = await widget.dataSource.inspect(patientId: patientId);
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _review = result;
        if (result == null) {
          _message = 'No active admission found for this patient.';
        }
      });
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _message = 'Current nursing readback unavailable. No write sent.';
      });
    }
  }

  Future<void> _perform(
    String title,
    Future<Object> Function(NursingCareSnapshot) operation,
  ) async {
    final review = _review;
    if (review == null ||
        !review.mayWrite ||
        widget.dataSource.writeAttempted ||
        _busy) {
      return;
    }
    final guard = widget.dataSource.sessionNavigationGuard;
    final revision = guard.revision;
    final generation = _generation;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text('Confirm $title?'),
        content: Text(
          'Patient ${review.admission.patientId}, admission '
          '${review.admission.id}. One live nursing action will be sent. '
          'Further writes will be locked on this screen. '
          'No offline queue or automatic retry.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('p23e-d-confirm'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('Confirm once'),
          ),
        ],
      ),
    );
    if (confirmed != true ||
        !_valid(generation, revision) ||
        widget.dataSource.writeAttempted ||
        _busy) {
      return;
    }
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await operation(review);
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _review = null;
        _taskPlan = null;
        _message =
            'Action confirmed by server readback. '
            'Return to the ward dashboard for another clinical action.';
      });
    } on Object {
      if (!_valid(generation, revision)) {
        return;
      }
      setState(() {
        _busy = false;
        _review = null;
        _taskPlan = null;
        _message = widget.dataSource.writeAttempted
            ? 'Outcome not confirmed. No replay. Reconcile the plan/task '
                  'on the server before another clinical action.'
            : 'Clinical state changed or validation failed before POST. '
                  'Review live status again.';
      });
    }
  }

  String _planAction(NursingPlanAction action) => switch (action) {
    NursingPlanAction.hold => 'Hold plan',
    NursingPlanAction.resume => 'Resume plan',
    NursingPlanAction.complete => 'Complete plan',
    NursingPlanAction.cancel => 'Cancel plan',
  };

  String _taskAction(NursingTaskAction action) => switch (action) {
    NursingTaskAction.start => 'Start task',
    NursingTaskAction.complete => 'Complete task',
    NursingTaskAction.cancel => 'Cancel task',
  };

  @override
  Widget build(BuildContext context) {
    final active =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;
    final locked = widget.dataSource.writeAttempted;
    final review = _review;
    final canWrite =
        active && review != null && review.mayWrite && !locked && !_busy;

    return Scaffold(
      appBar: AppBar(title: const Text('Nursing care plans & tasks')),
      body: !active
          ? const Center(
              key: ValueKey('p23e-d-unavailable'),
              child: Text('Nursing session unavailable.'),
            )
          : ListView(
              key: const ValueKey('p23e-d-screen'),
              padding: const EdgeInsets.all(16),
              children: [
                const Text('Online nursing care · no queued clinical writes'),
                TextField(
                  key: const ValueKey('p23e-d-patient'),
                  controller: _patient,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Patient ID'),
                  onChanged: _patientChanged,
                ),
                FilledButton(
                  key: const ValueKey('p23e-d-review'),
                  onPressed: !_busy ? _inspect : null,
                  child: const Text('Review active admission and nursing care'),
                ),
                if (_busy)
                  const LinearProgressIndicator(
                    key: ValueKey('p23e-d-loading'),
                  ),
                if (_message != null)
                  Text(_message!, key: const ValueKey('p23e-d-message')),
                if (locked)
                  const Text(
                    'Writes locked for this screen session. Reopen after reconciliation.',
                    key: ValueKey('p23e-d-write-locked'),
                  ),
                if (review != null) ...[
                  const Divider(),
                  Text(
                    'Patient ${review.admission.patientId} · '
                    'Admission ${review.admission.id}',
                  ),
                  if (!review.mayWrite)
                    const Text(
                      'Read only: nursing mutations require nurse role.',
                    ),
                  const SizedBox(height: 12),
                  Text(
                    'Care plans (${review.plans.length})',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  if (review.plans.isEmpty)
                    const Text('No nursing care plans for this admission.'),
                  for (final plan in review.plans) ...[
                    ListTile(
                      key: ValueKey('p23e-d-plan-${plan.id}'),
                      title: Text(plan.title),
                      subtitle: Text(
                        'Plan ${plan.id} · ${plan.status.name} · '
                        'version ${plan.version}',
                      ),
                    ),
                    Text('Problem: ${plan.problem}'),
                    Text('Goal: ${plan.goal}'),
                    Wrap(
                      spacing: 8,
                      children: [
                        for (final action in NursingPlanAction.values)
                          OutlinedButton(
                            key: ValueKey(
                              'p23e-d-plan-${plan.id}-${action.name}',
                            ),
                            onPressed: canWrite && plan.allows(action)
                                ? () => _perform(
                                    _planAction(action),
                                    (s) => widget.dataSource.changePlan(
                                      reviewed: s,
                                      selected: plan,
                                      action: action,
                                    ),
                                  )
                                : null,
                            child: Text(_planAction(action)),
                          ),
                        OutlinedButton(
                          key: ValueKey('p23e-d-task-for-${plan.id}'),
                          onPressed:
                              canWrite &&
                                  plan.status == NursingPlanStatus.active
                              ? () => setState(() => _taskPlan = plan)
                              : null,
                          child: const Text('Add task to plan'),
                        ),
                      ],
                    ),
                    const Divider(),
                  ],
                  Text(
                    'Create nursing care plan',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  TextField(
                    key: const ValueKey('p23e-d-title'),
                    controller: _title,
                    enabled: canWrite,
                    maxLength: 200,
                    decoration: const InputDecoration(labelText: 'Title'),
                  ),
                  TextField(
                    key: const ValueKey('p23e-d-problem'),
                    controller: _problem,
                    enabled: canWrite,
                    maxLength: 10000,
                    decoration: const InputDecoration(labelText: 'Problem'),
                  ),
                  TextField(
                    key: const ValueKey('p23e-d-goal'),
                    controller: _goal,
                    enabled: canWrite,
                    maxLength: 10000,
                    decoration: const InputDecoration(labelText: 'Goal'),
                  ),
                  FilledButton(
                    key: const ValueKey('p23e-d-create-plan'),
                    onPressed: canWrite
                        ? () => _perform(
                            'create care plan',
                            (s) => widget.dataSource.createPlan(
                              reviewed: s,
                              title: _title.text,
                              problem: _problem.text,
                              goal: _goal.text,
                            ),
                          )
                        : null,
                    child: const Text('Create care plan'),
                  ),
                  if (_taskPlan != null) ...[
                    const Divider(),
                    Text('New task for plan ${_taskPlan!.id}'),
                    TextField(
                      key: const ValueKey('p23e-d-task-description'),
                      controller: _description,
                      enabled: canWrite,
                      maxLength: 10000,
                      decoration: const InputDecoration(
                        labelText: 'Task description',
                      ),
                    ),
                    TextField(
                      key: const ValueKey('p23e-d-task-instructions'),
                      controller: _instructions,
                      enabled: canWrite,
                      maxLength: 10000,
                      decoration: const InputDecoration(
                        labelText: 'Instructions (optional)',
                      ),
                    ),
                    DropdownButton<NursingTaskPriority>(
                      key: const ValueKey('p23e-d-task-priority'),
                      value: _priority,
                      onChanged: canWrite
                          ? (value) {
                              if (value != null) {
                                setState(() => _priority = value);
                              }
                            }
                          : null,
                      items: [
                        for (final choice in NursingTaskPriority.values)
                          DropdownMenuItem(
                            value: choice,
                            child: Text(choice.name),
                          ),
                      ],
                    ),
                    FilledButton(
                      key: const ValueKey('p23e-d-create-task'),
                      onPressed: canWrite
                          ? () => _perform(
                              'create task',
                              (s) => widget.dataSource.createTask(
                                reviewed: s,
                                selectedPlan: _taskPlan!,
                                description: _description.text,
                                instructions: _instructions.text,
                                priority: _priority,
                              ),
                            )
                          : null,
                      child: const Text('Create care task'),
                    ),
                  ],
                  const Divider(),
                  Text(
                    'Care tasks (${review.tasks.length})',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  if (review.tasks.isEmpty)
                    const Text('No nursing care tasks for this admission.'),
                  TextField(
                    key: const ValueKey('p23e-d-cancel-reason'),
                    controller: _cancelReason,
                    enabled: canWrite,
                    maxLength: 10000,
                    decoration: const InputDecoration(
                      labelText:
                          'Task cancellation reason (required to cancel)',
                    ),
                  ),
                  for (final task in review.tasks) ...[
                    ListTile(
                      key: ValueKey('p23e-d-task-${task.id}'),
                      title: Text(task.description),
                      subtitle: Text(
                        'Plan ${task.carePlanId} · ${task.priority.name} '
                        '· ${task.status.name} · version ${task.version}',
                      ),
                    ),
                    Wrap(
                      spacing: 8,
                      children: [
                        for (final action in NursingTaskAction.values)
                          OutlinedButton(
                            key: ValueKey(
                              'p23e-d-task-${task.id}-${action.name}',
                            ),
                            onPressed: canWrite && task.allows(action)
                                ? () => _perform(
                                    _taskAction(action),
                                    (s) => widget.dataSource.changeTask(
                                      reviewed: s,
                                      selected: task,
                                      action: action,
                                      cancellationReason: _cancelReason.text,
                                    ),
                                  )
                                : null,
                            child: Text(_taskAction(action)),
                          ),
                      ],
                    ),
                  ],
                ],
              ],
            ),
    );
  }
}
