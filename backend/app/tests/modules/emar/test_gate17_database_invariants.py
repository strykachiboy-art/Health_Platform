from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.enums.emar_enums import (
    MedicationAdministrationStatus,
)
from app.extensions import db as db_ext
from app.modules.emar.models.emar_model import (
    MedicationAdministration,
)
from app.tests.modules.emar.test_emar_service_invariants import (
    _base_context,
    _order,
)


def _root() -> Path:
    return Path(
        __file__
    ).resolve().parents[5]


def _migration_text(name: str) -> str:
    return (
        _root()
        / "backend"
        / "migrations"
        / "versions"
        / name
    ).read_text(
        encoding="utf-8-sig"
    )


def _base(
    *,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    return _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )


def _administration(
    *,
    clinic,
    patient,
    admission,
    prescription,
    item,
    scheduled_at,
    status=MedicationAdministrationStatus.SCHEDULED,
    actor=None,
    reason=None,
):
    kwargs = {
        "clinic_id": clinic.id,
        "patient_id": patient.id,
        "admission_id": admission.id,
        "prescription_id": prescription.id,
        "prescription_item_id": item.id,
        "scheduled_at": scheduled_at,
        "dose_value": item.dose_value,
        "dose_unit": item.dose_unit,
        "route_code": item.route_code,
        "status": status,
        "source_order_version": 1,
    }

    if status == MedicationAdministrationStatus.ADMINISTERED:
        kwargs["administered_at"] = (
            datetime.now(timezone.utc)
            if actor is not None
            else None
        )
        kwargs["administered_by_staff_id"] = (
            actor.id
            if actor is not None
            else None
        )

    if status == MedicationAdministrationStatus.HELD:
        kwargs["held_at"] = datetime.now(timezone.utc)
        kwargs["held_by_staff_id"] = actor.id
        kwargs["hold_reason"] = reason

    if status == MedicationAdministrationStatus.REFUSED:
        kwargs["refused_at"] = datetime.now(timezone.utc)
        kwargs["refused_recorded_by_staff_id"] = actor.id
        kwargs["refusal_reason"] = reason

    return MedicationAdministration(
        **kwargs
    )


def test_structured_medication_order_migration_has_positive_dose_check():
    text = _migration_text(
        "f3a1c7d9e204_structured_medication_orders.py"
    )

    assert "ck_prescription_item_dose_value_positive" in text
    assert "dose_value IS NULL OR dose_value > 0" in text


def test_emar_migration_contains_roadmap_database_constraints():
    text = _migration_text(
        "d4e7c1a9f620_emar_core.py"
    )

    for marker in (
        "ck_med_admin_dose_positive",
        "ck_med_admin_scheduled_requires_time",
        "ck_med_admin_administered_requires_actor_time",
        "ck_med_admin_held_requires_evidence",
        "ck_med_admin_refused_requires_evidence",
        "uq_med_admin_",
        "admission_item_scheduled",
        '["admission_id"]',
        '["patient_id"]',
        '["prescription_id"]',
        '["prescription_item_id"]',
    ):
        assert marker in text


def test_database_rejects_nonpositive_prescription_dose(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    actor = make_authenticated_staff(
        clinic,
        __import__(
            "app.core.enums.role_enums",
            fromlist=["Role"],
        ).Role.NURSE,
    )[0]
    drug = make_drug(
        clinic,
        is_active=True,
    )

    with pytest.raises(
        IntegrityError,
    ):
        _order(
            db=db,
            clinic=clinic,
            patient=patient,
            actor=actor,
            drug=drug,
            dose_value=0,
        )

    db.session.rollback()


def test_database_rejects_scheduled_administration_without_time(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _drug,
        admission,
        prescription,
        item,
    ) = _base(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    record = _administration(
        clinic=clinic,
        patient=patient,
        admission=admission,
        prescription=prescription,
        item=item,
        scheduled_at=None,
    )

    db.session.add(record)

    with pytest.raises(
        IntegrityError,
    ):
        db.session.flush()

    db.session.rollback()


def test_database_rejects_administered_without_actor_and_time(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        _actor,
        _drug,
        admission,
        prescription,
        item,
    ) = _base(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    record = _administration(
        clinic=clinic,
        patient=patient,
        admission=admission,
        prescription=prescription,
        item=item,
        scheduled_at=datetime.now(timezone.utc),
        status=MedicationAdministrationStatus.ADMINISTERED,
        actor=None,
    )

    db.session.add(record)

    with pytest.raises(
        IntegrityError,
    ):
        db.session.flush()

    db.session.rollback()


@pytest.mark.parametrize(
    ("status", "reason"),
    (
        (
            MedicationAdministrationStatus.HELD,
            None,
        ),
        (
            MedicationAdministrationStatus.REFUSED,
            None,
        ),
    ),
)
def test_database_rejects_terminal_status_without_required_reason(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    status,
    reason,
):
    (
        actor,
        _drug,
        admission,
        prescription,
        item,
    ) = _base(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    record = _administration(
        clinic=clinic,
        patient=patient,
        admission=admission,
        prescription=prescription,
        item=item,
        scheduled_at=datetime.now(timezone.utc),
        status=status,
        actor=actor,
        reason=reason,
    )

    db.session.add(record)

    with pytest.raises(
        IntegrityError,
    ):
        db.session.flush()

    db.session.rollback()


def test_database_rejects_duplicate_scheduled_administration_identity(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        _actor,
        _drug,
        admission,
        prescription,
        item,
    ) = _base(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    scheduled_at = datetime.now(
        timezone.utc
    )

    first = _administration(
        clinic=clinic,
        patient=patient,
        admission=admission,
        prescription=prescription,
        item=item,
        scheduled_at=scheduled_at,
    )
    second = _administration(
        clinic=clinic,
        patient=patient,
        admission=admission,
        prescription=prescription,
        item=item,
        scheduled_at=scheduled_at,
    )

    db.session.add(first)
    db.session.flush()
    db.session.add(second)

    with pytest.raises(
        IntegrityError,
    ):
        db.session.flush()

    db.session.rollback()


def test_database_rejects_invalid_cross_reference_id(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        _actor,
        _drug,
        admission,
        prescription,
        item,
    ) = _base(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    record = _administration(
        clinic=clinic,
        patient=patient,
        admission=admission,
        prescription=prescription,
        item=item,
        scheduled_at=datetime.now(timezone.utc),
    )
    record.prescription_item_id = 2_000_000_001

    db.session.add(record)

    with pytest.raises(
        IntegrityError,
    ):
        db.session.flush()

    db.session.rollback()


def _load_gate17_migration_module():
    path = (
        _root()
        / "backend"
        / "migrations"
        / "versions"
        / "c1d03e4f6a29_clinical_database_invariants.py"
    )

    spec = importlib.util.spec_from_file_location(
        "gate17_migration",
        path,
    )
    module = importlib.util.module_from_spec(
        spec
    )
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _prepare_minimal_trigger_schema(connection):
    statements = (
        """
        CREATE TABLE patients (
            id INTEGER PRIMARY KEY,
            clinic_id INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE wards (
            id INTEGER PRIMARY KEY,
            clinic_id INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE beds (
            id INTEGER PRIMARY KEY,
            ward_id INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE staff (
            id INTEGER PRIMARY KEY,
            clinic_id INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE bed_reservations (
            id INTEGER PRIMARY KEY,
            patient_id INTEGER NOT NULL,
            bed_id INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE admissions (
            id INTEGER PRIMARY KEY,
            patient_id INTEGER NOT NULL,
            bed_id INTEGER NOT NULL,
            admitted_by_id INTEGER NOT NULL,
            reservation_id INTEGER,
            status TEXT NOT NULL,
            discharged_at TEXT
        )
        """,
        """
        CREATE TABLE prescriptions (
            id INTEGER PRIMARY KEY,
            clinic_id INTEGER NOT NULL,
            patient_id INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE prescription_items (
            id INTEGER PRIMARY KEY,
            prescription_id INTEGER NOT NULL
        )
        """,
        """
        CREATE TABLE medication_administrations (
            id INTEGER PRIMARY KEY,
            clinic_id INTEGER NOT NULL,
            patient_id INTEGER NOT NULL,
            admission_id INTEGER NOT NULL,
            prescription_id INTEGER NOT NULL,
            prescription_item_id INTEGER NOT NULL
        )
        """,
    )

    for statement in statements:
        connection.exec_driver_sql(
            statement
        )


def _seed_minimal_trigger_schema(connection):
    for statement in (
        "INSERT INTO patients VALUES (1, 10)",
        "INSERT INTO patients VALUES (2, 20)",
        "INSERT INTO wards VALUES (1, 10)",
        "INSERT INTO wards VALUES (2, 20)",
        "INSERT INTO beds VALUES (1, 1)",
        "INSERT INTO beds VALUES (2, 2)",
        "INSERT INTO staff VALUES (1, 10)",
        "INSERT INTO staff VALUES (2, 20)",
        "INSERT INTO prescriptions VALUES (1, 10, 1)",
        "INSERT INTO prescriptions VALUES (2, 20, 2)",
        "INSERT INTO prescription_items VALUES (1, 1)",
        "INSERT INTO prescription_items VALUES (2, 2)",
    ):
        connection.exec_driver_sql(
            statement
        )


def test_gate17_sqlite_triggers_reject_cross_clinic_admission():
    migration = _load_gate17_migration_module()
    engine = create_engine(
        "sqlite:///:memory:"
    )

    with engine.begin() as connection:
        _prepare_minimal_trigger_schema(
            connection
        )
        _seed_minimal_trigger_schema(
            connection
        )
        migration._create_sqlite_triggers(
            connection
        )

        with pytest.raises(
            DBAPIError,
            match="admission clinical scope mismatch",
        ):
            connection.exec_driver_sql(
                """
                INSERT INTO admissions (
                    id,
                    patient_id,
                    bed_id,
                    admitted_by_id,
                    reservation_id,
                    status,
                    discharged_at
                )
                VALUES (
                    1,
                    1,
                    2,
                    1,
                    NULL,
                    'admitted',
                    NULL
                )
                """
            )


def test_gate17_sqlite_triggers_reject_med_admin_cross_reference():
    migration = _load_gate17_migration_module()
    engine = create_engine(
        "sqlite:///:memory:"
    )

    with engine.begin() as connection:
        _prepare_minimal_trigger_schema(
            connection
        )
        _seed_minimal_trigger_schema(
            connection
        )
        migration._create_sqlite_triggers(
            connection
        )

        connection.exec_driver_sql(
            """
            INSERT INTO admissions (
                id,
                patient_id,
                bed_id,
                admitted_by_id,
                reservation_id,
                status,
                discharged_at
            )
            VALUES (
                1,
                1,
                1,
                1,
                NULL,
                'admitted',
                NULL
            )
            """
        )

        with pytest.raises(
            DBAPIError,
            match="item mismatch",
        ):
            connection.exec_driver_sql(
                """
                INSERT INTO medication_administrations (
                    id,
                    clinic_id,
                    patient_id,
                    admission_id,
                    prescription_id,
                    prescription_item_id
                )
                VALUES (
                    1,
                    10,
                    1,
                    1,
                    1,
                    2
                )
                """
            )


def test_gate17_sqlite_triggers_require_active_admission_on_insert():
    migration = _load_gate17_migration_module()
    engine = create_engine(
        "sqlite:///:memory:"
    )

    with engine.begin() as connection:
        _prepare_minimal_trigger_schema(
            connection
        )
        _seed_minimal_trigger_schema(
            connection
        )
        migration._create_sqlite_triggers(
            connection
        )

        connection.exec_driver_sql(
            """
            INSERT INTO admissions (
                id,
                patient_id,
                bed_id,
                admitted_by_id,
                reservation_id,
                status,
                discharged_at
            )
            VALUES (
                1,
                1,
                1,
                1,
                NULL,
                'discharged',
                '2026-10-31T00:00:00'
            )
            """
        )

        with pytest.raises(
            DBAPIError,
            match="active clinic-consistent admission",
        ):
            connection.exec_driver_sql(
                """
                INSERT INTO medication_administrations (
                    id,
                    clinic_id,
                    patient_id,
                    admission_id,
                    prescription_id,
                    prescription_item_id
                )
                VALUES (
                    1,
                    10,
                    1,
                    1,
                    1,
                    1
                )
                """
            )
