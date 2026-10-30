from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app import create_app
from app.config import config_by_name
from app.core.auth.user.models.user_model import User
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.emar_enums import (
    MedicationAdministrationStatus,
)
from app.core.enums.prescription_enums import (
    MedicationOrderPriority,
    MedicationOrderType,
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.extensions import db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.emar.models.emar_model import MedicationAdministration
from app.modules.emar.services import emar_service
from app.modules.patient.models.patient_model import Patient
from app.modules.pharmacy.models.pharmacy_model import Drug
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import (
    Admission,
    Bed,
    Ward,
)


def _database_url() -> str:
    value = os.getenv(
        "CONCURRENCY_TEST_DATABASE_URL"
    )

    if not value:
        pytest.fail(
            "CONCURRENCY_TEST_DATABASE_URL is required"
        )

    return value


def _build_app():
    from app import extensions

    database_url = _database_url()

    original_database_url = (
        config_by_name[
            "testing"
        ].SQLALCHEMY_DATABASE_URI
    )

    original_redis_client = (
        extensions.redis_client
    )

    config_by_name[
        "testing"
    ].SQLALCHEMY_DATABASE_URI = database_url

    app = create_app(
        "testing"
    )

    extensions.redis_client = (
        original_redis_client
    )

    return (
        app,
        extensions,
        original_database_url,
        original_redis_client,
    )


def _seed(
    app,
    *,
    suffix: str,
) -> dict:
    with app.app_context():
        from app import models_registry  # noqa: F401

        db.drop_all()
        db.create_all()

        clinic = Clinic(
            name=(
                "Gate 6 Unknown Outcome Clinic "
                f"{suffix}"
            ),
            status=ClinicStatus.ACTIVE,
            ai_credits=5,
        )

        db.session.add(
            clinic
        )
        db.session.flush()

        user = User(
            clinic_id=clinic.id,
            role=Role.NURSE,
            is_active=True,
            email=(
                "gate6-unknown-"
                f"{suffix}@test.com"
            ),
        )

        user.set_password(
            "supersecret"
        )

        db.session.add(
            user
        )
        db.session.flush()

        staff = Staff(
            clinic_id=clinic.id,
            user_id=user.id,
            first_name="Retry",
            last_name="Nurse",
        )

        patient = Patient(
            clinic_id=clinic.id,
            first_name="Unknown",
            last_name="Outcome",
            patient_number=(
                "G6-UNKNOWN-"
                f"{suffix}"
            ),
            is_active=True,
        )

        db.session.add_all(
            [
                staff,
                patient,
            ]
        )
        db.session.flush()

        ward = Ward(
            clinic_id=clinic.id,
            name=(
                "Gate 6 Unknown Ward "
                f"{suffix}"
            ),
            capacity=1,
        )

        db.session.add(
            ward
        )
        db.session.flush()

        bed = Bed(
            ward_id=ward.id,
            bed_number="1",
        )

        db.session.add(
            bed
        )
        db.session.flush()

        admission = Admission(
            patient_id=patient.id,
            bed_id=bed.id,
            admitted_by_id=staff.id,
            status=AdmissionStatus.ADMITTED,
        )

        db.session.add(
            admission
        )
        db.session.flush()

        drug = Drug(
            clinic_id=clinic.id,
            name=(
                "Gate 6 Unknown Drug "
                f"{suffix}"
            ),
            is_active=True,
        )

        db.session.add(
            drug
        )
        db.session.flush()

        prescription = Prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            status=PrescriptionStatus.ACTIVE,
        )

        db.session.add(
            prescription
        )
        db.session.flush()

        item = PrescriptionItem(
            prescription_id=prescription.id,
            drug_id=drug.id,
            dose_value=500,
            dose_unit="mg",
            route_code="oral",
            is_prn=False,
            order_priority=MedicationOrderPriority.ROUTINE,
            order_type=MedicationOrderType.INPATIENT,
            ordered_at=datetime.now(
                timezone.utc
            ),
        )

        db.session.add(
            item
        )
        db.session.flush()

        administration = MedicationAdministration(
            clinic_id=clinic.id,
            patient_id=patient.id,
            admission_id=admission.id,
            prescription_id=prescription.id,
            prescription_item_id=item.id,
            scheduled_at=datetime.now(
                timezone.utc
            ),
            dose_value=item.dose_value,
            dose_unit=item.dose_unit,
            route_code=item.route_code,
            status=MedicationAdministrationStatus.SCHEDULED,
            source_order_version=1,
        )

        db.session.add(
            administration
        )
        db.session.commit()

        return {
            "clinic_id": clinic.id,
            "user_id": user.id,
            "staff_id": staff.id,
            "administration_id": administration.id,
        }


def _cleanup(
    app,
    extensions,
    original_database_url,
    original_redis_client,
) -> None:
    with app.app_context():
        db.session.rollback()
        db.drop_all()
        db.session.remove()

        try:
            db.engine.dispose()
        except Exception:
            pass

    config_by_name[
        "testing"
    ].SQLALCHEMY_DATABASE_URI = (
        original_database_url
    )

    extensions.redis_client = (
        original_redis_client
    )


def _ledger_rows(
    *,
    clinic_id: int,
    user_id: int,
    key: str,
):
    return (
        db.session.execute(
            select(
                IdempotencyRecord
            ).where(
                IdempotencyRecord.clinic_id
                == clinic_id,
                IdempotencyRecord.user_id
                == user_id,
                IdempotencyRecord.operation
                == "emar.administer",
                IdempotencyRecord.idempotency_key
                == key,
            )
        )
        .scalars()
        .all()
    )


def test_committed_unknown_outcome_retry_reconciles_same_entity(
    monkeypatch,
):
    (
        app,
        extensions,
        original_database_url,
        original_redis_client,
    ) = _build_app()

    try:
        ids = _seed(
            app,
            suffix="committed",
        )

        monkeypatch.setattr(
            emar_service,
            "create_audit_log",
            lambda *args, **kwargs: None,
        )

        key = "gate6-unknown-committed"

        with app.app_context():
            first = (
                emar_service
                .administer_medication(
                    administration_id=ids[
                        "administration_id"
                    ],
                    clinic_id=ids[
                        "clinic_id"
                    ],
                    actor_staff_id=ids[
                        "staff_id"
                    ],
                    administered_at=None,
                    notes="Unknown outcome dose",
                    idempotency_key=key,
                    idempotency_user_id=ids[
                        "user_id"
                    ],
                )
            )

            first_id = first.id
            first_time = first.administered_at

            db.session.remove()

        # Simulate response loss: the first committed result is discarded.
        # The client knows only its original request and retries it.
        with app.app_context():
            retry = (
                emar_service
                .administer_medication(
                    administration_id=ids[
                        "administration_id"
                    ],
                    clinic_id=ids[
                        "clinic_id"
                    ],
                    actor_staff_id=ids[
                        "staff_id"
                    ],
                    administered_at=None,
                    notes="Unknown outcome dose",
                    idempotency_key=key,
                    idempotency_user_id=ids[
                        "user_id"
                    ],
                )
            )

            assert retry.id == first_id
            assert (
                retry.status
                == MedicationAdministrationStatus
                .ADMINISTERED
            )
            assert (
                retry.administered_at
                == first_time
            )

            rows = _ledger_rows(
                clinic_id=ids[
                    "clinic_id"
                ],
                user_id=ids[
                    "user_id"
                ],
                key=key,
            )

            assert len(rows) == 1
            assert (
                rows[0].entity_type
                == "MedicationAdministration"
            )
            assert (
                rows[0].entity_id
                == first_id
            )

            count = (
                db.session.execute(
                    select(
                        func.count(
                            MedicationAdministration.id
                        )
                    ).where(
                        MedicationAdministration.id
                        == ids[
                            "administration_id"
                        ],
                        MedicationAdministration.status
                        == MedicationAdministrationStatus
                        .ADMINISTERED,
                    )
                ).scalar_one()
            )

            assert count == 1

    finally:
        _cleanup(
            app,
            extensions,
            original_database_url,
            original_redis_client,
        )


def test_unknown_outcome_retry_with_changed_payload_fails_closed(
    monkeypatch,
):
    (
        app,
        extensions,
        original_database_url,
        original_redis_client,
    ) = _build_app()

    try:
        ids = _seed(
            app,
            suffix="mismatch",
        )

        monkeypatch.setattr(
            emar_service,
            "create_audit_log",
            lambda *args, **kwargs: None,
        )

        key = "gate6-unknown-mismatch"

        with app.app_context():
            first = (
                emar_service
                .administer_medication(
                    administration_id=ids[
                        "administration_id"
                    ],
                    clinic_id=ids[
                        "clinic_id"
                    ],
                    actor_staff_id=ids[
                        "staff_id"
                    ],
                    administered_at=None,
                    notes="Original payload",
                    idempotency_key=key,
                    idempotency_user_id=ids[
                        "user_id"
                    ],
                )
            )

            first_time = first.administered_at

        with app.app_context():
            with pytest.raises(
                ConflictError,
                match="different request payload",
            ) as exc_info:
                (
                    emar_service
                    .administer_medication(
                        administration_id=ids[
                            "administration_id"
                        ],
                        clinic_id=ids[
                            "clinic_id"
                        ],
                        actor_staff_id=ids[
                            "staff_id"
                        ],
                        administered_at=None,
                        notes="Changed payload",
                        idempotency_key=key,
                        idempotency_user_id=ids[
                            "user_id"
                        ],
                    )
                )

            assert (
                getattr(
                    exc_info.value,
                    "code",
                    None,
                )
                == "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH"
            )

            record = db.session.get(
                MedicationAdministration,
                ids[
                    "administration_id"
                ],
            )

            assert (
                record.status
                == MedicationAdministrationStatus
                .ADMINISTERED
            )
            assert (
                record.administered_at
                == first_time
            )
            assert (
                record.notes
                == "Original payload"
            )

            rows = _ledger_rows(
                clinic_id=ids[
                    "clinic_id"
                ],
                user_id=ids[
                    "user_id"
                ],
                key=key,
            )

            assert len(rows) == 1

    finally:
        _cleanup(
            app,
            extensions,
            original_database_url,
            original_redis_client,
        )


def test_precommit_failure_rolls_back_ledger_and_allows_safe_retry(
    monkeypatch,
):
    (
        app,
        extensions,
        original_database_url,
        original_redis_client,
    ) = _build_app()

    try:
        ids = _seed(
            app,
            suffix="rollback",
        )

        key = "gate6-precommit-rollback"

        original_audit = (
            emar_service
            ._audit_status_change
        )

        def fail_before_commit(**_kwargs):
            raise RuntimeError(
                "simulated failure before transaction commit"
            )

        monkeypatch.setattr(
            emar_service,
            "_audit_status_change",
            fail_before_commit,
        )

        with app.app_context():
            with pytest.raises(
                RuntimeError,
                match="simulated failure",
            ):
                (
                    emar_service
                    .administer_medication(
                        administration_id=ids[
                            "administration_id"
                        ],
                        clinic_id=ids[
                            "clinic_id"
                        ],
                        actor_staff_id=ids[
                            "staff_id"
                        ],
                        administered_at=None,
                        notes="Will roll back",
                        idempotency_key=key,
                        idempotency_user_id=ids[
                            "user_id"
                        ],
                    )
                )

            db.session.remove()

        with app.app_context():
            record = db.session.get(
                MedicationAdministration,
                ids[
                    "administration_id"
                ],
            )

            assert (
                record.status
                == MedicationAdministrationStatus
                .SCHEDULED
            )
            assert record.administered_at is None
            assert (
                record.administered_by_staff_id
                is None
            )

            rows = _ledger_rows(
                clinic_id=ids[
                    "clinic_id"
                ],
                user_id=ids[
                    "user_id"
                ],
                key=key,
            )

            assert rows == []

        monkeypatch.setattr(
            emar_service,
            "_audit_status_change",
            original_audit,
        )

        monkeypatch.setattr(
            emar_service,
            "create_audit_log",
            lambda *args, **kwargs: None,
        )

        with app.app_context():
            retry = (
                emar_service
                .administer_medication(
                    administration_id=ids[
                        "administration_id"
                    ],
                    clinic_id=ids[
                        "clinic_id"
                    ],
                    actor_staff_id=ids[
                        "staff_id"
                    ],
                    administered_at=None,
                    notes="Will roll back",
                    idempotency_key=key,
                    idempotency_user_id=ids[
                        "user_id"
                    ],
                )
            )

            assert (
                retry.status
                == MedicationAdministrationStatus
                .ADMINISTERED
            )

            rows = _ledger_rows(
                clinic_id=ids[
                    "clinic_id"
                ],
                user_id=ids[
                    "user_id"
                ],
                key=key,
            )

            assert len(rows) == 1
            assert (
                rows[0].entity_id
                == ids[
                    "administration_id"
                ]
            )

    finally:
        _cleanup(
            app,
            extensions,
            original_database_url,
            original_redis_client,
        )
