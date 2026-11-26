// Fixed, reviewed source contracts. No caller-provided HTTP method or route.
class ModuleReadSpec {
  const ModuleReadSpec(
    this.key,
    this.label,
    this.domain,
    this.roles,
    this.route,
    this.shape,
    this.selector,
    this.identity,
    this.relation,
    this.clinicField,
    this.managed,
    this.fields,
  );
  final String key,
      label,
      domain,
      route,
      shape,
      selector,
      identity,
      relation,
      clinicField;
  final Set<String> roles;
  final bool managed;
  final Map<String, String> fields;
  bool get requiresId => selector.isNotEmpty;
  String path(int id) {
    if (requiresId ? (id <= 0 || id > 2147483647) : id != 0) {
      throw const FormatException('Invalid resource selection.');
    }
    return route.replaceAll('{id}', '$id');
  }
}

class ModuleDraftField {
  const ModuleDraftField(
    this.key,
    this.label,
    this.limit,
    this.kind,
    this.required,
  );
  final String key, label, kind;
  final int limit;
  final bool required;
}

class ModuleDraftSpec {
  const ModuleDraftSpec(
    this.key,
    this.label,
    this.domain,
    this.roles,
    this.anchor,
    this.fields,
  );
  final String key, label, domain, anchor;
  final Set<String> roles;
  final List<ModuleDraftField> fields;
}

class Run2ModuleCatalog {
  const Run2ModuleCatalog._();
  static const snapshotType = 'local.module.snapshot';
  static const draftType = 'local.module.draft';
  static const localTypes = {snapshotType, draftType};
  static const retention = Duration(hours: 24);
  // Never load real clinical/financial content by default. A deployment owner
  // must separately approve the managed-device retention/access policy.
  static const managedPolicyEnabled = bool.fromEnvironment(
    'CSP_RUN2_MANAGED_LOCAL_RECORDS',
  );
  static const reads = <String, ModuleReadSpec>{
    "patient": ModuleReadSpec(
      "patient",
      "Patient identity snapshot",
      "patients",
      {
        "admin",
        "doctor",
        "nurse",
        "receptionist",
        "pharmacist",
        "lab_technician",
        "paramedic",
        "emt",
      },
      "api/v1/patients/{id}",
      "envelope",
      "Patient ID",
      "id",
      "",
      "clinic_id",
      true,
      {
        "id": "i",
        "patient_number": "s",
        "first_name": "s",
        "last_name": "s",
        "date_of_birth": "s?",
        "gender": "s?",
        "is_active": "b",
      },
    ),
    "appointments": ModuleReadSpec(
      "appointments",
      "Staff appointment schedule",
      "appointments",
      {"admin", "doctor", "nurse", "receptionist"},
      "api/v1/appointments/staff/{id}?page=1&per_page=20",
      "page",
      "Staff ID",
      "id",
      "staff_id",
      "clinic_id",
      true,
      {
        "id": "i",
        "patient_id": "i",
        "staff_id": "i",
        "scheduled_start": "s",
        "scheduled_end": "s",
        "status": "s",
        "appointment_type": "s",
      },
    ),
    "consultation": ModuleReadSpec(
      "consultation",
      "Consultation document history",
      "consultations",
      {"doctor", "nurse", "admin"},
      "api/v1/consultations/{id}",
      "envelope",
      "Consultation ID",
      "id",
      "",
      "clinic_id",
      true,
      {
        "id": "i",
        "patient_id": "i",
        "record_version": "i",
        "record_status": "s",
        "status": "s",
        "chief_complaint": "s?",
        "symptoms": "s?",
        "diagnosis": "s?",
        "treatment_plan": "s?",
        "notes": "s?",
      },
    ),
    "admission": ModuleReadSpec(
      "admission",
      "Last observed admission",
      "nursing",
      {"admin", "doctor", "nurse", "receptionist"},
      "api/v1/wards/patients/{id}/current-admission",
      "raw",
      "Patient ID",
      "id",
      "patient_id",
      "",
      true,
      {
        "id": "i",
        "patient_id": "i",
        "bed_id": "i",
        "status": "s",
        "admitted_at": "s?",
      },
    ),
    "nursing_notes": ModuleReadSpec(
      "nursing_notes",
      "Nursing note history",
      "nursing",
      {"admin", "doctor", "nurse"},
      "api/v1/nursing/admissions/{id}/notes",
      "rawPage",
      "Admission ID",
      "id",
      "admission_id",
      "clinic_id",
      true,
      {
        "id": "i",
        "patient_id": "i",
        "admission_id": "i",
        "note_text": "s",
        "note_type": "s",
      },
    ),
    "lab_catalog": ModuleReadSpec(
      "lab_catalog",
      "Lab test catalogue",
      "lab",
      {"admin", "doctor", "nurse", "receptionist", "lab_technician"},
      "api/v1/lab/tests?page=1&per_page=20",
      "page",
      "",
      "id",
      "",
      "clinic_id",
      false,
      {
        "id": "i",
        "name": "s",
        "code": "s",
        "sample_type": "s?",
        "is_active": "b",
      },
    ),
    "lab_order": ModuleReadSpec(
      "lab_order",
      "Laboratory order history",
      "lab",
      {"admin", "doctor", "nurse", "lab_technician"},
      "api/v1/lab/orders/{id}",
      "envelope",
      "Lab order ID",
      "id",
      "",
      "clinic_id",
      true,
      {
        "id": "i",
        "patient_id": "i",
        "status": "s",
        "sample_collected_at": "s?",
        "verified_at": "s?",
        "completed_at": "s?",
      },
    ),
    "drug": ModuleReadSpec(
      "drug",
      "Drug catalogue entry",
      "pharmacy",
      {"admin", "pharmacist"},
      "api/v1/pharmacy/drugs/{id}",
      "envelope",
      "Drug ID",
      "id",
      "",
      "clinic_id",
      false,
      {
        "id": "i",
        "name": "s",
        "generic_name": "s?",
        "dosage_form": "s?",
        "strength": "s?",
        "is_active": "b",
      },
    ),
    "prescription": ModuleReadSpec(
      "prescription",
      "Prescription history (metadata)",
      "patients",
      {"doctor", "pharmacist"},
      "api/v1/prescriptions/{id}",
      "envelope",
      "Prescription ID",
      "id",
      "",
      "clinic_id",
      true,
      {
        "id": "i",
        "patient_id": "i",
        "status": "s",
        "issued_at": "s?",
        "expires_at": "s?",
      },
    ),
    "billing": ModuleReadSpec(
      "billing",
      "Outstanding invoice history",
      "billing",
      {"admin"},
      "api/v1/billing/invoices/outstanding?page=1&per_page=20",
      "page",
      "",
      "id",
      "",
      "clinic_id",
      true,
      {
        "id": "i",
        "patient_id": "i",
        "invoice_number": "s",
        "currency": "s?",
        "total_amount": "d",
        "amount_paid": "d",
        "status": "s",
        "due_date": "s?",
      },
    ),
    "ambulance": ModuleReadSpec(
      "ambulance",
      "Ambulance trip history",
      "ambulance",
      {
        "admin",
        "ambulance_coordinator",
        "ambulance_dispatcher",
        "driver",
        "paramedic",
        "emt",
      },
      "api/v1/ambulance/trips/{id}",
      "envelope",
      "Trip ID",
      "id",
      "",
      "clinic_id",
      true,
      {
        "id": "i",
        "trip_type": "s",
        "status": "s",
        "pickup_address": "s?",
        "destination_address": "s?",
      },
    ),
    "blood_stock": ModuleReadSpec(
      "blood_stock",
      "Blood stock historical totals",
      "blood_bank",
      {"admin", "lab_technician", "doctor", "nurse"},
      "api/v1/blood-bank/stock-summary",
      "aggregate",
      "",
      "",
      "",
      "",
      true,
      {"total_available": "n", "total_quarantined": "n"},
    ),
    "nursing_order": ModuleReadSpec(
      "nursing_order",
      "Nursing order history",
      "clinical_orders",
      {"admin", "doctor", "nurse"},
      "api/v1/clinical-orders/nursing-orders/{id}",
      "raw",
      "Nursing order ID",
      "id",
      "",
      "clinic_id",
      true,
      {
        "id": "i",
        "patient_id": "i",
        "admission_id": "i",
        "category": "s",
        "status": "s",
        "version": "i",
        "order_text": "s",
        "frequency": "s",
      },
    ),
    "pharmacy_stock": ModuleReadSpec(
      "pharmacy_stock",
      "Pharmacy historical stock totals",
      "pharmacy",
      {"admin", "pharmacist"},
      "api/v1/pharmacy/drugs/{id}/stock-summary",
      "envelope",
      "Drug ID",
      "drug_id",
      "",
      "clinic_id",
      true,
      {
        "drug_id": "i",
        "drug_name": "s",
        "quantity_on_hand": "n",
        "batch_count": "n",
      },
    ),
    "medrec": ModuleReadSpec(
      "medrec",
      "Medication reconciliation history",
      "medication_reconciliation",
      {"doctor", "nurse", "pharmacist"},
      "api/v1/medication-reconciliations/{id}",
      "reconciliation",
      "Reconciliation ID",
      "id",
      "",
      "clinic_id",
      true,
      {
        "id": "i",
        "patient_id": "i",
        "admission_id": "i",
        "event_type": "s",
        "record_status": "s",
        "record_version": "i",
        "notes": "s?",
      },
    ),
    "notifications": ModuleReadSpec(
      "notifications",
      "Previously received notification inbox",
      "notifications",
      {
        "admin",
        "doctor",
        "nurse",
        "patient",
        "pharmacist",
        "lab_technician",
        "receptionist",
        "accountant",
        "paramedic",
        "emt",
        "driver",
        "ambulance_dispatcher",
        "ambulance_coordinator",
        "other",
      },
      "api/v1/notifications/?page=1&per_page=20",
      "page",
      "",
      "id",
      "",
      "clinic_id",
      true,
      {
        "id": "i",
        "user_id": "i",
        "title": "s",
        "message": "s",
        "notification_type": "s",
        "priority": "s",
        "status": "s",
        "is_read": "b",
        "created_at": "s?",
      },
    ),
    "report": ModuleReadSpec(
      "report",
      "Generated report metadata",
      "administration",
      {
        "admin",
        "doctor",
        "nurse",
        "receptionist",
        "accountant",
        "pharmacist",
        "lab_technician",
      },
      "api/v1/reports/{id}",
      "envelope",
      "Report ID",
      "id",
      "",
      "clinic_id",
      true,
      {"id": "i", "report_type": "s", "report_format": "s", "created_at": "s"},
    ),
  };
  // These GETs carry no-store. Use only for transient live target validation.
  static const anchorOnly = <String, ModuleReadSpec>{
    "radiology_exam": ModuleReadSpec(
      "radiology_exam",
      "Radiology examination",
      "radiology",
      {"doctor"},
      "api/v1/radiology/exams/{id}",
      "envelope",
      "Exam ID",
      "exam_id",
      "",
      "clinic_id",
      true,
      {"exam_id": "i", "imaging_order_id": "i", "version": "i", "status": "s"},
    ),
    "surgery_booking": ModuleReadSpec(
      "surgery_booking",
      "Surgical case booking",
      "surgery",
      {"doctor"},
      "api/v1/surgery/bookings/{id}",
      "envelope",
      "Booking ID",
      "id",
      "",
      "clinic_id",
      true,
      {"id": "i", "surgical_case_id": "i", "version": "i", "status": "s"},
    ),
  };
  static const drafts = <String, ModuleDraftSpec>{
    "patient_registration": ModuleDraftSpec(
      "patient_registration",
      "Patient registration draft",
      "patients",
      {"admin", "receptionist"},
      "",
      [
        ModuleDraftField("first_name", "First name", 80, "text", true),
        ModuleDraftField("last_name", "Last name", 80, "text", true),
        ModuleDraftField("date_of_birth", "Date of birth", 10, "date", false),
        ModuleDraftField("phone", "Phone", 30, "text", false),
        ModuleDraftField("address", "Address", 255, "text", false),
      ],
    ),
    "consultation_note": ModuleDraftSpec(
      "consultation_note",
      "Consultation working document",
      "consultations",
      {"doctor", "nurse", "admin"},
      "consultation",
      [
        ModuleDraftField(
          "chief_complaint",
          "Chief complaint",
          2000,
          "text",
          false,
        ),
        ModuleDraftField("symptoms", "Symptoms", 5000, "text", false),
        ModuleDraftField("diagnosis", "Diagnosis", 5000, "text", false),
        ModuleDraftField(
          "treatment_plan",
          "Treatment plan",
          5000,
          "text",
          false,
        ),
        ModuleDraftField("notes", "Notes", 10000, "text", false),
      ],
    ),
    "nursing_note": ModuleDraftSpec(
      "nursing_note",
      "Nursing progress note draft",
      "nursing",
      {"nurse"},
      "admission",
      [
        ModuleDraftField("note_text", "Note text", 12000, "text", true),
        ModuleDraftField("observed_at", "Observed at", 40, "datetime", false),
      ],
    ),
    "lab_result": ModuleDraftSpec(
      "lab_result",
      "Laboratory result-entry draft",
      "lab",
      {"lab_technician"},
      "lab_order",
      [
        ModuleDraftField(
          "order_item_id",
          "Order item ID (verified against the lab order)",
          10,
          "id",
          true,
        ),
        ModuleDraftField("result_value", "Result value", 150, "text", true),
        ModuleDraftField("result_notes", "Result notes", 1000, "text", false),
      ],
    ),
    "prescription_draft": ModuleDraftSpec(
      "prescription_draft",
      "Prescription medication working draft",
      "patients",
      {"doctor"},
      "patient",
      [
        ModuleDraftField("drug_id", "Drug id", 10, "id", true),
        ModuleDraftField("dosage", "Dosage", 255, "text", true),
        ModuleDraftField("frequency", "Frequency", 255, "text", true),
        ModuleDraftField("duration", "Duration", 255, "text", true),
        ModuleDraftField("quantity", "Quantity", 10, "id", true),
        ModuleDraftField("route_code", "Route code", 64, "text", true),
        ModuleDraftField("route_display", "Route display", 128, "text", true),
        ModuleDraftField("instructions", "Instructions", 1000, "text", false),
        ModuleDraftField("notes", "Notes", 2000, "text", false),
      ],
    ),
    "invoice_draft": ModuleDraftSpec(
      "invoice_draft",
      "Invoice line working draft",
      "billing",
      {"admin"},
      "patient",
      [
        ModuleDraftField("currency", "Currency", 3, "currency", true),
        ModuleDraftField("description", "Description", 255, "text", true),
        ModuleDraftField("quantity", "Quantity", 10, "id", true),
        ModuleDraftField("unit_price", "Unit price", 18, "decimal", true),
        ModuleDraftField("due_date", "Due date", 10, "date", false),
      ],
    ),
    "radiology_report": ModuleDraftSpec(
      "radiology_report",
      "Radiology working report (unsigned)",
      "radiology",
      {"doctor"},
      "radiology_exam",
      [ModuleDraftField("content", "Content", 12000, "text", true)],
    ),
    "surgery_report": ModuleDraftSpec(
      "surgery_report",
      "Operative narrative working draft (unsigned)",
      "surgery",
      {"doctor"},
      "surgery_booking",
      [
        ModuleDraftField(
          "preoperative_diagnosis",
          "Preoperative diagnosis",
          1200,
          "text",
          false,
        ),
        ModuleDraftField(
          "postoperative_diagnosis",
          "Postoperative diagnosis",
          1200,
          "text",
          false,
        ),
        ModuleDraftField(
          "procedure_narrative",
          "Procedure narrative",
          6000,
          "text",
          false,
        ),
        ModuleDraftField(
          "operative_findings",
          "Operative findings",
          4000,
          "text",
          false,
        ),
        ModuleDraftField(
          "operative_course",
          "Operative course",
          6000,
          "text",
          false,
        ),
        ModuleDraftField(
          "complication_notes",
          "Complication notes",
          2000,
          "text",
          false,
        ),
        ModuleDraftField(
          "postoperative_plan",
          "Postoperative plan",
          3000,
          "text",
          false,
        ),
      ],
    ),
    "ambulance_request": ModuleDraftSpec(
      "ambulance_request",
      "Ambulance request draft (not dispatch)",
      "ambulance",
      {"admin", "ambulance_coordinator", "ambulance_dispatcher"},
      "",
      [
        ModuleDraftField("trip_type", "Trip type", 40, "text", true),
        ModuleDraftField("pickup_address", "Pickup address", 255, "text", true),
        ModuleDraftField(
          "destination_address",
          "Destination address",
          255,
          "text",
          true,
        ),
        ModuleDraftField("notes", "Notes", 2000, "text", false),
      ],
    ),
    "donor_registration": ModuleDraftSpec(
      "donor_registration",
      "Blood donor intake draft (no consent or clearance)",
      "blood_bank",
      {"admin", "nurse", "lab_technician"},
      "",
      [
        ModuleDraftField("donor_code", "Donor code", 64, "text", true),
        ModuleDraftField("full_name", "Full name", 200, "text", true),
        ModuleDraftField("phone", "Phone", 40, "text", true),
        ModuleDraftField("notes", "Notes", 5000, "text", false),
      ],
    ),
    "medrec_draft": ModuleDraftSpec(
      "medrec_draft",
      "Medication reconciliation working notes",
      "medication_reconciliation",
      {"doctor", "pharmacist"},
      "medrec",
      [
        ModuleDraftField(
          "medication_name",
          "Medication name",
          255,
          "text",
          false,
        ),
        ModuleDraftField("dose_text", "Dose text", 255, "text", false),
        ModuleDraftField("route_text", "Route text", 128, "text", false),
        ModuleDraftField(
          "frequency_text",
          "Frequency text",
          255,
          "text",
          false,
        ),
        ModuleDraftField("instructions", "Instructions", 1000, "text", false),
        ModuleDraftField("rationale", "Rationale", 1000, "text", false),
        ModuleDraftField("notes", "Notes", 10000, "text", false),
      ],
    ),
    "nursing_order_draft": ModuleDraftSpec(
      "nursing_order_draft",
      "Nursing order working draft (not an order)",
      "clinical_orders",
      {"doctor"},
      "admission",
      [
        ModuleDraftField("category", "Category", 32, "text", true),
        ModuleDraftField("order_text", "Order text", 10000, "text", true),
        ModuleDraftField("frequency", "Frequency", 24, "text", true),
        ModuleDraftField("instructions", "Instructions", 10000, "text", false),
      ],
    ),
  };
  static ModuleReadSpec? anchor(String key) => reads[key] ?? anchorOnly[key];
  static List<ModuleReadSpec> readsFor(String role, bool managed) => reads
      .values
      .where((s) => s.roles.contains(role) && (managed || !s.managed))
      .toList(growable: false);
  static List<ModuleDraftSpec> draftsFor(String role, bool managed) => managed
      ? drafts.values
            .where((s) => s.roles.contains(role))
            .toList(growable: false)
      : const [];

  static Map<String, String> validateDraft(
    ModuleDraftSpec spec,
    Map<String, String> values,
  ) {
    if (values.keys.any((k) => !spec.fields.any((f) => f.key == k))) {
      throw const FormatException('Unexpected draft field.');
    }
    final result = <String, String>{};
    for (final field in spec.fields) {
      final value = (values[field.key] ?? '').trim();
      if (value.isEmpty) {
        if (field.required) {
          throw const FormatException('Required draft field missing.');
        }
        continue;
      }
      if (value.length > field.limit || value.contains('\u0000')) {
        throw const FormatException('Invalid draft field length.');
      }
      final valid = switch (field.kind) {
        'id' =>
          RegExp(r'^[1-9][0-9]{0,9}$').hasMatch(value) &&
              (int.tryParse(value) ?? 0) <= 2147483647,
        'date' =>
          RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(value) &&
              DateTime.tryParse(value)?.toIso8601String().substring(0, 10) ==
                  value,
        'datetime' =>
          RegExp(r'(?:Z|[+-]\d{2}:\d{2})$').hasMatch(value) &&
              DateTime.tryParse(value) != null,
        'currency' => RegExp(r'^[A-Z]{3}$').hasMatch(value),
        'decimal' => RegExp(r'^\d{1,12}(?:\.\d{1,4})?$').hasMatch(value),
        'text' => true,
        _ => false,
      };
      if (!valid) {
        throw const FormatException('Invalid draft field format.');
      }
      result[field.key] = value;
    }
    if (result.isEmpty) {
      throw const FormatException('Empty draft.');
    }
    return Map.unmodifiable(result);
  }
}
