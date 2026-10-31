"""harden clinical database invariants

Revision ID: c1d03e4f6a29
Revises: b0c92d3e5f18
"""

from __future__ import annotations

from alembic import op


revision = "c1d03e4f6a29"
down_revision = "b0c92d3e5f18"
branch_labels = None
depends_on = None


def _create_postgresql_triggers(bind) -> None:
    bind.exec_driver_sql(
        """
        CREATE OR REPLACE FUNCTION
        validate_admission_clinical_scope()
        RETURNS trigger AS $$
        DECLARE
            patient_clinic_id integer;
            bed_clinic_id integer;
            actor_clinic_id integer;
            reservation_patient_id integer;
            reservation_bed_id integer;
        BEGIN
            SELECT clinic_id
            INTO patient_clinic_id
            FROM patients
            WHERE id = NEW.patient_id;

            SELECT w.clinic_id
            INTO bed_clinic_id
            FROM beds b
            JOIN wards w
              ON w.id = b.ward_id
            WHERE b.id = NEW.bed_id;

            SELECT clinic_id
            INTO actor_clinic_id
            FROM staff
            WHERE id = NEW.admitted_by_id;

            IF patient_clinic_id IS NULL
               OR bed_clinic_id IS NULL
               OR actor_clinic_id IS NULL
               OR patient_clinic_id <> bed_clinic_id
               OR patient_clinic_id <> actor_clinic_id
            THEN
                RAISE EXCEPTION
                    'admission clinical scope mismatch';
            END IF;

            IF NEW.reservation_id IS NOT NULL THEN
                SELECT patient_id, bed_id
                INTO reservation_patient_id, reservation_bed_id
                FROM bed_reservations
                WHERE id = NEW.reservation_id;

                IF reservation_patient_id IS NULL
                   OR reservation_patient_id <> NEW.patient_id
                   OR reservation_bed_id <> NEW.bed_id
                THEN
                    RAISE EXCEPTION
                        'admission reservation mismatch';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    bind.exec_driver_sql(
        """
        CREATE TRIGGER admissions_validate_clinical_scope
        BEFORE INSERT OR UPDATE OF
            patient_id,
            bed_id,
            admitted_by_id,
            reservation_id
        ON admissions
        FOR EACH ROW
        EXECUTE FUNCTION
        validate_admission_clinical_scope()
        """
    )

    bind.exec_driver_sql(
        """
        CREATE OR REPLACE FUNCTION
        validate_med_admin_cross_references()
        RETURNS trigger AS $$
        DECLARE
            prescription_matches boolean;
            item_matches boolean;
            admission_matches boolean;
            admission_active boolean;
        BEGIN
            SELECT EXISTS (
                SELECT 1
                FROM prescriptions p
                WHERE p.id = NEW.prescription_id
                  AND p.clinic_id = NEW.clinic_id
                  AND p.patient_id = NEW.patient_id
            )
            INTO prescription_matches;

            SELECT EXISTS (
                SELECT 1
                FROM prescription_items pi
                WHERE pi.id = NEW.prescription_item_id
                  AND pi.prescription_id = NEW.prescription_id
            )
            INTO item_matches;

            SELECT EXISTS (
                SELECT 1
                FROM admissions a
                JOIN beds b
                  ON b.id = a.bed_id
                JOIN wards w
                  ON w.id = b.ward_id
                WHERE a.id = NEW.admission_id
                  AND a.patient_id = NEW.patient_id
                  AND w.clinic_id = NEW.clinic_id
            )
            INTO admission_matches;

            IF NOT prescription_matches
               OR NOT item_matches
               OR NOT admission_matches
            THEN
                RAISE EXCEPTION
                    'medication administration cross-reference mismatch';
            END IF;

            IF TG_OP = 'INSERT' THEN
                SELECT EXISTS (
                    SELECT 1
                    FROM admissions a
                    WHERE a.id = NEW.admission_id
                      AND lower(a.status::text) = 'admitted'
                      AND a.discharged_at IS NULL
                )
                INTO admission_active;

                IF NOT admission_active THEN
                    RAISE EXCEPTION
                        'medication administration requires active admission';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    bind.exec_driver_sql(
        """
        CREATE TRIGGER med_admin_validate_cross_references
        BEFORE INSERT OR UPDATE OF
            clinic_id,
            patient_id,
            admission_id,
            prescription_id,
            prescription_item_id
        ON medication_administrations
        FOR EACH ROW
        EXECUTE FUNCTION
        validate_med_admin_cross_references()
        """
    )


def _drop_postgresql_triggers(bind) -> None:
    bind.exec_driver_sql(
        """
        DROP TRIGGER IF EXISTS
        med_admin_validate_cross_references
        ON medication_administrations
        """
    )
    bind.exec_driver_sql(
        """
        DROP FUNCTION IF EXISTS
        validate_med_admin_cross_references()
        """
    )
    bind.exec_driver_sql(
        """
        DROP TRIGGER IF EXISTS
        admissions_validate_clinical_scope
        ON admissions
        """
    )
    bind.exec_driver_sql(
        """
        DROP FUNCTION IF EXISTS
        validate_admission_clinical_scope()
        """
    )


def _create_sqlite_triggers(bind) -> None:
    bind.exec_driver_sql(
        """
        CREATE TRIGGER admissions_validate_clinical_scope_insert
        BEFORE INSERT ON admissions
        BEGIN
            SELECT CASE
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM patients p
                    JOIN beds b
                      ON b.id = NEW.bed_id
                    JOIN wards w
                      ON w.id = b.ward_id
                    JOIN staff s
                      ON s.id = NEW.admitted_by_id
                    WHERE p.id = NEW.patient_id
                      AND p.clinic_id = w.clinic_id
                      AND s.clinic_id = p.clinic_id
                      AND (
                          NEW.reservation_id IS NULL
                          OR EXISTS (
                              SELECT 1
                              FROM bed_reservations r
                              WHERE r.id = NEW.reservation_id
                                AND r.patient_id = NEW.patient_id
                                AND r.bed_id = NEW.bed_id
                          )
                      )
                )
                THEN RAISE(
                    ABORT,
                    'admission clinical scope mismatch'
                )
            END;
        END
        """
    )

    bind.exec_driver_sql(
        """
        CREATE TRIGGER admissions_validate_clinical_scope_update
        BEFORE UPDATE OF
            patient_id,
            bed_id,
            admitted_by_id,
            reservation_id
        ON admissions
        BEGIN
            SELECT CASE
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM patients p
                    JOIN beds b
                      ON b.id = NEW.bed_id
                    JOIN wards w
                      ON w.id = b.ward_id
                    JOIN staff s
                      ON s.id = NEW.admitted_by_id
                    WHERE p.id = NEW.patient_id
                      AND p.clinic_id = w.clinic_id
                      AND s.clinic_id = p.clinic_id
                      AND (
                          NEW.reservation_id IS NULL
                          OR EXISTS (
                              SELECT 1
                              FROM bed_reservations r
                              WHERE r.id = NEW.reservation_id
                                AND r.patient_id = NEW.patient_id
                                AND r.bed_id = NEW.bed_id
                          )
                      )
                )
                THEN RAISE(
                    ABORT,
                    'admission clinical scope mismatch'
                )
            END;
        END
        """
    )

    bind.exec_driver_sql(
        """
        CREATE TRIGGER med_admin_validate_cross_references_insert
        BEFORE INSERT ON medication_administrations
        BEGIN
            SELECT CASE
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM prescriptions p
                    WHERE p.id = NEW.prescription_id
                      AND p.clinic_id = NEW.clinic_id
                      AND p.patient_id = NEW.patient_id
                )
                THEN RAISE(
                    ABORT,
                    'medication administration prescription mismatch'
                )
            END;

            SELECT CASE
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM prescription_items pi
                    WHERE pi.id = NEW.prescription_item_id
                      AND pi.prescription_id = NEW.prescription_id
                )
                THEN RAISE(
                    ABORT,
                    'medication administration item mismatch'
                )
            END;

            SELECT CASE
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM admissions a
                    JOIN beds b
                      ON b.id = a.bed_id
                    JOIN wards w
                      ON w.id = b.ward_id
                    WHERE a.id = NEW.admission_id
                      AND a.patient_id = NEW.patient_id
                      AND w.clinic_id = NEW.clinic_id
                      AND lower(a.status) = 'admitted'
                      AND a.discharged_at IS NULL
                )
                THEN RAISE(
                    ABORT,
                    'medication administration requires active clinic-consistent admission'
                )
            END;
        END
        """
    )

    bind.exec_driver_sql(
        """
        CREATE TRIGGER med_admin_validate_cross_references_update
        BEFORE UPDATE OF
            clinic_id,
            patient_id,
            admission_id,
            prescription_id,
            prescription_item_id
        ON medication_administrations
        BEGIN
            SELECT CASE
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM prescriptions p
                    WHERE p.id = NEW.prescription_id
                      AND p.clinic_id = NEW.clinic_id
                      AND p.patient_id = NEW.patient_id
                )
                THEN RAISE(
                    ABORT,
                    'medication administration prescription mismatch'
                )
            END;

            SELECT CASE
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM prescription_items pi
                    WHERE pi.id = NEW.prescription_item_id
                      AND pi.prescription_id = NEW.prescription_id
                )
                THEN RAISE(
                    ABORT,
                    'medication administration item mismatch'
                )
            END;

            SELECT CASE
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM admissions a
                    JOIN beds b
                      ON b.id = a.bed_id
                    JOIN wards w
                      ON w.id = b.ward_id
                    WHERE a.id = NEW.admission_id
                      AND a.patient_id = NEW.patient_id
                      AND w.clinic_id = NEW.clinic_id
                )
                THEN RAISE(
                    ABORT,
                    'medication administration admission mismatch'
                )
            END;
        END
        """
    )


def _drop_sqlite_triggers(bind) -> None:
    for name in (
        "med_admin_validate_cross_references_update",
        "med_admin_validate_cross_references_insert",
        "admissions_validate_clinical_scope_update",
        "admissions_validate_clinical_scope_insert",
    ):
        bind.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {name}"
        )


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        _create_postgresql_triggers(bind)
    elif dialect == "sqlite":
        _create_sqlite_triggers(bind)


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        _drop_postgresql_triggers(bind)
    elif dialect == "sqlite":
        _drop_sqlite_triggers(bind)
