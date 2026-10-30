from sqlalchemy import CheckConstraint, Index, UniqueConstraint

from app.core.enums.emar_enums import (
    BarcodeVerificationFailureCode,
    BarcodeVerificationResult,
)
from app.modules.emar.models.emar_model import (
    MedicationAdministration,
    MedicationAdministrationEvent,
)
from app.modules.patient.models.patient_model import Patient


def _constraint_names(table, kind):
    return {
        item.name
        for item in table.constraints
        if isinstance(item, kind) and item.name
    }


def _index_names(table):
    return {item.name for item in table.indexes if isinstance(item, Index) and item.name}


def test_barcode_verification_result_contract_is_complete():
    assert {item.value for item in BarcodeVerificationResult} == {
        "barcode_verified",
        "not_barcode_verified",
    }


def test_barcode_failure_codes_cover_required_fail_closed_cases():
    values = {item.value for item in BarcodeVerificationFailureCode}
    assert {
        "unknown_patient_barcode",
        "unknown_medication_barcode",
        "malformed_patient_barcode",
        "malformed_medication_barcode",
        "cross_clinic_patient_barcode",
        "wrong_patient",
        "wrong_medication",
        "dose_mismatch",
        "route_mismatch",
        "outside_time_window",
        "inactive_admission",
        "inactive_order",
        "order_cancelled",
        "order_expired",
        "unauthorized_actor",
        "stale_verification",
    } <= values


def test_patient_has_clinic_scoped_barcode_identity():
    table = Patient.__table__
    assert "barcode" in table.c
    assert table.c.barcode.nullable is True
    assert "uq_patients_clinic_barcode" in _constraint_names(table, UniqueConstraint)
    assert "ix_patients_clinic_barcode" in _index_names(table)


def test_emar_contains_all_planned_barcode_snapshot_fields():
    table = MedicationAdministration.__table__
    for column in (
        "patient_barcode",
        "medication_barcode",
        "scan_timestamp",
        "scan_device",
        "verification_result",
        "barcode_verified_by_staff_id",
        "barcode_verification_event_id",
    ):
        assert column in table.c
    checks = _constraint_names(table, CheckConstraint)
    assert {
        "ck_med_admin_barcode_verification_result",
        "ck_med_admin_barcode_verification_evidence",
    } <= checks
    assert "ix_med_admin_barcode_verification" in _index_names(table)


def test_medication_administration_event_is_required_append_only_domain():
    table = MedicationAdministrationEvent.__table__
    assert table.name == "medication_administration_events"
    assert "updated_at" not in table.c
    for column in (
        "patient_barcode",
        "medication_barcode",
        "scan_timestamp",
        "scan_device",
        "verification_result",
        "failure_code",
        "failure_reason",
        "patient_match",
        "medication_match",
        "dose_match",
        "route_match",
        "time_window_match",
        "operation_identity",
    ):
        assert column in table.c


def test_event_five_right_checks_are_nonnullable():
    table = MedicationAdministrationEvent.__table__
    for column in (
        "patient_match",
        "medication_match",
        "dose_match",
        "route_match",
        "time_window_match",
    ):
        assert table.c[column].nullable is False


def test_event_required_scan_fields_are_nonnullable():
    table = MedicationAdministrationEvent.__table__
    for column in (
        "patient_barcode",
        "medication_barcode",
        "scan_timestamp",
        "scan_device",
        "verification_result",
        "actor_staff_id",
        "operation_identity",
    ):
        assert table.c[column].nullable is False


def test_event_has_database_consistency_guards():
    table = MedicationAdministrationEvent.__table__
    checks = _constraint_names(table, CheckConstraint)
    assert {
        "ck_med_admin_event_expected_dose_positive",
        "ck_med_admin_event_source_order_version_positive",
        "ck_med_admin_event_policy_version_positive",
        "ck_med_admin_event_verification_result",
        "ck_med_admin_event_verified_consistency",
        "ck_med_admin_event_failed_consistency",
    } <= checks
    assert "uq_med_admin_event_clinic_operation" in _constraint_names(table, UniqueConstraint)


def test_event_indexes_support_history_and_result_queries():
    indexes = _index_names(MedicationAdministrationEvent.__table__)
    assert {
        "ix_med_admin_event_admin_scan",
        "ix_med_admin_event_clinic_patient_scan",
        "ix_med_admin_event_clinic_result_scan",
    } <= indexes


def test_emar_has_barcode_event_relationship():
    # Relationship inspection configures the global SQLAlchemy
    # mapper registry. Load all application models first so
    # unrelated string relationships such as Notification->Clinic
    # are resolvable during mapper configuration.
    from app import models_registry  # noqa: F401

    assert "barcode_events" in MedicationAdministration.__mapper__.relationships
    assert "medication_administration" in MedicationAdministrationEvent.__mapper__.relationships
    assert "bound_barcode_verification" in MedicationAdministration.__mapper__.relationships

def test_barcode_verification_binding_fk_breaks_metadata_drop_cycle():
    column = (
        MedicationAdministration
        .__table__
        .c
        .barcode_verification_event_id
    )

    foreign_keys = list(
        column.foreign_keys
    )

    assert len(
        foreign_keys
    ) == 1

    foreign_key = foreign_keys[0]

    assert (
        foreign_key.name
        == "fk_med_admin_barcode_verification_event"
    )

    assert (
        foreign_key.use_alter
        is True
    )
