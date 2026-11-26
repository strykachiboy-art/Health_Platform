import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:clinic_system_pro/core/config/backend_config.dart';
import 'package:clinic_system_pro/core/networking/api_client.dart';
import 'package:clinic_system_pro/core/session/session_navigation_guard.dart';
import 'package:clinic_system_pro/features/auth/data/auth_session_coordinator.dart';
import 'package:clinic_system_pro/features/auth/data/models/auth_token_response.dart';
import 'package:clinic_system_pro/features/administration/data/sa_workspace_contract.dart';
import 'package:clinic_system_pro/features/administration/data/sa_workspace_remote.dart';
import 'package:clinic_system_pro/features/administration/data/wd09cf_operations_remote_data_source.dart';

class _Case {
  const _Case(
    this.fields,
    this.path,
    this.verb,
    this.body,
    this.response,
    this.reads,
  );
  final Map<String, String> fields;
  final String path, verb;
  final Map<String, dynamic> body, response;
  final Map<String, Map<String, dynamic>> reads;
}

// Fixed synthetic request and response fixtures, independent of the Dart
// implementation's route construction and projection.
const _cases = <SaOperation, _Case>{
  SaOperation.adminClinics: _Case({}, "/api/v1/clinics", "GET", {}, {
    "data": [
      {
        "id": 11,
        "name": "Synthetic record",
        "status": "active",
        "updated_at": "2030-01-01T00:00:00Z",
      },
    ],
  }, {}),
  SaOperation.adminClinicDetail: _Case(
    {"id": "11"},
    "/api/v1/clinics/11",
    "GET",
    {},
    {
      "data": {
        "id": 11,
        "name": "Synthetic record",
        "status": "active",
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {},
  ),
  SaOperation.adminClinicBranches: _Case(
    {"id": "11"},
    "/api/v1/clinics/11/branches",
    "GET",
    {},
    {
      "data": [
        {
          "id": 11,
          "name": "Synthetic record",
          "status": "active",
          "updated_at": "2030-01-01T00:00:00Z",
          "parent_clinic_id": 11,
        },
      ],
    },
    {},
  ),
  SaOperation.adminClinicProfile: _Case(
    {
      "id": "7",
      "name": "Synthetic name",
      "clinic_type": "general",
      "address": "Synthetic address",
      "city": "Synthetic city",
      "country": "Synthetic country",
      "phone": "Synthetic phone",
      "email": "sample@example.com",
      "timezone": "Synthetic timezone",
    },
    "/api/v1/clinics/7",
    "PATCH",
    {
      "name": "Synthetic name",
      "clinic_type": "general",
      "address": "Synthetic address",
      "city": "Synthetic city",
      "country": "Synthetic country",
      "phone": "Synthetic phone",
      "email": "sample@example.com",
      "timezone": "Synthetic timezone",
    },
    {
      "data": {
        "id": 7,
        "name": "Synthetic name",
        "status": "active",
        "updated_at": "2030-01-01T00:00:00Z",
        "clinic_type": "general",
        "address": "Synthetic address",
        "city": "Synthetic city",
        "country": "Synthetic country",
        "phone": "Synthetic phone",
        "email": "sample@example.com",
        "timezone": "Synthetic timezone",
      },
    },
    {
      "/api/v1/clinics/7": {
        "data": {
          "id": 7,
          "name": "Synthetic record",
          "status": "active",
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminClinicStatus: _Case(
    {"id": "7", "status": "active"},
    "/api/v1/clinics/7/status",
    "PATCH",
    {"status": "active"},
    {
      "data": {
        "id": 7,
        "name": "Synthetic record",
        "status": "active",
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {
      "/api/v1/clinics/7": {
        "data": {
          "id": 7,
          "name": "Synthetic record",
          "status": "active",
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminClinicBranchCreate: _Case(
    {
      "id": "7",
      "name": "Synthetic name",
      "clinic_type": "general",
      "address": "Synthetic address",
      "city": "Synthetic city",
      "country": "Synthetic country",
      "phone": "Synthetic phone",
      "email": "sample@example.com",
      "timezone": "Synthetic timezone",
    },
    "/api/v1/clinics/7/branches",
    "POST",
    {
      "name": "Synthetic name",
      "clinic_type": "general",
      "address": "Synthetic address",
      "city": "Synthetic city",
      "country": "Synthetic country",
      "phone": "Synthetic phone",
      "email": "sample@example.com",
      "timezone": "Synthetic timezone",
    },
    {
      "data": {
        "id": 11,
        "name": "Synthetic name",
        "status": "active",
        "updated_at": "2030-01-01T00:00:00Z",
        "parent_clinic_id": 7,
        "is_headquarters": false,
      },
    },
    {
      "/api/v1/clinics/7": {
        "data": {
          "id": 7,
          "name": "Synthetic record",
          "status": "active",
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminLowStock: _Case(
    {"page": "1", "per_page": "20"},
    "/api/v1/inventory/items/low-stock?page=1&per_page=20",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "items": [
          {
            "id": 11,
            "clinic_id": 7,
            "name": "Synthetic record",
            "quantity_on_hand": 100,
            "is_active": true,
            "updated_at": "2030-01-01T00:00:00Z",
          },
        ],
        "page": 1,
        "per_page": 20,
        "total": 1,
      },
    },
    {},
  ),
  SaOperation.adminItemDeactivate: _Case(
    {"id": "11"},
    "/api/v1/inventory/items/11/deactivate",
    "POST",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "name": "Synthetic record",
        "quantity_on_hand": 100,
        "is_active": false,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {
      "/api/v1/inventory/items/11": {
        "success": true,
        "data": {
          "id": 11,
          "clinic_id": 7,
          "name": "Synthetic record",
          "quantity_on_hand": 100,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminItemReactivate: _Case(
    {"id": "11"},
    "/api/v1/inventory/items/11/reactivate",
    "POST",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "name": "Synthetic record",
        "quantity_on_hand": 100,
        "is_active": true,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {
      "/api/v1/inventory/items/11": {
        "success": true,
        "data": {
          "id": 11,
          "clinic_id": 7,
          "name": "Synthetic record",
          "quantity_on_hand": 100,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminSuppliers: _Case(
    {"page": "1", "per_page": "20", "include_inactive": "true"},
    "/api/v1/inventory/suppliers?page=1&per_page=20&include_inactive=true",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "items": [
          {
            "id": 11,
            "clinic_id": 7,
            "name": "Synthetic record",
            "is_active": true,
            "updated_at": "2030-01-01T00:00:00Z",
          },
        ],
        "page": 1,
        "per_page": 20,
        "total": 1,
      },
    },
    {},
  ),
  SaOperation.adminSupplierDetail: _Case(
    {"id": "11"},
    "/api/v1/inventory/suppliers/11",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "name": "Synthetic record",
        "is_active": true,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {},
  ),
  SaOperation.adminSupplierCreate: _Case(
    {
      "name": "Synthetic name",
      "contact_person": "Synthetic contact person",
      "phone": "Synthetic phone",
      "email": "sample@example.com",
      "address": "Synthetic address",
    },
    "/api/v1/inventory/suppliers",
    "POST",
    {
      "name": "Synthetic name",
      "contact_person": "Synthetic contact person",
      "phone": "Synthetic phone",
      "email": "sample@example.com",
      "address": "Synthetic address",
    },
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "name": "Synthetic name",
        "is_active": true,
        "updated_at": "2030-01-01T00:00:00Z",
        "contact_person": "Synthetic contact person",
        "phone": "Synthetic phone",
        "email": "sample@example.com",
        "address": "Synthetic address",
      },
    },
    {},
  ),
  SaOperation.adminSupplierUpdate: _Case(
    {
      "id": "11",
      "name": "Synthetic name",
      "contact_person": "Synthetic contact person",
      "phone": "Synthetic phone",
      "email": "sample@example.com",
      "address": "Synthetic address",
    },
    "/api/v1/inventory/suppliers/11",
    "PATCH",
    {
      "name": "Synthetic name",
      "contact_person": "Synthetic contact person",
      "phone": "Synthetic phone",
      "email": "sample@example.com",
      "address": "Synthetic address",
    },
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "name": "Synthetic name",
        "is_active": true,
        "updated_at": "2030-01-01T00:00:00Z",
        "contact_person": "Synthetic contact person",
        "phone": "Synthetic phone",
        "email": "sample@example.com",
        "address": "Synthetic address",
      },
    },
    {
      "/api/v1/inventory/suppliers/11": {
        "success": true,
        "data": {
          "id": 11,
          "clinic_id": 7,
          "name": "Synthetic record",
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminSupplierDeactivate: _Case(
    {"id": "11"},
    "/api/v1/inventory/suppliers/11/deactivate",
    "POST",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "name": "Synthetic record",
        "is_active": false,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {
      "/api/v1/inventory/suppliers/11": {
        "success": true,
        "data": {
          "id": 11,
          "clinic_id": 7,
          "name": "Synthetic record",
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminSupplierReactivate: _Case(
    {"id": "11"},
    "/api/v1/inventory/suppliers/11/reactivate",
    "POST",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "name": "Synthetic record",
        "is_active": true,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {
      "/api/v1/inventory/suppliers/11": {
        "success": true,
        "data": {
          "id": 11,
          "clinic_id": 7,
          "name": "Synthetic record",
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminBatches: _Case(
    {
      "item_id": "21",
      "page": "1",
      "per_page": "20",
      "include_inactive": "true",
    },
    "/api/v1/inventory/items/21/batches?page=1&per_page=20&include_inactive=true",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "items": [
          {
            "id": 11,
            "item_id": 21,
            "supplier_id": 51,
            "quantity_on_hand": 100,
            "is_active": true,
            "updated_at": "2030-01-01T00:00:00Z",
          },
        ],
        "page": 1,
        "per_page": 20,
        "total": 1,
      },
    },
    {},
  ),
  SaOperation.adminBatchDetail: _Case(
    {"id": "11", "item_id": "21"},
    "/api/v1/inventory/batches/11",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "item_id": 21,
        "supplier_id": 51,
        "quantity_on_hand": 100,
        "is_active": true,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {},
  ),
  SaOperation.adminBatchCreate: _Case(
    {
      "item_id": "21",
      "batch_number": "Synthetic batch number",
      "supplier_id": "51",
      "unit_cost": "12.50",
      "expiry_date": "2030-01-01",
    },
    "/api/v1/inventory/batches",
    "POST",
    {
      "item_id": 21,
      "batch_number": "Synthetic batch number",
      "supplier_id": 51,
      "unit_cost": "12.50",
      "expiry_date": "2030-01-01",
    },
    {
      "success": true,
      "data": {
        "id": 11,
        "item_id": 21,
        "supplier_id": 51,
        "quantity_on_hand": 0,
        "is_active": true,
        "updated_at": "2030-01-01T00:00:00Z",
        "batch_number": "Synthetic batch number",
        "unit_cost": "12.50",
        "expiry_date": "2030-01-01",
      },
    },
    {
      "/api/v1/inventory/items/21": {
        "success": true,
        "data": {
          "id": 21,
          "clinic_id": 7,
          "name": "Synthetic record",
          "quantity_on_hand": 100,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
      "/api/v1/inventory/suppliers/51": {
        "success": true,
        "data": {
          "id": 51,
          "clinic_id": 7,
          "name": "Synthetic record",
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminBatchUpdate: _Case(
    {
      "id": "11",
      "item_id": "21",
      "batch_number": "Synthetic batch number",
      "supplier_id": "51",
      "unit_cost": "12.50",
      "expiry_date": "2030-01-01",
    },
    "/api/v1/inventory/batches/11",
    "PATCH",
    {
      "batch_number": "Synthetic batch number",
      "supplier_id": 51,
      "unit_cost": "12.50",
      "expiry_date": "2030-01-01",
    },
    {
      "success": true,
      "data": {
        "id": 11,
        "item_id": 21,
        "supplier_id": 51,
        "quantity_on_hand": 100,
        "is_active": true,
        "updated_at": "2030-01-01T00:00:00Z",
        "batch_number": "Synthetic batch number",
        "unit_cost": "12.50",
        "expiry_date": "2030-01-01",
      },
    },
    {
      "/api/v1/inventory/items/21": {
        "success": true,
        "data": {
          "id": 21,
          "clinic_id": 7,
          "name": "Synthetic record",
          "quantity_on_hand": 100,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
      "/api/v1/inventory/batches/11": {
        "success": true,
        "data": {
          "id": 11,
          "item_id": 21,
          "supplier_id": 51,
          "quantity_on_hand": 100,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
      "/api/v1/inventory/suppliers/51": {
        "success": true,
        "data": {
          "id": 51,
          "clinic_id": 7,
          "name": "Synthetic record",
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminExpiringBatches: _Case(
    {"page": "1", "per_page": "20", "days": "30"},
    "/api/v1/inventory/batches/expiring?page=1&per_page=20&days=30",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "items": [
          {
            "id": 11,
            "item_id": 21,
            "supplier_id": 51,
            "quantity_on_hand": 100,
            "is_active": true,
            "updated_at": "2030-01-01T00:00:00Z",
          },
        ],
        "page": 1,
        "per_page": 20,
        "total": 1,
      },
    },
    {},
  ),
  SaOperation.adminStockMovements: _Case(
    {"item_id": "21", "page": "1", "per_page": "20"},
    "/api/v1/inventory/items/21/movements?page=1&per_page=20",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "items": [
          {"id": 11, "item_id": 21, "batch_id": 61, "quantity": 4},
        ],
        "page": 1,
        "per_page": 20,
        "total": 1,
      },
    },
    {},
  ),
  SaOperation.adminStockMovement: _Case(
    {
      "item_id": "21",
      "batch_id": "61",
      "movement_type": "restock",
      "quantity": "4",
      "reason": "Synthetic reason",
    },
    "/api/v1/inventory/movements",
    "POST",
    {
      "item_id": 21,
      "batch_id": 61,
      "movement_type": "restock",
      "quantity": 4,
      "reason": "Synthetic reason",
    },
    {
      "success": true,
      "data": {
        "id": 11,
        "item_id": 21,
        "batch_id": 61,
        "quantity": 4,
        "movement_type": "restock",
        "reason": "Synthetic reason",
        "direction": "in",
      },
    },
    {
      "/api/v1/inventory/items/21": {
        "success": true,
        "data": {
          "id": 21,
          "clinic_id": 7,
          "name": "Synthetic record",
          "quantity_on_hand": 100,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
      "/api/v1/inventory/batches/61": {
        "success": true,
        "data": {
          "id": 61,
          "item_id": 21,
          "supplier_id": 51,
          "quantity_on_hand": 100,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminTransfers: _Case(
    {"page": "1", "per_page": "20", "status": "pending"},
    "/api/v1/inventory/transfers?page=1&per_page=20&status=pending",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "items": [
          {
            "id": 11,
            "source_clinic_id": 7,
            "destination_clinic_id": 8,
            "status": "pending",
            "item_id": 21,
            "batch_id": 61,
            "quantity": 4,
            "requested_by_id": 51,
            "updated_at": "2030-01-01T00:00:00Z",
          },
        ],
        "page": 1,
        "per_page": 20,
        "total": 1,
      },
    },
    {},
  ),
  SaOperation.adminTransferDetail: _Case(
    {"id": "11"},
    "/api/v1/inventory/transfers/11",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "source_clinic_id": 7,
        "destination_clinic_id": 8,
        "status": "pending",
        "item_id": 21,
        "batch_id": 61,
        "quantity": 4,
        "requested_by_id": 51,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {},
  ),
  SaOperation.adminTransferCreate: _Case(
    {
      "item_id": "21",
      "destination_clinic_id": "8",
      "quantity": "4",
      "batch_id": "61",
      "reason": "Synthetic reason",
    },
    "/api/v1/inventory/transfers",
    "POST",
    {
      "item_id": 21,
      "destination_clinic_id": 8,
      "quantity": 4,
      "batch_id": 61,
      "reason": "Synthetic reason",
    },
    {
      "success": true,
      "data": {
        "id": 11,
        "source_clinic_id": 7,
        "destination_clinic_id": 8,
        "status": "pending",
        "item_id": 21,
        "batch_id": 61,
        "quantity": 4,
        "requested_by_id": 51,
        "updated_at": "2030-01-01T00:00:00Z",
        "reason": "Synthetic reason",
      },
    },
    {
      "/api/v1/inventory/items/21": {
        "success": true,
        "data": {
          "id": 21,
          "clinic_id": 7,
          "name": "Synthetic record",
          "quantity_on_hand": 100,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
      "/api/v1/inventory/batches/61": {
        "success": true,
        "data": {
          "id": 61,
          "item_id": 21,
          "supplier_id": 51,
          "quantity_on_hand": 100,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminTransferApprove: _Case(
    {"id": "11"},
    "/api/v1/inventory/transfers/11/approve",
    "POST",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "source_clinic_id": 7,
        "destination_clinic_id": 8,
        "status": "approved",
        "item_id": 21,
        "batch_id": 61,
        "quantity": 4,
        "requested_by_id": 51,
        "updated_at": "2030-01-01T00:00:00Z",
        "approved_by_id": 52,
      },
    },
    {
      "/api/v1/inventory/transfers/11": {
        "success": true,
        "data": {
          "id": 11,
          "source_clinic_id": 7,
          "destination_clinic_id": 8,
          "status": "pending",
          "item_id": 21,
          "batch_id": 61,
          "quantity": 4,
          "requested_by_id": 51,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminTransferComplete: _Case(
    {"id": "11"},
    "/api/v1/inventory/transfers/11/complete",
    "POST",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "source_clinic_id": 7,
        "destination_clinic_id": 8,
        "status": "completed",
        "item_id": 21,
        "batch_id": 61,
        "quantity": 4,
        "requested_by_id": 51,
        "updated_at": "2030-01-01T00:00:00Z",
        "approved_by_id": 52,
      },
    },
    {
      "/api/v1/inventory/transfers/11": {
        "success": true,
        "data": {
          "id": 11,
          "source_clinic_id": 7,
          "destination_clinic_id": 8,
          "status": "approved",
          "item_id": 21,
          "batch_id": 61,
          "quantity": 4,
          "requested_by_id": 51,
          "updated_at": "2030-01-01T00:00:00Z",
          "approved_by_id": 52,
        },
      },
    },
  ),
  SaOperation.adminTransferCancel: _Case(
    {"id": "11", "reason": "Synthetic reason"},
    "/api/v1/inventory/transfers/11/cancel",
    "POST",
    {"reason": "Synthetic reason"},
    {
      "success": true,
      "data": {
        "id": 11,
        "source_clinic_id": 7,
        "destination_clinic_id": 8,
        "status": "cancelled",
        "item_id": 21,
        "batch_id": 61,
        "quantity": 4,
        "requested_by_id": 51,
        "updated_at": "2030-01-01T00:00:00Z",
        "reason": "Synthetic reason",
      },
    },
    {
      "/api/v1/inventory/transfers/11": {
        "success": true,
        "data": {
          "id": 11,
          "source_clinic_id": 7,
          "destination_clinic_id": 8,
          "status": "pending",
          "item_id": 21,
          "batch_id": 61,
          "quantity": 4,
          "requested_by_id": 51,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminAssetAssignments: _Case(
    {"asset_id": "41", "page": "1", "per_page": "20"},
    "/api/v1/assets/41/assignments?page=1&per_page=20",
    "GET",
    {},
    {
      "success": true,
      "items": [
        {
          "id": 11,
          "clinic_id": 7,
          "asset_id": 41,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      ],
      "page": 1,
      "per_page": 20,
      "total": 1,
    },
    {},
  ),
  SaOperation.adminAssetAssignmentsDetail: _Case(
    {"id": "11"},
    "/api/v1/assets/assignments/11",
    "GET",
    {},
    {
      "success": true,
      "assignment": {
        "id": 11,
        "clinic_id": 7,
        "asset_id": 41,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {},
  ),
  SaOperation.adminAssetHistory: _Case(
    {"asset_id": "41", "page": "1", "per_page": "20"},
    "/api/v1/assets/41/history?page=1&per_page=20",
    "GET",
    {},
    {
      "success": true,
      "items": [
        {"id": 11, "clinic_id": 7, "asset_id": 41},
      ],
      "page": 1,
      "per_page": 20,
      "total": 1,
    },
    {},
  ),
  SaOperation.adminAssetHistoryDetail: _Case(
    {"id": "11"},
    "/api/v1/assets/history/11",
    "GET",
    {},
    {
      "success": true,
      "history": {"id": 11, "clinic_id": 7, "asset_id": 41},
    },
    {},
  ),
  SaOperation.adminAssetMaintenance: _Case(
    {"asset_id": "41", "page": "1", "per_page": "20"},
    "/api/v1/assets/41/maintenance?page=1&per_page=20",
    "GET",
    {},
    {
      "success": true,
      "items": [
        {
          "id": 11,
          "clinic_id": 7,
          "asset_id": 41,
          "status": "active",
          "updated_at": "2030-01-01T00:00:00Z",
        },
      ],
      "page": 1,
      "per_page": 20,
      "total": 1,
    },
    {},
  ),
  SaOperation.adminAssetMaintenanceDetail: _Case(
    {"id": "11"},
    "/api/v1/assets/maintenance/11",
    "GET",
    {},
    {
      "success": true,
      "maintenance": {
        "id": 11,
        "clinic_id": 7,
        "asset_id": 41,
        "status": "active",
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {},
  ),
  SaOperation.adminMaintenanceSchedule: _Case(
    {
      "asset_id": "41",
      "scheduled_date": "2030-01-01",
      "description": "Synthetic description",
      "notes": "Synthetic notes",
    },
    "/api/v1/assets/41/maintenance",
    "POST",
    {
      "scheduled_date": "2030-01-01",
      "description": "Synthetic description",
      "notes": "Synthetic notes",
    },
    {
      "success": true,
      "maintenance": {
        "id": 11,
        "clinic_id": 7,
        "asset_id": 41,
        "status": "active",
        "updated_at": "2030-01-01T00:00:00Z",
        "description": "Synthetic description",
        "scheduled_date": "2030-01-01",
        "notes": "Synthetic notes",
      },
    },
    {
      "/api/v1/assets/41": {
        "success": true,
        "asset": {
          "id": 41,
          "clinic_id": 7,
          "name": "Synthetic record",
          "status": "active",
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminMaintenanceStart: _Case(
    {"id": "11", "notes": "Synthetic notes"},
    "/api/v1/assets/maintenance/11/start",
    "POST",
    {"notes": "Synthetic notes"},
    {
      "success": true,
      "maintenance": {
        "id": 11,
        "clinic_id": 7,
        "asset_id": 41,
        "status": "in_progress",
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {
      "/api/v1/assets/maintenance/11": {
        "success": true,
        "maintenance": {
          "id": 11,
          "clinic_id": 7,
          "asset_id": 41,
          "status": "scheduled",
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminMaintenanceComplete: _Case(
    {"id": "11", "cost": "12.50", "notes": "Synthetic notes"},
    "/api/v1/assets/maintenance/11/complete",
    "POST",
    {"cost": "12.50", "notes": "Synthetic notes"},
    {
      "success": true,
      "maintenance": {
        "id": 11,
        "clinic_id": 7,
        "asset_id": 41,
        "status": "completed",
        "updated_at": "2030-01-01T00:00:00Z",
        "cost": "12.50",
      },
    },
    {
      "/api/v1/assets/maintenance/11": {
        "success": true,
        "maintenance": {
          "id": 11,
          "clinic_id": 7,
          "asset_id": 41,
          "status": "in_progress",
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminMaintenanceCancel: _Case(
    {"id": "11", "reason": "Synthetic reason"},
    "/api/v1/assets/maintenance/11/cancel",
    "POST",
    {"reason": "Synthetic reason"},
    {
      "success": true,
      "maintenance": {
        "id": 11,
        "clinic_id": 7,
        "asset_id": 41,
        "status": "cancelled",
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {
      "/api/v1/assets/maintenance/11": {
        "success": true,
        "maintenance": {
          "id": 11,
          "clinic_id": 7,
          "asset_id": 41,
          "status": "scheduled",
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminFamilyList: _Case(
    {"patient_id": "31", "page": "1", "per_page": "20"},
    "/api/v1/patients/31/family?page=1&per_page=20",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "items": [
          {
            "id": 11,
            "patient_id": 31,
            "related_patient_id": 32,
            "updated_at": "2030-01-01T00:00:00Z",
          },
        ],
        "page": 1,
        "per_page": 20,
        "total": 1,
      },
    },
    {},
  ),
  SaOperation.adminFamilyCreate: _Case(
    {
      "patient_id": "31",
      "full_name": "Synthetic full name",
      "relation": "spouse",
      "phone": "Synthetic phone",
      "is_emergency_contact": "true",
      "related_patient_id": "32",
    },
    "/api/v1/patients/31/family",
    "POST",
    {
      "full_name": "Synthetic full name",
      "relation": "spouse",
      "phone": "Synthetic phone",
      "is_emergency_contact": true,
      "related_patient_id": 32,
    },
    {
      "success": true,
      "data": {
        "id": 11,
        "patient_id": 31,
        "related_patient_id": 32,
        "updated_at": "2030-01-01T00:00:00Z",
        "full_name": "Synthetic full name",
        "relation": "spouse",
        "phone": "Synthetic phone",
        "is_emergency_contact": true,
      },
    },
    {
      "/api/v1/patients/31": {
        "success": true,
        "data": {
          "id": 31,
          "clinic_id": 7,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
      "/api/v1/patients/32": {
        "success": true,
        "data": {
          "id": 32,
          "clinic_id": 7,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminFamilyUpdate: _Case(
    {
      "patient_id": "31",
      "id": "11",
      "lookup_page": "1",
      "full_name": "Synthetic full name",
      "relation": "spouse",
      "phone": "Synthetic phone",
      "is_emergency_contact": "true",
      "related_patient_id": "32",
    },
    "/api/v1/patients/31/family/11",
    "PATCH",
    {
      "full_name": "Synthetic full name",
      "relation": "spouse",
      "phone": "Synthetic phone",
      "is_emergency_contact": true,
      "related_patient_id": 32,
    },
    {
      "success": true,
      "data": {
        "id": 11,
        "patient_id": 31,
        "related_patient_id": 32,
        "updated_at": "2030-01-01T00:00:00Z",
        "full_name": "Synthetic full name",
        "relation": "spouse",
        "phone": "Synthetic phone",
        "is_emergency_contact": true,
      },
    },
    {
      "/api/v1/patients/31": {
        "success": true,
        "data": {
          "id": 31,
          "clinic_id": 7,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
      "/api/v1/patients/31/family?page=1&per_page=20": {
        "success": true,
        "data": {
          "items": [
            {
              "id": 11,
              "patient_id": 31,
              "related_patient_id": 32,
              "updated_at": "2030-01-01T00:00:00Z",
            },
          ],
          "page": 1,
          "per_page": 20,
          "total": 1,
        },
      },
      "/api/v1/patients/32": {
        "success": true,
        "data": {
          "id": 32,
          "clinic_id": 7,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminFamilyRemove: _Case(
    {"patient_id": "31", "id": "11", "lookup_page": "1"},
    "/api/v1/patients/31/family/11",
    "DELETE",
    {},
    {"success": true, "message": "Family member removed successfully"},
    {
      "/api/v1/patients/31": {
        "success": true,
        "data": {
          "id": 31,
          "clinic_id": 7,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
      "/api/v1/patients/31/family?page=1&per_page=20": {
        "success": true,
        "data": {
          "items": [
            {
              "id": 11,
              "patient_id": 31,
              "related_patient_id": 32,
              "updated_at": "2030-01-01T00:00:00Z",
            },
          ],
          "page": 1,
          "per_page": 20,
          "total": 1,
        },
      },
    },
  ),
  SaOperation.adminInsuranceList: _Case(
    {"patient_id": "31", "page": "1", "per_page": "20"},
    "/api/v1/patients/31/insurance?page=1&per_page=20",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "items": [
          {
            "id": 11,
            "patient_id": 31,
            "is_active": true,
            "updated_at": "2030-01-01T00:00:00Z",
          },
        ],
        "page": 1,
        "per_page": 20,
        "total": 1,
      },
    },
    {},
  ),
  SaOperation.adminInsuranceCreate: _Case(
    {
      "patient_id": "31",
      "provider_name": "Synthetic provider name",
      "policy_number": "Synthetic policy number",
      "plan_type": "Synthetic plan type",
      "coverage_start": "2030-01-01",
      "coverage_end": "2030-12-31",
      "is_primary": "true",
      "is_active": "true",
    },
    "/api/v1/patients/31/insurance",
    "POST",
    {
      "provider_name": "Synthetic provider name",
      "policy_number": "Synthetic policy number",
      "plan_type": "Synthetic plan type",
      "coverage_start": "2030-01-01",
      "coverage_end": "2030-12-31",
      "is_primary": true,
      "is_active": true,
    },
    {
      "success": true,
      "data": {
        "id": 11,
        "patient_id": 31,
        "is_active": true,
        "updated_at": "2030-01-01T00:00:00Z",
        "provider_name": "Synthetic provider name",
        "policy_number": "Synthetic policy number",
        "plan_type": "Synthetic plan type",
        "coverage_start": "2030-01-01",
        "coverage_end": "2030-12-31",
        "is_primary": true,
      },
    },
    {
      "/api/v1/patients/31": {
        "success": true,
        "data": {
          "id": 31,
          "clinic_id": 7,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminInsuranceUpdate: _Case(
    {
      "patient_id": "31",
      "id": "11",
      "lookup_page": "1",
      "provider_name": "Synthetic provider name",
      "policy_number": "Synthetic policy number",
      "plan_type": "Synthetic plan type",
      "coverage_start": "2030-01-01",
      "coverage_end": "2030-12-31",
      "is_primary": "true",
      "is_active": "true",
    },
    "/api/v1/patients/31/insurance/11",
    "PATCH",
    {
      "provider_name": "Synthetic provider name",
      "policy_number": "Synthetic policy number",
      "plan_type": "Synthetic plan type",
      "coverage_start": "2030-01-01",
      "coverage_end": "2030-12-31",
      "is_primary": true,
      "is_active": true,
    },
    {
      "success": true,
      "data": {
        "id": 11,
        "patient_id": 31,
        "is_active": true,
        "updated_at": "2030-01-01T00:00:00Z",
        "provider_name": "Synthetic provider name",
        "policy_number": "Synthetic policy number",
        "plan_type": "Synthetic plan type",
        "coverage_start": "2030-01-01",
        "coverage_end": "2030-12-31",
        "is_primary": true,
      },
    },
    {
      "/api/v1/patients/31": {
        "success": true,
        "data": {
          "id": 31,
          "clinic_id": 7,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
      "/api/v1/patients/31/insurance?page=1&per_page=20": {
        "success": true,
        "data": {
          "items": [
            {
              "id": 11,
              "patient_id": 31,
              "is_active": true,
              "updated_at": "2030-01-01T00:00:00Z",
            },
          ],
          "page": 1,
          "per_page": 20,
          "total": 1,
        },
      },
    },
  ),
  SaOperation.adminSettingsInitialize: _Case(
    {},
    "/api/v1/settings/clinic/initialize",
    "POST",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "is_enabled": true,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {},
  ),
  SaOperation.adminSettingsEnable: _Case(
    {},
    "/api/v1/settings/clinic/enable",
    "POST",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "is_enabled": true,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {
      "/api/v1/settings/clinic": {
        "success": true,
        "data": {
          "id": 11,
          "clinic_id": 7,
          "is_enabled": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminSettingsDisable: _Case(
    {},
    "/api/v1/settings/clinic/disable",
    "POST",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "is_enabled": false,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {
      "/api/v1/settings/clinic": {
        "success": true,
        "data": {
          "id": 11,
          "clinic_id": 7,
          "is_enabled": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
  SaOperation.adminIntegrations: _Case(
    {"page": "1", "per_page": "20", "include_disabled": "true"},
    "/api/v1/settings/integrations?page=1&per_page=20&include_disabled=true",
    "GET",
    {},
    {
      "success": true,
      "data": [
        {
          "id": 11,
          "clinic_id": 7,
          "is_enabled": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      ],
      "pagination": {"page": 1, "per_page": 20, "total": 1},
    },
    {},
  ),
  SaOperation.adminIntegrationById: _Case(
    {"id": "11"},
    "/api/v1/settings/integrations/id/11",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "is_enabled": true,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {},
  ),
  SaOperation.adminIntegrationDetail: _Case(
    {"provider": "sample-provider"},
    "/api/v1/settings/integrations/sample-provider",
    "GET",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "is_enabled": true,
        "updated_at": "2030-01-01T00:00:00Z",
        "provider": "sample-provider",
      },
    },
    {},
  ),
  SaOperation.adminIntegrationEnable: _Case(
    {"provider": "sample-provider"},
    "/api/v1/settings/integrations/sample-provider/enable",
    "POST",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "is_enabled": true,
        "updated_at": "2030-01-01T00:00:00Z",
        "provider": "sample-provider",
      },
    },
    {
      "/api/v1/settings/integrations/sample-provider": {
        "success": true,
        "data": {
          "id": 11,
          "clinic_id": 7,
          "is_enabled": true,
          "updated_at": "2030-01-01T00:00:00Z",
          "provider": "sample-provider",
        },
      },
    },
  ),
  SaOperation.adminIntegrationDisable: _Case(
    {"provider": "sample-provider"},
    "/api/v1/settings/integrations/sample-provider/disable",
    "POST",
    {},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "is_enabled": false,
        "updated_at": "2030-01-01T00:00:00Z",
        "provider": "sample-provider",
      },
    },
    {
      "/api/v1/settings/integrations/sample-provider": {
        "success": true,
        "data": {
          "id": 11,
          "clinic_id": 7,
          "is_enabled": true,
          "updated_at": "2030-01-01T00:00:00Z",
          "provider": "sample-provider",
        },
      },
    },
  ),
  SaOperation.adminWardCreate: _Case(
    {"name": "Synthetic name", "ward_type": "general", "capacity": "12"},
    "/api/v1/wards",
    "POST",
    {"name": "Synthetic name", "ward_type": "general", "capacity": 12},
    {
      "ward": {
        "id": 11,
        "clinic_id": 7,
        "name": "Synthetic name",
        "capacity": 12,
        "updated_at": "2030-01-01T00:00:00Z",
        "ward_type": "general",
      },
    },
    {},
  ),
  SaOperation.adminWardUpdate: _Case(
    {
      "id": "11",
      "name": "Synthetic name",
      "ward_type": "general",
      "capacity": "12",
    },
    "/api/v1/wards/11",
    "PATCH",
    {"name": "Synthetic name", "ward_type": "general", "capacity": 12},
    {
      "ward": {
        "id": 11,
        "clinic_id": 7,
        "name": "Synthetic name",
        "capacity": 12,
        "updated_at": "2030-01-01T00:00:00Z",
        "ward_type": "general",
      },
    },
    {
      "/api/v1/wards/11": {
        "id": 11,
        "clinic_id": 7,
        "name": "Synthetic record",
        "capacity": 12,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
  ),
  SaOperation.adminBedCreate: _Case(
    {"ward_id": "22", "bed_number": "Synthetic bed number"},
    "/api/v1/wards/22/beds",
    "POST",
    {"bed_number": "Synthetic bed number"},
    {
      "bed": {
        "id": 11,
        "ward_id": 22,
        "status": "active",
        "updated_at": "2030-01-01T00:00:00Z",
        "bed_number": "Synthetic bed number",
      },
    },
    {
      "/api/v1/wards/22": {
        "id": 22,
        "clinic_id": 7,
        "name": "Synthetic record",
        "capacity": 12,
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
  ),
  SaOperation.adminBedMaintenance: _Case(
    {"id": "11", "ward_id": "22", "under_maintenance": "true"},
    "/api/v1/wards/beds/11/maintenance",
    "PATCH",
    {"under_maintenance": true},
    {
      "bed": {
        "id": 11,
        "ward_id": 22,
        "status": "maintenance",
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {
      "/api/v1/wards/22": {
        "id": 22,
        "clinic_id": 7,
        "name": "Synthetic record",
        "capacity": 12,
        "updated_at": "2030-01-01T00:00:00Z",
      },
      "/api/v1/wards/beds/11": {
        "id": 11,
        "ward_id": 22,
        "status": "available",
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
  ),
  SaOperation.adminReservations: _Case(
    {
      "page": "1",
      "per_page": "20",
      "patient_id": "31",
      "bed_id": "23",
      "status": "pending",
    },
    "/api/v1/wards/reservations?page=1&per_page=20&patient_id=31&bed_id=23&status=pending",
    "GET",
    {},
    {
      "items": [
        {
          "id": 11,
          "patient_id": 31,
          "bed_id": 23,
          "status": "pending",
          "updated_at": "2030-01-01T00:00:00Z",
        },
      ],
      "page": 1,
      "per_page": 20,
      "total": 1,
    },
    {},
  ),
  SaOperation.adminReservationDetail: _Case(
    {"id": "11"},
    "/api/v1/wards/reservations/11",
    "GET",
    {},
    {
      "id": 11,
      "patient_id": 31,
      "bed_id": 23,
      "status": "active",
      "updated_at": "2030-01-01T00:00:00Z",
    },
    {},
  ),
  SaOperation.adminBedReservation: _Case(
    {"bed_id": "23"},
    "/api/v1/wards/beds/23/reservation",
    "GET",
    {},
    {
      "id": 11,
      "patient_id": 31,
      "bed_id": 23,
      "status": "active",
      "updated_at": "2030-01-01T00:00:00Z",
    },
    {},
  ),
  SaOperation.adminReservationCancel: _Case(
    {"id": "11", "patient_id": "31", "reason": "Synthetic reason"},
    "/api/v1/wards/reservations/11/cancel",
    "POST",
    {"reason": "Synthetic reason"},
    {
      "reservation": {
        "id": 11,
        "patient_id": 31,
        "bed_id": 23,
        "status": "cancelled",
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
    {
      "/api/v1/patients/31": {
        "success": true,
        "data": {
          "id": 31,
          "clinic_id": 7,
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
      "/api/v1/wards/reservations/11": {
        "id": 11,
        "patient_id": 31,
        "bed_id": 23,
        "status": "pending",
        "updated_at": "2030-01-01T00:00:00Z",
      },
    },
  ),
  SaOperation.adminTheatreCreate: _Case(
    {"code": "Synthetic code", "display_name": "Synthetic display name"},
    "/api/v1/surgery/theatres",
    "POST",
    {"code": "Synthetic code", "display_name": "Synthetic display name"},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "is_active": true,
        "code": "Synthetic code",
      },
      "documentation_only": true,
      "operative_completion_authorized": false,
      "anesthesia_induction_authorized": false,
      "medication_administration_authorized": false,
      "nursing_transfer_authorized": false,
      "billing_clearance_authorized": false,
    },
    {},
  ),
  SaOperation.adminLabCatalogPrice: _Case(
    {"id": "11", "price": "12.50"},
    "/api/v1/lab/tests/11",
    "PATCH",
    {"price": "12.50"},
    {
      "success": true,
      "data": {
        "id": 11,
        "clinic_id": 7,
        "name": "Synthetic record",
        "is_active": true,
        "updated_at": "2030-01-01T00:00:00Z",
        "price": "12.50",
      },
    },
    {
      "/api/v1/lab/tests/11": {
        "success": true,
        "data": {
          "id": 11,
          "clinic_id": 7,
          "name": "Synthetic record",
          "is_active": true,
          "updated_at": "2030-01-01T00:00:00Z",
        },
      },
    },
  ),
};
const _tenant = '12345678-1234-1234-1234-123456789abc';
SaWorkspaceRemote _source(
  MockClient client,
  SessionNavigationGuard guard, {
  bool admin = true,
  bool staging = true,
  String role = 'admin',
}) => SaWorkspaceRemote(
  apiClient: ApiClient(
    config: const BackendConfig(
      baseUrl: 'http://127.0.0.1:5000',
      allowInsecureHttp: true,
    ),
    client: client,
  ),
  loadSession: () async => AuthSessionState.ready(
    AuthTokenResponse(
      accessToken: 'token',
      refreshToken: 'refresh',
      userId: 41,
      role: role,
      clinicContextId: 7,
      tenantId: _tenant,
    ),
  ),
  guard: guard,
  gate: Wd09cfOneShotGate(),
  adminMode: admin,
  staging: staging,
);
http.Response _reply(Map<String, dynamic> raw) => http.Response(
  jsonEncode(raw),
  200,
  headers: {'content-type': 'application/json'},
);

void main() {
  for (final e in _cases.entries) {
    test('expanded admin contract ${e.key.name}', () async {
      final op = e.key;
      final c = e.value;
      var writes = 0;
      var preReads = 0;
      final client = MockClient((r) async {
        expect(r.headers['authorization'], 'Bearer token');
        final path = r.url.path + (r.url.hasQuery ? '?${r.url.query}' : '');
        if (r.method == c.verb && path == c.path) {
          writes++;
          if (c.verb == 'GET' || c.verb == 'DELETE') {
            expect(r.body, isEmpty);
          } else {
            expect(jsonDecode(r.body), c.body);
          }
          return _reply(c.response);
        }
        expect(r.method, 'GET');
        expect(c.reads.containsKey(path), isTrue, reason: path);
        preReads++;
        return _reply(c.reads[path]!);
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard);
      final actor = await source.session();
      expect(source.allows(actor, op), isTrue);
      final SaResult result;
      if (op.writes) {
        final review = await source.review(actor, op, c.fields);
        expect(writes, 0);
        expect(preReads, c.reads.length);
        result = await source.submit(review);
        expect(preReads, 2 * c.reads.length);
        await expectLater(
          source.submit(review),
          throwsA(isA<Wd09cfAlreadyAttempted>()),
        );
      } else {
        result = await source.read(actor, op, c.fields);
      }
      expect(writes, 1);
      expect(result.rows, hasLength(1));
    });
  }
  test('extended actions never enter the original Super Admin mode', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      throw StateError('Unexpected HTTP');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard, admin: false, role: 'super_admin');
    final actor = await source.session();
    for (final e in _cases.entries) {
      expect(source.allows(actor, e.key), isFalse);
      await expectLater(
        e.key.writes
            ? source.review(actor, e.key, e.value.fields)
            : source.read(actor, e.key, e.value.fields),
        throwsA(isA<SaUnavailable>()),
      );
    }
    expect(calls, 0);
  });
  test(
    'six staging-held resource actions cannot send HTTP in default mode',
    () async {
      var calls = 0;
      final client = MockClient((_) async {
        calls++;
        throw StateError('Unexpected HTTP');
      });
      addTearDown(client.close);
      final guard = SessionNavigationGuard()..markSessionAvailable();
      addTearDown(guard.dispose);
      final source = _source(client, guard, staging: false);
      final actor = await source.session();
      final held = _cases.entries.where((e) => e.key.releaseHeld);
      expect(held, hasLength(6));
      for (final e in held) {
        await expectLater(
          source.review(actor, e.key, e.value.fields),
          throwsA(isA<SaUnavailable>()),
        );
      }
      expect(calls, 0);
    },
  );
  test('clinic profile cannot change a non-selected clinic', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      throw StateError('Unexpected HTTP');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final actor = await source.session();
    await expectLater(
      source.review(actor, SaOperation.adminClinicProfile, {
        'id': '99',
        'name': 'Other',
      }),
      throwsA(isA<SaUnavailable>()),
    );
    expect(calls, 0);
  });
  test('shared supplier cannot be mutated by clinic Admin', () async {
    var calls = 0;
    final client = MockClient((r) async {
      calls++;
      expect(r.method, 'GET');
      return _reply({
        'success': true,
        'data': {'id': 11, 'clinic_id': null, 'name': 'Shared'},
      });
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final actor = await source.session();
    await expectLater(
      source.review(actor, SaOperation.adminSupplierUpdate, {
        'id': '11',
        'name': 'Other',
      }),
      throwsFormatException,
    );
    expect(calls, 1);
  });
  test('changed asset maintenance pre-read prevents write', () async {
    var calls = 0;
    final client = MockClient((r) async {
      calls++;
      expect(r.method, 'GET');
      return _reply({
        'success': true,
        'maintenance': {
          'id': 11,
          'clinic_id': 7,
          'asset_id': 41,
          'status': 'scheduled',
          'updated_at': 'version-$calls',
        },
      });
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final actor = await source.session();
    final review = await source.review(
      actor,
      SaOperation.adminMaintenanceStart,
      {'id': '11'},
    );
    await expectLater(source.submit(review), throwsFormatException);
    expect(calls, 2);
  });
  test('foreign transfer rows cannot be displayed', () async {
    final client = MockClient(
      (_) async => _reply({
        'success': true,
        'data': {
          'id': 11,
          'source_clinic_id': 8,
          'destination_clinic_id': 9,
          'item_id': 21,
          'quantity': 4,
          'requested_by_id': 51,
          'status': 'pending',
        },
      }),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await expectLater(
      source.read(await source.session(), SaOperation.adminTransferDetail, {
        'id': '11',
      }),
      throwsFormatException,
    );
  });
  test('same clinic destination blocked before network', () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      throw StateError('Unexpected HTTP');
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await expectLater(
      source.review(await source.session(), SaOperation.adminTransferCreate, {
        'item_id': '21',
        'destination_clinic_id': '7',
        'quantity': '4',
      }),
      throwsA(isA<SaUnavailable>()),
    );
    expect(calls, 0);
  });
  test('destination clinic cannot approve transfer', () async {
    var calls = 0;
    final client = MockClient((r) async {
      calls++;
      expect(r.method, 'GET');
      return _reply({
        'success': true,
        'data': {
          'id': 11,
          'source_clinic_id': 8,
          'destination_clinic_id': 7,
          'item_id': 21,
          'quantity': 4,
          'requested_by_id': 51,
          'status': 'pending',
        },
      });
    });
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    await expectLater(
      source.review(await source.session(), SaOperation.adminTransferApprove, {
        'id': '11',
      }),
      throwsFormatException,
    );
    expect(calls, 1);
  });
  test('foreign inventory response rejected rather than displayed', () async {
    final client = MockClient(
      (r) async => _reply({
        'success': true,
        'data': {
          'items': [
            {'id': 11, 'clinic_id': 8},
          ],
          'total': 1,
          'page': 1,
          'per_page': 20,
        },
      }),
    );
    addTearDown(client.close);
    final guard = SessionNavigationGuard()..markSessionAvailable();
    addTearDown(guard.dispose);
    final source = _source(client, guard);
    final actor = await source.session();
    await expectLater(
      source.read(actor, SaOperation.adminLowStock, {}),
      throwsFormatException,
    );
  });
}
