import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/features/ambulance/data/ambulance_read_remote_data_source.dart';
import 'package:clinic_system_pro/features/ambulance/data/models/ambulance_read.dart';

/// P23I.S05: standalone fleet/trip visibility; no router or mutation actions.
class AmbulanceReadScreen extends StatefulWidget {
  const AmbulanceReadScreen({super.key, required this.dataSource});

  final AmbulanceReadRemoteDataSource dataSource;

  @override
  State<AmbulanceReadScreen> createState() => _AmbulanceReadScreenState();
}

class _AmbulanceReadScreenState extends State<AmbulanceReadScreen> {
  AmbulanceReadKind _kind = AmbulanceReadKind.vehicles;
  AmbulanceReadPage<AmbulanceVehicleRead>? _vehiclesPage;
  AmbulanceReadPage<AmbulanceTripRead>? _tripsPage;
  List<AmbulanceVehicleRead> _vehicles = const [];
  List<AmbulanceTripRead> _trips = const [];
  AmbulanceVehicleRead? _vehicleDetail;
  AmbulanceTripRead? _tripDetail;
  bool _busy = false;
  String? _error;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant AmbulanceReadScreen old) {
    super.didUpdateWidget(old);
    if (identical(old.dataSource, widget.dataSource)) return;
    old.dataSource.sessionNavigationGuard.removeListener(_sessionChanged);
    widget.dataSource.sessionNavigationGuard.addListener(_sessionChanged);
    _generation++;
    _clear();
    _busy = false;
    _error = null;
    unawaited(_load());
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_sessionChanged);
    super.dispose();
  }

  void _clear() {
    _vehiclesPage = null;
    _tripsPage = null;
    _vehicles = const [];
    _trips = const [];
    _vehicleDetail = null;
    _tripDetail = null;
  }

  void _sessionChanged() {
    if (!mounted) return;
    _generation++;
    setState(() {
      _clear();
      _busy = false;
      _error = 'Ambulance records cleared after session change.';
    });
  }

  bool _current(
    AmbulanceReadRemoteDataSource source,
    int generation,
    int revision,
  ) =>
      mounted &&
      identical(widget.dataSource, source) &&
      generation == _generation &&
      source.sessionNavigationGuard.canAccessProtectedRoutes &&
      source.sessionNavigationGuard.revision == revision;

  void _select(AmbulanceReadKind kind) {
    if (_kind == kind) return;
    _generation++;
    setState(() {
      _kind = kind;
      _clear();
      _busy = false;
      _error = null;
    });
    unawaited(_load());
  }

  Future<void> _load({bool more = false}) async {
    if (_busy) return;
    final source = widget.dataSource;
    final revision = source.sessionNavigationGuard.revision;
    final generation = ++_generation;
    final kind = _kind;
    final lastVehiclePage = _vehiclesPage;
    final lastTripPage = _tripsPage;
    if (more &&
        (kind == AmbulanceReadKind.vehicles
            ? lastVehiclePage?.hasNext != true
            : lastTripPage?.hasNext != true)) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _vehicleDetail = null;
      _tripDetail = null;
      if (!more) _clear();
    });
    try {
      if (kind == AmbulanceReadKind.vehicles) {
        final next = await source.fetchVehicles(
          page: more ? lastVehiclePage!.page + 1 : 1,
        );
        if (!_current(source, generation, revision)) return;
        final combined = <AmbulanceVehicleRead>[
          if (more) ..._vehicles,
          ...next.items,
        ];
        if ((more && lastVehiclePage!.total != next.total) ||
            combined.length > next.total ||
            combined.map((v) => v.id).toSet().length != combined.length) {
          throw const FormatException('Conflicting ambulance vehicle pages.');
        }
        setState(() {
          _vehiclesPage = next;
          _vehicles = List<AmbulanceVehicleRead>.unmodifiable(combined);
          _busy = false;
        });
      } else {
        final next = await source.fetchTrips(
          page: more ? lastTripPage!.page + 1 : 1,
        );
        if (!_current(source, generation, revision)) return;
        final combined = <AmbulanceTripRead>[
          if (more) ..._trips,
          ...next.items,
        ];
        if ((more && lastTripPage!.total != next.total) ||
            combined.length > next.total ||
            combined.map((t) => t.id).toSet().length != combined.length) {
          throw const FormatException('Conflicting ambulance trip pages.');
        }
        setState(() {
          _tripsPage = next;
          _trips = List<AmbulanceTripRead>.unmodifiable(combined);
          _busy = false;
        });
      }
    } on Object {
      if (!_current(source, generation, revision)) return;
      setState(() {
        _clear();
        _busy = false;
        _error = 'Current ambulance records unavailable or access denied.';
      });
    }
  }

  Future<void> _detail(int id) async {
    if (_busy) return;
    final source = widget.dataSource;
    final revision = source.sessionNavigationGuard.revision;
    final generation = ++_generation;
    final kind = _kind;
    setState(() {
      _busy = true;
      _vehicleDetail = null;
      _tripDetail = null;
      _error = null;
    });
    try {
      if (kind == AmbulanceReadKind.vehicles) {
        final vehicle = await source.fetchVehicleDetail(id);
        if (!_current(source, generation, revision)) return;
        setState(() {
          _vehicleDetail = vehicle;
          _busy = false;
        });
      } else {
        final trip = await source.fetchTripDetail(id);
        if (!_current(source, generation, revision)) return;
        setState(() {
          _tripDetail = trip;
          _busy = false;
        });
      }
    } on Object {
      if (!_current(source, generation, revision)) return;
      setState(() {
        _clear();
        _busy = false;
        _error = 'Ambulance detail unavailable or access denied.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final selected = _kind;
    final isVehicle = selected == AmbulanceReadKind.vehicles;
    final page = isVehicle ? _vehiclesPage : _tripsPage;
    final total = isVehicle ? _vehicles.length : _trips.length;
    final allowed =
        widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes;
    return Scaffold(
      appBar: AppBar(title: const Text('Ambulance fleet and trips')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            'Staff-only server-confirmed ambulance records. Read-only; no dispatch or live GPS tracking.',
          ),
          Row(
            children: [
              OutlinedButton(
                key: const ValueKey('p23i-s05-vehicles-tab'),
                onPressed: allowed
                    ? () => _select(AmbulanceReadKind.vehicles)
                    : null,
                child: const Text('Fleet'),
              ),
              const SizedBox(width: 8),
              OutlinedButton(
                key: const ValueKey('p23i-s05-trips-tab'),
                onPressed: allowed
                    ? () => _select(AmbulanceReadKind.trips)
                    : null,
                child: const Text('Trips'),
              ),
            ],
          ),
          if (_error != null)
            Text(_error!, key: const ValueKey('p23i-s05-error')),
          if (_busy && page == null)
            const Center(child: CircularProgressIndicator()),
          if (page != null) ...[
            Text(
              'Records shown: $total of ${page.total}',
              key: const ValueKey('p23i-s05-count'),
            ),
            if (total == 0)
              const Text(
                'No ambulance records returned for this clinic.',
                key: ValueKey('p23i-s05-empty'),
              ),
            if (isVehicle)
              for (final v in _vehicles)
                Card(
                  key: ValueKey('p23i-s05-vehicle-${v.id}'),
                  child: ListTile(
                    title: Text('Vehicle ${v.plateNumber}'),
                    subtitle: Text(
                      '${v.status} • ${v.equipmentLevel} • capacity ${v.capacity}',
                    ),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: _busy ? null : () => unawaited(_detail(v.id)),
                  ),
                )
            else
              for (final t in _trips)
                Card(
                  key: ValueKey('p23i-s05-trip-${t.id}'),
                  child: ListTile(
                    title: Text('Trip #${t.id}'),
                    subtitle: Text(
                      '${t.tripType} • ${t.status} • patient ${t.patientId ?? 'unassigned'}',
                    ),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: _busy ? null : () => unawaited(_detail(t.id)),
                  ),
                ),
            if (page.hasNext && !_busy)
              OutlinedButton(
                key: const ValueKey('p23i-s05-more'),
                onPressed: allowed ? () => unawaited(_load(more: true)) : null,
                child: const Text('Load more'),
              ),
          ],
          if (_vehicleDetail != null)
            Card(
              key: const ValueKey('p23i-s05-vehicle-detail'),
              child: ListTile(
                title: Text('Vehicle #${_vehicleDetail!.id} details'),
                subtitle: Text(
                  'Status: ${_vehicleDetail!.status}\n'
                  'Last service: ${_vehicleDetail!.lastServiceDate ?? 'Not recorded'}',
                ),
              ),
            ),
          if (_tripDetail != null)
            Card(
              key: const ValueKey('p23i-s05-trip-detail'),
              child: ListTile(
                title: Text('Trip #${_tripDetail!.id} details'),
                subtitle: Text(
                  'Status: ${_tripDetail!.status}\n'
                  'Vehicle: ${_tripDetail!.vehicleId ?? 'Unassigned'}\n'
                  'Pickup: ${_tripDetail!.pickupAddress ?? 'Not recorded'}\n'
                  'Destination: ${_tripDetail!.destinationAddress ?? 'Not recorded'}',
                ),
              ),
            ),
          if (_busy && page != null)
            const Center(child: CircularProgressIndicator()),
          if (!_busy && allowed)
            OutlinedButton(
              key: const ValueKey('p23i-s05-refresh'),
              onPressed: () => unawaited(_load()),
              child: const Text('Refresh ambulance records'),
            ),
        ],
      ),
    );
  }
}
