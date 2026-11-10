import 'dart:async';

import 'package:flutter/material.dart';
import 'package:clinic_system_pro/core/networking/api_exception.dart';
import 'package:clinic_system_pro/features/pharmacy/data/models/pharmacy_stock.dart';
import 'package:clinic_system_pro/features/pharmacy/data/pharmacy_inventory_remote_data_source.dart';

/// Explicitly mounted by a trusted caller; no new router or role grant.
/// Inventory is displayed only after authenticated backend reads.
class PharmacyStockScreen extends StatefulWidget {
  const PharmacyStockScreen({super.key, required this.dataSource});

  final PharmacyInventoryRemoteDataSource dataSource;

  @override
  State<PharmacyStockScreen> createState() => _PharmacyStockScreenState();
}

class _PharmacyStockScreenState extends State<PharmacyStockScreen> {
  PharmacyPage<PharmacyDrug>? _catalog;
  PharmacyStockSnapshot? _stock;
  int? _selectedId;
  String? _error;
  bool _catalogLoading = false;
  bool _stockLoading = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    widget.dataSource.sessionNavigationGuard.addListener(_sessionChanged);
    unawaited(_loadCatalog());
  }

  @override
  void didUpdateWidget(covariant PharmacyStockScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (identical(oldWidget.dataSource, widget.dataSource)) {
      return;
    }
    oldWidget.dataSource.sessionNavigationGuard.removeListener(_sessionChanged);
    widget.dataSource.sessionNavigationGuard.addListener(_sessionChanged);
    _generation++;
    _catalog = null;
    _stock = null;
    _selectedId = null;
    _error = null;
    _catalogLoading = false;
    _stockLoading = false;
    unawaited(_loadCatalog());
  }

  @override
  void dispose() {
    _generation++;
    widget.dataSource.sessionNavigationGuard.removeListener(_sessionChanged);
    super.dispose();
  }

  void _sessionChanged() {
    if (!mounted) {
      return;
    }
    _generation++;
    setState(() {
      _catalog = null;
      _stock = null;
      _selectedId = null;
      _catalogLoading = false;
      _stockLoading = false;
      _error = 'Pharmacy records cleared after session change.';
    });
  }

  String _safeError(Object failure) {
    if (failure is PharmacyInventorySessionUnavailable) {
      return 'Pharmacy access unavailable. Sign in with an authorized role.';
    }
    if (failure is ApiException &&
        const <int>{401, 403, 404}.contains(failure.statusCode)) {
      return 'Pharmacy access denied or stock record unavailable.';
    }
    return 'Pharmacy stock could not be verified from the server.';
  }

  bool _current(
    PharmacyInventoryRemoteDataSource source,
    int generation,
    int revision,
  ) =>
      mounted &&
      identical(source, widget.dataSource) &&
      generation == _generation &&
      source.sessionNavigationGuard.canAccessProtectedRoutes &&
      source.sessionNavigationGuard.revision == revision;

  Future<void> _loadCatalog({bool more = false}) async {
    if (_catalogLoading || _stockLoading) {
      return;
    }
    final old = _catalog;
    if (more && (old == null || !old.hasNext)) {
      return;
    }
    final source = widget.dataSource;
    final revision = source.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _catalogLoading = true;
      _error = null;
      if (!more) {
        _catalog = null;
        _stock = null;
        _selectedId = null;
      }
    });
    try {
      final page = await source.listDrugs(page: more ? old!.page + 1 : 1);
      if (!_current(source, generation, revision)) {
        return;
      }
      final combined = more
          ? <PharmacyDrug>[...old!.items, ...page.items]
          : <PharmacyDrug>[...page.items];
      if (combined.length > page.total ||
          combined.map((drug) => drug.id).toSet().length != combined.length ||
          (more && old!.total != page.total)) {
        throw const FormatException('Conflicting pharmacy catalog pages.');
      }
      setState(() {
        _catalog = PharmacyPage<PharmacyDrug>(
          items: List<PharmacyDrug>.unmodifiable(combined),
          total: page.total,
          page: page.page,
          perPage: page.perPage,
        );
        _catalogLoading = false;
      });
    } catch (failure) {
      if (!_current(source, generation, revision)) {
        return;
      }
      setState(() {
        _catalog = null;
        _stock = null;
        _selectedId = null;
        _catalogLoading = false;
        _error = _safeError(failure);
      });
    }
  }

  Future<void> _loadStock(int drugId) async {
    if (_stockLoading || _catalogLoading || _catalog == null) {
      return;
    }
    if (!_catalog!.items.any((drug) => drug.id == drugId)) {
      return;
    }
    final source = widget.dataSource;
    final revision = source.sessionNavigationGuard.revision;
    final generation = ++_generation;
    setState(() {
      _selectedId = drugId;
      _stock = null;
      _stockLoading = true;
      _error = null;
    });
    try {
      final stock = await source.loadStock(drugId: drugId);
      if (!_current(source, generation, revision) || _selectedId != drugId) {
        return;
      }
      setState(() {
        _stock = stock;
        _stockLoading = false;
      });
    } catch (failure) {
      if (!_current(source, generation, revision)) {
        return;
      }
      setState(() {
        _stock = null;
        _stockLoading = false;
        _error = _safeError(failure);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final catalog = _catalog;
    final stock = _stock;
    return Scaffold(
      appBar: AppBar(title: const Text('Pharmacy stock')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            'Server inventory, read-only. Dispensing is not enabled here.',
          ),
          if (_error != null)
            Text(_error!, key: const ValueKey('p23h-s03a-error')),
          if (_catalogLoading && catalog == null)
            const Center(child: CircularProgressIndicator()),
          if (catalog != null) ...[
            Text('Catalog: ${catalog.items.length} of ${catalog.total}'),
            if (catalog.items.isEmpty)
              const Text('No drugs returned by the backend.'),
            for (final drug in catalog.items)
              ListTile(
                key: ValueKey('p23h-s03a-drug-${drug.id}'),
                title: Text(drug.name),
                subtitle: Text('Category: ${drug.category}'),
                selected: _selectedId == drug.id,
                onTap: _stockLoading || _catalogLoading
                    ? null
                    : () => unawaited(_loadStock(drug.id)),
              ),
            if (_catalogLoading)
              const Center(child: CircularProgressIndicator()),
            if (catalog.hasNext && !_catalogLoading)
              OutlinedButton(
                key: const ValueKey('p23h-s03a-load-more'),
                onPressed: _stockLoading
                    ? null
                    : () => unawaited(_loadCatalog(more: true)),
                child: const Text('Load more drugs'),
              ),
          ],
          if (_stockLoading) const Center(child: CircularProgressIndicator()),
          if (stock != null) ...[
            const Divider(),
            Text(
              'Verified stock: ${stock.drug.name}',
              key: const ValueKey('p23h-s03a-stock-title'),
            ),
            Text('On hand (server summary): ${stock.summary.quantityOnHand}'),
            Text('Batch count (server summary): ${stock.summary.batchCount}'),
            Text(
              'Batches shown: ${stock.batches.items.length} of ${stock.batches.total}',
            ),
            for (final batch in stock.batches.items)
              ListTile(
                key: ValueKey('p23h-s03a-batch-${batch.id}'),
                title: Text('Batch ${batch.batchNumber}'),
                subtitle: Text(
                  'Qty: ${batch.quantityOnHand} • Expires: ${batch.expiryDate}',
                ),
              ),
            if (stock.batches.hasNext)
              const Text(
                'Additional batches exist on the server. Showing the first page.',
              ),
          ],
          const SizedBox(height: 24),
          if (!_catalogLoading &&
              !_stockLoading &&
              widget.dataSource.sessionNavigationGuard.canAccessProtectedRoutes)
            OutlinedButton(
              key: const ValueKey('p23h-s03a-refresh'),
              onPressed: () => unawaited(_loadCatalog()),
              child: const Text('Refresh catalog'),
            ),
        ],
      ),
    );
  }
}
