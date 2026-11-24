import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/lab/data/lab_order_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/lab/data/models/lab_order_read.dart';

// P23H.S04A: standalone read-only laboratory workspace; no router mutation.
class LabPatientOrdersScreen extends StatefulWidget {
  const LabPatientOrdersScreen({
    super.key,
    required this.patientId,
    required this.dataSource,
    this.pageSize = 20,
    this.onOpenRelease,
  }) : assert(patientId > 0),
       assert(pageSize > 0 && pageSize <= 100);

  final int patientId;
  final LabOrderReadRemoteDataSource dataSource;
  final int pageSize;
  final Future<void> Function(int)? onOpenRelease;

  @override
  State<LabPatientOrdersScreen> createState() => _LabPatientOrdersScreenState();
}

class _LabPatientOrdersScreenState extends State<LabPatientOrdersScreen> {
  List<LabPatientOrder> _orders = const [];
  int? _total;
  int _page = 0;
  int _generation = 0;
  bool _loading = false;
  bool _detailLoading = false;
  String? _error;
  String? _detailError;
  LabPatientOrder? _detail;
  bool _canRelease = false;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_sessionChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        unawaited(_load());
      }
    });
  }

  @override
  void didUpdateWidget(covariant LabPatientOrdersScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.patientId == widget.patientId &&
        identical(oldWidget.dataSource, widget.dataSource) &&
        oldWidget.pageSize == widget.pageSize) {
      return;
    }
    oldWidget.dataSource.sessionNavigationGuard.removeListener(_sessionChanged);
    widget.dataSource.sessionNavigationGuard.addListener(_sessionChanged);
    _generation++;
    _clear();
    _loading = false;
    _error = null;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        unawaited(_load());
      }
    });
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_sessionChanged);
    super.dispose();
  }

  void _clear() {
    _orders = const [];
    _total = null;
    _page = 0;
    _detail = null;
    _detailLoading = false;
    _detailError = null;
    _canRelease = false;
  }

  void _sessionChanged() {
    if (!mounted) {
      return;
    }
    _generation++;
    final available =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;
    setState(() {
      _clear();
      _loading = false;
      _error = available ? null : 'Session unavailable. Lab records cleared.';
    });
    if (available) {
      unawaited(_load());
    }
  }

  bool _current(
    int generation,
    int revision,
    int patientId,
    LabOrderReadRemoteDataSource source,
  ) =>
      mounted &&
      _generation == generation &&
      widget.patientId == patientId &&
      identical(widget.dataSource, source) &&
      source.sessionNavigationGuard.canAccessProtectedRoutes &&
      source.sessionNavigationGuard.revision == revision;

  Future<void> _load({bool more = false}) async {
    if (_loading || (more && (_total == null || _orders.length >= _total!))) {
      return;
    }
    final source = widget.dataSource;
    final guard = source.sessionNavigationGuard;
    if (!guard.canAccessProtectedRoutes) {
      setState(() {
        _generation++;
        _clear();
        _error = 'Session unavailable. Lab records cleared.';
      });
      return;
    }
    final revision = guard.revision;
    final patientId = widget.patientId;
    final generation = ++_generation;
    final page = more ? _page + 1 : 1;
    setState(() {
      if (!more) {
        _clear();
      }
      _loading = true;
      _error = null;
    });
    try {
      final result = await source.fetchPatientOrders(
        patientId: patientId,
        page: page,
        perPage: widget.pageSize,
      );
      if (!_current(generation, revision, patientId, source)) {
        return;
      }
      final merged = more
          ? <LabPatientOrder>[..._orders, ...result.items]
          : <LabPatientOrder>[...result.items];
      if ((more && _total != result.total) ||
          merged.length > result.total ||
          merged.map((item) => item.id).toSet().length != merged.length) {
        throw const FormatException('Inconsistent laboratory order pages.');
      }
      setState(() {
        _orders = List<LabPatientOrder>.unmodifiable(merged);
        _page = result.page;
        _total = result.total;
        _loading = false;
      });
    } catch (error) {
      if (!_current(generation, revision, patientId, source)) {
        return;
      }
      setState(() {
        _clear();
        _loading = false;
        _error = _message(error);
      });
    }
  }

  Future<void> _loadDetail(int orderId) async {
    if (_loading || _detailLoading || _total == null) {
      return;
    }
    final source = widget.dataSource;
    final revision = source.sessionNavigationGuard.revision;
    final patientId = widget.patientId;
    final generation = ++_generation;
    setState(() {
      _detail = null;
      _detailError = null;
      _detailLoading = true;
      _canRelease = false;
    });
    try {
      final value = await source.fetchOrder(
        patientId: patientId,
        orderId: orderId,
      );
      if (!_current(generation, revision, patientId, source)) {
        return;
      }
      if (!_orders.any((order) => order.id == value.id)) {
        throw const FormatException('Lab detail is not on the loaded page.');
      }
      var canRelease = false;
      if (widget.onOpenRelease != null && value.status == LabOrderStatus.completed) {
        final state = await source.loadSession();
        final actor = state.session;
        canRelease = state.disposition == AuthSessionDisposition.ready &&
            actor != null && actor.clinicContextId == value.clinicId &&
            const {'doctor', 'lab_technician'}.contains(actor.role);
      }
      if (!_current(generation, revision, patientId, source)) return;
      setState(() {
        _detailLoading = false;
        _detail = value;
        _canRelease = canRelease;
      });
    } catch (error) {
      if (!_current(generation, revision, patientId, source)) {
        return;
      }
      setState(() {
        _detail = null;
        _detailLoading = false;
        _detailError = _message(error);
      });
    }
  }

  String _message(Object error) {
    if (error is LabReadSessionUnavailable) {
      return 'Session unavailable. Lab records cleared.';
    }
    if (error is ApiException &&
        const <int>{401, 403, 404, 422}.contains(error.statusCode)) {
      return 'Laboratory records are unavailable or access was denied.';
    }
    return 'Laboratory data could not be verified. Retry online.';
  }

  String _status(LabOrderStatus status) => switch (status) {
    LabOrderStatus.ordered => 'Ordered',
    LabOrderStatus.sampleCollected => 'Sample collected',
    LabOrderStatus.inProgress => 'In progress',
    LabOrderStatus.completed => 'Completed',
    LabOrderStatus.cancelled => 'Cancelled',
  };

  Widget _order(LabPatientOrder order, {bool detail = false}) {
    return Padding(
      key: ValueKey(
        detail ? 'lab-detail-${order.id}' : 'lab-order-${order.id}',
      ),
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Lab order #${order.id}'),
          Text('Status: ${_status(order.status)}'),
          Text('Recorded: ${order.createdAt}'),
          for (final item in order.items) ...[
            Text('Test: ${item.test?.name ?? 'Test #${item.testId}'}'),
            if (item.resultValue != null)
              Text('Recorded result: ${item.resultValue}'),
            if (item.flag == LabResultFlag.normal)
              const Text('Result flag: Normal'),
            if (item.flag == LabResultFlag.abnormal)
              const Text('Result flag: Abnormal'),
            if (item.flag == LabResultFlag.critical)
              const Text(
                'CRITICAL RESULT — clinical review required; no acknowledgement recorded here.',
                key: ValueKey('lab-critical-result'),
              ),
            if (item.resultNotes != null)
              Text('Lab notes: ${item.resultNotes}'),
          ],
          if (!detail)
            TextButton(
              key: ValueKey('lab-view-detail-${order.id}'),
              onPressed: () => unawaited(_loadDetail(order.id)),
              child: const Text('Recheck order from server'),
            ),
          const Divider(),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final available =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;
    return Scaffold(
      appBar: AppBar(title: const Text('Patient laboratory orders')),
      body: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Backend-recorded lab results only. No clinical action or critical-result acknowledgement is performed here.',
              ),
              if (_error != null)
                Text(_error!, key: const ValueKey('lab-read-error')),
              if (_loading) const CircularProgressIndicator(),
              if (available && !_loading && _total != null)
                Text('Orders: ${_orders.length} of $_total'),
              if (available && _total == 0)
                const Text(
                  'No orders returned. This is not proof of absent lab history.',
                  key: ValueKey('lab-orders-empty'),
                ),
              for (final order in _orders) _order(order),
              if (_detailLoading)
                const CircularProgressIndicator(
                  key: ValueKey('lab-detail-loading'),
                ),
              if (_detailError != null)
                Text(_detailError!, key: const ValueKey('lab-detail-error')),
              if (_detail != null) ...[
                const Text('Refreshed server order detail'),
                _order(_detail!, detail: true),
                if (_canRelease && widget.onOpenRelease != null)
                  OutlinedButton(
                    key: const ValueKey('lab-open-patient-release'),
                    onPressed: () => unawaited(widget.onOpenRelease!(_detail!.id)),
                    child: const Text('Review patient release'),
                  ),
              ],
              if (available &&
                  !_loading &&
                  _total != null &&
                  _orders.length < _total!)
                OutlinedButton(
                  key: const ValueKey('lab-orders-load-more'),
                  onPressed: () => unawaited(_load(more: true)),
                  child: const Text('Load more lab orders'),
                ),
              if (available && !_loading)
                OutlinedButton(
                  key: const ValueKey('lab-orders-refresh'),
                  onPressed: () => unawaited(_load()),
                  child: const Text('Refresh lab orders'),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
