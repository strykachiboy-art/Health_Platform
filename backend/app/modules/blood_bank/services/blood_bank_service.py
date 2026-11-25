from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.blood_bank_enums import (
    BloodUnitSourceType,
    BloodUnitStatus,
    DonationStatus,
    DonorDeferralStatus,
    TtiScreeningStatus,
)
from app.core.enums.clinical_order_enums import ClinicalOrderLifecycleStatus
from app.core.enums.patient_enums import BloodType
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.blood_bank.models.blood_bank_model import (
    BloodDonation,
    BloodDonor,
    BloodStockThreshold,
    BloodUnit,
)
from app.modules.clinical_orders.models.blood_product_order_model import (
    BloodProductOrder,
)
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission, Bed, Ward


_PRODUCT_TYPES = {
    "packed_red_blood_cells",
    "whole_blood",
    "platelets",
    "fresh_frozen_plasma",
    "cryoprecipitate",
}

_KNOWN_BLOOD_TYPES = (
    BloodType.A_POS.value,
    BloodType.A_NEG.value,
    BloodType.B_POS.value,
    BloodType.B_NEG.value,
    BloodType.AB_POS.value,
    BloodType.AB_NEG.value,
    BloodType.O_POS.value,
    BloodType.O_NEG.value,
)

# Donor -> compatible recipient blood types for RBC / Whole Blood
_RBC_COMPATIBILITY: dict[str, set[str]] = {
    "O-": {"O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"},
    "O+": {"O+", "A+", "B+", "AB+"},
    "A-": {"A-", "A+", "AB-", "AB+"},
    "A+": {"A+", "AB+"},
    "B-": {"B-", "B+", "AB-", "AB+"},
    "B+": {"B+", "AB+"},
    "AB-": {"AB-", "AB+"},
    "AB+": {"AB+"},
}

# Donor -> compatible recipient blood types for Plasma / Cryoprecipitate
_PLASMA_COMPATIBILITY: dict[str, set[str]] = {
    "AB+": {"O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"},
    "AB-": {"O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"},
    "A+": {"A-", "A+", "O-", "O+"},
    "A-": {"A-", "O-"},
    "B+": {"B-", "B+", "O-", "O+"},
    "B-": {"B-", "O-"},
    "O+": {"O-", "O+"},
    "O-": {"O-"},
}

_REACTIVE_TTI_VALUES = {"reactive", "positive", "detected", "fail", "failed"}
_NEGATIVE_TTI_VALUES = {"negative", "non_reactive", "non-reactive", "cleared", "pass"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _positive_id(value, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{field_name} must be a positive integer")
    return value


def _required_text(value, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError(f"{field_name} is required")
    if len(cleaned) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed {max_length} characters"
        )
    return cleaned


def _optional_text(value, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed {max_length} characters"
        )
    return cleaned


def _normalize_product_type(value) -> str:
    cleaned = _required_text(value, "product_type", 40).lower()
    if cleaned not in _PRODUCT_TYPES:
        raise ValidationError("Invalid blood product type")
    return cleaned


def _normalize_blood_type(value, *, allow_unknown: bool = False) -> str:
    raw = getattr(value, "value", value)
    try:
        bt = BloodType(raw).value
    except (TypeError, ValueError) as exc:
        raise ValidationError("Invalid blood_type") from exc
    if not allow_unknown and bt == BloodType.UNKNOWN.value:
        raise ValidationError("blood_type cannot be unknown")
    return bt


def compatible_donor_blood_types(
    recipient_blood_type: str,
    product_type: str,
    *,
    emergency_release: bool = False,
) -> list[str]:
    norm_product = _normalize_product_type(product_type)
    recipient = _normalize_blood_type(
        recipient_blood_type,
        allow_unknown=emergency_release,
    )
    if recipient == BloodType.UNKNOWN.value:
        if norm_product in {"fresh_frozen_plasma", "cryoprecipitate"}:
            return ["AB-", "AB+"]
        return ["O-"]

    matrix = (
        _PLASMA_COMPATIBILITY
        if norm_product in {"fresh_frozen_plasma", "cryoprecipitate"}
        else _RBC_COMPATIBILITY
    )
    return [
        donor_bt
        for donor_bt in _KNOWN_BLOOD_TYPES
        if recipient in matrix.get(donor_bt, set())
    ]


def is_blood_unit_compatible(
    *,
    unit_blood_type: str,
    recipient_blood_type: str,
    product_type: str,
    emergency_release: bool = False,
) -> bool:
    unit_bt = _normalize_blood_type(unit_blood_type, allow_unknown=False)
    allowed = compatible_donor_blood_types(
        recipient_blood_type,
        product_type,
        emergency_release=emergency_release,
    )
    return unit_bt in allowed


def _get_staff(
    staff_id: int,
    clinic_id: int,
    *,
    allowed_roles: tuple[Role, ...],
    role_message: str,
) -> Staff:
    staff = db.session.execute(
        select(Staff).where(
            Staff.id == _positive_id(staff_id, "staff_id"),
            Staff.clinic_id == _positive_id(clinic_id, "clinic_id"),
        )
    ).scalar_one_or_none()
    if staff is None:
        raise NotFoundError(f"Staff {staff_id} not found")
    if staff.status != StaffStatus.ACTIVE:
        raise ValidationError("Blood bank actor must be active")
    if staff.user is None or not staff.user.is_active:
        raise ValidationError("Blood bank actor user must be active")
    if staff.user.role not in allowed_roles:
        raise ValidationError(role_message)
    return staff


def _audit(
    *,
    actor: Staff,
    action: AuditAction,
    entity_type: str,
    entity_id: int,
    description: str,
    old_value=None,
    new_value=None,
) -> None:
    create_audit_log(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        old_value=old_value,
        new_value=new_value,
        user_id=actor.user_id,
        clinic_id=actor.clinic_id,
    )


# ---------------------------------------------------------------------------
# Donors & Deferrals
# ---------------------------------------------------------------------------


@transactional
def register_blood_donor(
    *,
    clinic_id: int,
    actor_staff_id: int,
    donor_code: str,
    full_name: str,
    phone: str,
    email: str | None = None,
    date_of_birth: date | None = None,
    blood_type: BloodType | str = BloodType.UNKNOWN,
    cooldown_days: int = 56,
    consent_to_contact: bool = True,
    notes: str | None = None,
) -> BloodDonor:
    actor = _get_staff(
        actor_staff_id,
        clinic_id,
        allowed_roles=(Role.NURSE, Role.LAB_TECHNICIAN, Role.ADMIN),
        role_message="Donor registration requires nurse, lab technician, or admin role",
    )
    code = _required_text(donor_code, "donor_code", 64)
    existing = db.session.execute(
        select(BloodDonor).where(
            BloodDonor.clinic_id == clinic_id,
            BloodDonor.donor_code == code,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(f"Donor code '{code}' already exists in clinic")

    if isinstance(cooldown_days, bool) or not isinstance(cooldown_days, int) or cooldown_days <= 0:
        raise ValidationError("cooldown_days must be a positive integer")

    donor = BloodDonor(
        clinic_id=clinic_id,
        donor_code=code,
        full_name=_required_text(full_name, "full_name", 200),
        phone=_required_text(phone, "phone", 40),
        email=_optional_text(email, "email", 255),
        date_of_birth=date_of_birth,
        blood_type=_normalize_blood_type(blood_type, allow_unknown=True),
        deferral_status=DonorDeferralStatus.ELIGIBLE.value,
        cooldown_days=cooldown_days,
        total_donations=0,
        consent_to_contact=bool(consent_to_contact),
        notes=_optional_text(notes, "notes", 5000),
        registered_by_staff_id=actor.id,
        version=1,
    )
    db.session.add(donor)
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="BloodDonor",
        entity_id=donor.id,
        description="Registered blood donor",
        new_value={
            "donor_code": donor.donor_code,
            "blood_type": donor.blood_type,
            "deferral_status": donor.deferral_status,
        },
    )
    return donor


def get_blood_donor(donor_id: int, *, clinic_id: int) -> BloodDonor:
    donor = db.session.execute(
        select(BloodDonor).where(
            BloodDonor.id == _positive_id(donor_id, "donor_id"),
            BloodDonor.clinic_id == _positive_id(clinic_id, "clinic_id"),
        )
    ).scalar_one_or_none()
    if donor is None:
        raise NotFoundError(f"Blood donor {donor_id} not found")
    return donor


def list_blood_donors(
    *,
    clinic_id: int,
    blood_type: str | None = None,
    deferral_status: str | None = None,
) -> list[BloodDonor]:
    stmt = select(BloodDonor).where(
        BloodDonor.clinic_id == _positive_id(clinic_id, "clinic_id")
    )
    if blood_type is not None:
        stmt = stmt.where(
            BloodDonor.blood_type
            == _normalize_blood_type(blood_type, allow_unknown=True)
        )
    if deferral_status is not None:
        stmt = stmt.where(
            BloodDonor.deferral_status == DonorDeferralStatus(deferral_status).value
        )
    return list(
        db.session.execute(stmt.order_by(BloodDonor.id.desc())).scalars().all()
    )


@transactional
def update_blood_donor_deferral(
    donor_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    deferral_status: DonorDeferralStatus | str,
    deferral_reason: str | None = None,
    deferred_until: datetime | None = None,
    consent_to_contact: bool | None = None,
) -> BloodDonor:
    actor = _get_staff(
        actor_staff_id,
        clinic_id,
        allowed_roles=(Role.NURSE, Role.LAB_TECHNICIAN, Role.DOCTOR, Role.ADMIN),
        role_message="Donor deferral update requires clinical or admin role",
    )
    donor = db.session.execute(
        select(BloodDonor)
        .where(
            BloodDonor.id == _positive_id(donor_id, "donor_id"),
            BloodDonor.clinic_id == _positive_id(clinic_id, "clinic_id"),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if donor is None:
        raise NotFoundError(f"Blood donor {donor_id} not found")

    if donor.deferral_status == DonorDeferralStatus.PERMANENT_DEFERRAL.value:
        target_status = DonorDeferralStatus(deferral_status).value
        if target_status != DonorDeferralStatus.PERMANENT_DEFERRAL.value:
            raise ConflictError(
                "Permanent donor deferral cannot be reverted"
            )

    target_status = DonorDeferralStatus(deferral_status).value
    reason = _optional_text(deferral_reason, "deferral_reason", 5000)

    if target_status == DonorDeferralStatus.TEMPORARY_DEFERRAL.value:
        if deferred_until is None or reason is None:
            raise ValidationError(
                "temporary_deferral requires deferred_until and deferral_reason"
            )
        donor.deferred_until = _as_utc_naive(deferred_until)
        donor.deferral_reason = reason
    elif target_status == DonorDeferralStatus.PERMANENT_DEFERRAL.value:
        if reason is None:
            raise ValidationError(
                "permanent_deferral requires deferral_reason"
            )
        donor.deferred_until = None
        donor.deferral_reason = reason
    else:
        donor.deferred_until = None
        donor.deferral_reason = reason

    old_status = donor.deferral_status
    donor.deferral_status = target_status
    if consent_to_contact is not None:
        donor.consent_to_contact = bool(consent_to_contact)
    donor.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="BloodDonor",
        entity_id=donor.id,
        description="Updated blood donor deferral status",
        old_value={"deferral_status": old_status},
        new_value={
            "deferral_status": donor.deferral_status,
            "deferral_reason": donor.deferral_reason,
            "version": donor.version,
        },
    )
    return donor


def _donor_is_eligible_now(donor: BloodDonor, now: datetime) -> tuple[bool, str | None]:
    if donor.deferral_status == DonorDeferralStatus.PERMANENT_DEFERRAL.value:
        return False, "Donor is permanently deferred"
    if donor.deferral_status == DonorDeferralStatus.TEMPORARY_DEFERRAL.value:
        if donor.deferred_until is None or _as_utc_naive(donor.deferred_until) > now:
            return False, "Donor is under active temporary deferral"
    if donor.last_donated_at is not None:
        next_eligible = _as_utc_naive(donor.last_donated_at) + timedelta(
            days=donor.cooldown_days
        )
        if next_eligible > now:
            return False, (
                f"Donor is within mandatory {donor.cooldown_days}-day cooldown "
                f"(eligible after {next_eligible.isoformat()})"
            )
    return True, None


# ---------------------------------------------------------------------------
# Donations & TTI Screening Gate -> Quarantined BloodUnits
# ---------------------------------------------------------------------------


@transactional
def record_blood_donation(
    *,
    clinic_id: int,
    actor_staff_id: int,
    donor_id: int,
    donation_number: str,
    volume_ml: int,
    components: list[dict],
    collected_at: datetime | None = None,
    hemoglobin_g_dl: Decimal | None = None,
    systolic_bp: int | None = None,
    diastolic_bp: int | None = None,
    pulse_bpm: int | None = None,
    intake_passed: bool = True,
    intake_notes: str | None = None,
    replacement_for_order_id: int | None = None,
    replacement_for_admission_id: int | None = None,
) -> tuple[BloodDonation, list[BloodUnit]]:
    actor = _get_staff(
        actor_staff_id,
        clinic_id,
        allowed_roles=(Role.NURSE, Role.LAB_TECHNICIAN),
        role_message="Blood donation collection requires nurse or lab technician role",
    )
    donor = db.session.execute(
        select(BloodDonor)
        .where(
            BloodDonor.id == _positive_id(donor_id, "donor_id"),
            BloodDonor.clinic_id == _positive_id(clinic_id, "clinic_id"),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if donor is None:
        raise NotFoundError(f"Blood donor {donor_id} not found")

    if donor.blood_type == BloodType.UNKNOWN.value:
        raise ConflictError(
            "Donor blood_type must be known before recording a donation"
        )
    if not intake_passed:
        raise ValidationError("Donation cannot be collected when intake_passed is false")
    if hemoglobin_g_dl is not None and hemoglobin_g_dl < Decimal("12.0"):
        raise ValidationError(
            "Donor hemoglobin is below safe collection threshold (12.0 g/dL)"
        )

    collection_time = (
        _as_utc_naive(collected_at) if collected_at is not None else _utcnow()
    )
    eligible, reason = _donor_is_eligible_now(donor, collection_time)
    if not eligible:
        raise ConflictError(reason or "Donor is not eligible to donate")

    number = _required_text(donation_number, "donation_number", 64)
    dup_num = db.session.execute(
        select(BloodDonation.id).where(
            BloodDonation.clinic_id == clinic_id,
            BloodDonation.donation_number == number,
        )
    ).scalar_one_or_none()
    if dup_num is not None:
        raise ConflictError(f"Donation number '{number}' already exists")

    if replacement_for_order_id is not None:
        order = db.session.execute(
            select(BloodProductOrder).where(
                BloodProductOrder.id
                == _positive_id(replacement_for_order_id, "replacement_for_order_id"),
                BloodProductOrder.clinic_id == clinic_id,
            )
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError(
                f"Blood product order {replacement_for_order_id} not found"
            )
        if replacement_for_admission_id is None:
            replacement_for_admission_id = order.admission_id

    if replacement_for_admission_id is not None:
        admission = db.session.execute(
            select(Admission)
            .join(Bed, Admission.bed_id == Bed.id)
            .join(Ward, Bed.ward_id == Ward.id)
            .where(
                Admission.id
                == _positive_id(
                    replacement_for_admission_id,
                    "replacement_for_admission_id",
                ),
                Ward.clinic_id == clinic_id,
            )
        ).scalar_one_or_none()
        if admission is None:
            raise NotFoundError(
                f"Admission {replacement_for_admission_id} not found"
            )

    if not components:
        raise ValidationError("At least one blood unit component is required")

    donation = BloodDonation(
        clinic_id=clinic_id,
        donor_id=donor.id,
        donation_number=number,
        collected_by_staff_id=actor.id,
        collected_at=collection_time,
        volume_ml=volume_ml,
        hemoglobin_g_dl=hemoglobin_g_dl,
        systolic_bp=systolic_bp,
        diastolic_bp=diastolic_bp,
        pulse_bpm=pulse_bpm,
        intake_passed=True,
        intake_notes=_optional_text(intake_notes, "intake_notes", 5000),
        tti_screening_status=TtiScreeningStatus.PENDING.value,
        status=DonationStatus.SCREENING_PENDING.value,
        replacement_for_order_id=replacement_for_order_id,
        replacement_for_admission_id=replacement_for_admission_id,
        version=1,
    )
    db.session.add(donation)
    db.session.flush()

    created_units: list[BloodUnit] = []
    seen_barcodes: set[str] = set()
    for spec in components:
        unit_barcode = _required_text(spec["unit_barcode"], "unit_barcode", 128)
        if unit_barcode in seen_barcodes:
            raise ValidationError(f"Duplicate unit_barcode '{unit_barcode}' in payload")
        seen_barcodes.add(unit_barcode)

        existing_barcode = db.session.execute(
            select(BloodUnit.id).where(
                BloodUnit.clinic_id == clinic_id,
                BloodUnit.unit_barcode == unit_barcode,
            )
        ).scalar_one_or_none()
        if existing_barcode is not None:
            raise ConflictError(f"Blood unit barcode '{unit_barcode}' already exists")

        product_type = _normalize_product_type(spec["product_type"])
        expires_at = _as_utc_naive(spec["expires_at"])
        if expires_at <= collection_time:
            raise ValidationError("Blood unit expires_at must be after collected_at")

        # Every in-house collected unit starts QUARANTINED with TTI PENDING.
        unit = BloodUnit(
            clinic_id=clinic_id,
            unit_barcode=unit_barcode,
            din=number,
            source_type=BloodUnitSourceType.IN_HOUSE_DONATION.value,
            donation_id=donation.id,
            product_type=product_type,
            blood_type=donor.blood_type,
            volume_ml=int(spec["volume_ml"]),
            collected_at=collection_time,
            expires_at=expires_at,
            tti_screening_status=TtiScreeningStatus.PENDING.value,
            status=BloodUnitStatus.QUARANTINED.value,
            received_or_created_by_staff_id=actor.id,
            version=1,
        )
        db.session.add(unit)
        created_units.append(unit)

    donor.last_donated_at = collection_time
    donor.total_donations += 1
    if donor.deferral_status == DonorDeferralStatus.TEMPORARY_DEFERRAL.value:
        donor.deferral_status = DonorDeferralStatus.ELIGIBLE.value
        donor.deferred_until = None
    donor.version += 1

    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="BloodDonation",
        entity_id=donation.id,
        description="Recorded blood donation and quarantined component units",
        new_value={
            "donation_number": donation.donation_number,
            "donor_id": donor.id,
            "unit_count": len(created_units),
            "status": donation.status,
        },
    )
    return donation, created_units


def _tti_results_have_reactive_marker(tti_results: dict) -> bool:
    for value in tti_results.values():
        if isinstance(value, str) and value.strip().lower() in _REACTIVE_TTI_VALUES:
            return True
    return False


def _tti_results_all_negative(tti_results: dict) -> bool:
    if not tti_results:
        return False
    for value in tti_results.values():
        if not isinstance(value, str) or value.strip().lower() not in _NEGATIVE_TTI_VALUES:
            return False
    return True


@transactional
def screen_blood_donation(
    donation_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    tti_screening_status: TtiScreeningStatus | str,
    tti_results: dict,
    confirmed_blood_type: BloodType | str | None = None,
    deferral_reason: str | None = None,
) -> tuple[BloodDonation, list[BloodUnit]]:
    actor = _get_staff(
        actor_staff_id,
        clinic_id,
        allowed_roles=(Role.LAB_TECHNICIAN,),
        role_message="TTI screening requires lab technician role",
    )
    donation = db.session.execute(
        select(BloodDonation)
        .where(
            BloodDonation.id == _positive_id(donation_id, "donation_id"),
            BloodDonation.clinic_id == _positive_id(clinic_id, "clinic_id"),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if donation is None:
        raise NotFoundError(f"Blood donation {donation_id} not found")

    if donation.status not in {
        DonationStatus.COLLECTED.value,
        DonationStatus.SCREENING_PENDING.value,
    }:
        raise ConflictError("Blood donation screening has already been finalized")

    target_tti = TtiScreeningStatus(tti_screening_status).value
    if target_tti == TtiScreeningStatus.PENDING.value:
        raise ValidationError("tti_screening_status must resolve to cleared, reactive, or indeterminate")

    if target_tti == TtiScreeningStatus.CLEARED.value:
        if _tti_results_have_reactive_marker(tti_results) or not _tti_results_all_negative(tti_results):
            raise ValidationError(
                "Cleared TTI screening requires all tested markers to be negative/non_reactive"
            )

    donor = db.session.execute(
        select(BloodDonor)
        .where(
            BloodDonor.id == donation.donor_id,
            BloodDonor.clinic_id == clinic_id,
        )
        .with_for_update()
    ).scalar_one()

    units = list(
        db.session.execute(
            select(BloodUnit)
            .where(
                BloodUnit.donation_id == donation.id,
                BloodUnit.clinic_id == clinic_id,
            )
            .with_for_update()
        ).scalars().all()
    )

    now = _utcnow()
    donation.tti_screening_status = target_tti
    donation.tti_results = dict(tti_results)
    donation.screened_by_staff_id = actor.id
    donation.screened_at = now
    donation.version += 1

    if confirmed_blood_type is not None:
        confirmed_bt = _normalize_blood_type(confirmed_blood_type, allow_unknown=False)
        donor.blood_type = confirmed_bt
        donor.version += 1
        for unit in units:
            unit.blood_type = confirmed_bt

    if target_tti == TtiScreeningStatus.CLEARED.value:
        donation.status = DonationStatus.CLEARED.value
        for unit in units:
            unit.tti_screening_status = TtiScreeningStatus.CLEARED.value
            if unit.status == BloodUnitStatus.QUARANTINED.value:
                if _as_utc_naive(unit.expires_at) <= now:
                    unit.status = BloodUnitStatus.EXPIRED.value
                else:
                    unit.status = BloodUnitStatus.AVAILABLE.value
                    unit.released_by_staff_id = actor.id
                    unit.released_at = now
            unit.version += 1
    else:
        # Reactive or indeterminate -> discard derived units and defer donor.
        donation.status = DonationStatus.REACTIVELY_DISCARDED.value
        reason = _optional_text(deferral_reason, "deferral_reason", 5000) or (
            f"Donation {donation.donation_number} failed TTI screening ({target_tti})"
        )
        if target_tti == TtiScreeningStatus.REACTIVE.value:
            donor.deferral_status = DonorDeferralStatus.PERMANENT_DEFERRAL.value
            donor.deferral_reason = reason
            donor.deferred_until = None
        else:
            donor.deferral_status = DonorDeferralStatus.TEMPORARY_DEFERRAL.value
            donor.deferral_reason = reason
            donor.deferred_until = now + timedelta(days=90)
        donor.version += 1

        for unit in units:
            unit.tti_screening_status = target_tti
            if unit.status in {
                BloodUnitStatus.QUARANTINED.value,
                BloodUnitStatus.AVAILABLE.value,
                BloodUnitStatus.RESERVED.value,
            }:
                unit.status = BloodUnitStatus.DISCARDED.value
                unit.reserved_for_order_id = None
                unit.reserved_by_staff_id = None
                unit.reserved_at = None
                unit.discarded_by_staff_id = actor.id
                unit.discarded_at = now
                unit.discard_reason = reason
            unit.version += 1

    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="BloodDonation",
        entity_id=donation.id,
        description=f"Finalized TTI screening ({target_tti})",
        new_value={
            "tti_screening_status": donation.tti_screening_status,
            "status": donation.status,
            "donor_deferral_status": donor.deferral_status,
            "units_updated": [u.id for u in units],
        },
    )
    return donation, units


# ---------------------------------------------------------------------------
# Blood Unit Inventory (External Receipts, Listing, Discard, Expiry Sweep)
# ---------------------------------------------------------------------------


@transactional
def receive_external_blood_unit(
    *,
    clinic_id: int,
    actor_staff_id: int,
    unit_barcode: str,
    din: str,
    external_supplier_name: str,
    product_type: str,
    blood_type: BloodType | str,
    volume_ml: int,
    collected_at: datetime,
    expires_at: datetime,
    external_reference: str | None = None,
    tti_screening_cleared: bool = True,
    notes: str | None = None,
) -> BloodUnit:
    actor = _get_staff(
        actor_staff_id,
        clinic_id,
        allowed_roles=(Role.LAB_TECHNICIAN, Role.ADMIN),
        role_message="Receiving external blood units requires lab technician or admin role",
    )
    barcode = _required_text(unit_barcode, "unit_barcode", 128)
    clean_din = _required_text(din, "din", 64)
    supplier = _required_text(external_supplier_name, "external_supplier_name", 200)
    norm_product = _normalize_product_type(product_type)
    norm_blood = _normalize_blood_type(blood_type, allow_unknown=False)

    coll = _as_utc_naive(collected_at)
    exp = _as_utc_naive(expires_at)
    if exp <= coll:
        raise ValidationError("expires_at must be after collected_at")

    dup_barcode = db.session.execute(
        select(BloodUnit.id).where(
            BloodUnit.clinic_id == clinic_id,
            BloodUnit.unit_barcode == barcode,
        )
    ).scalar_one_or_none()
    if dup_barcode is not None:
        raise ConflictError(f"Blood unit barcode '{barcode}' already exists")

    dup_din = db.session.execute(
        select(BloodUnit.id).where(
            BloodUnit.clinic_id == clinic_id,
            BloodUnit.din == clean_din,
            BloodUnit.product_type == norm_product,
        )
    ).scalar_one_or_none()
    if dup_din is not None:
        raise ConflictError(
            f"Blood unit DIN '{clean_din}' for {norm_product} already exists"
        )

    now = _utcnow()
    if exp <= now:
        raise ValidationError("Cannot receive an already expired blood unit")

    tti_status = (
        TtiScreeningStatus.CLEARED.value
        if tti_screening_cleared
        else TtiScreeningStatus.PENDING.value
    )
    initial_status = (
        BloodUnitStatus.AVAILABLE.value
        if tti_screening_cleared
        else BloodUnitStatus.QUARANTINED.value
    )

    unit = BloodUnit(
        clinic_id=clinic_id,
        unit_barcode=barcode,
        din=clean_din,
        source_type=BloodUnitSourceType.EXTERNAL_BLOOD_BANK.value,
        external_supplier_name=supplier,
        external_reference=_optional_text(external_reference, "external_reference", 128),
        product_type=norm_product,
        blood_type=norm_blood,
        volume_ml=volume_ml,
        collected_at=coll,
        expires_at=exp,
        tti_screening_status=tti_status,
        status=initial_status,
        received_or_created_by_staff_id=actor.id,
        released_by_staff_id=actor.id if tti_screening_cleared else None,
        released_at=now if tti_screening_cleared else None,
        notes=_optional_text(notes, "notes", 5000),
        version=1,
    )
    db.session.add(unit)
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="BloodUnit",
        entity_id=unit.id,
        description="Received external blood bank unit",
        new_value={
            "unit_barcode": unit.unit_barcode,
            "product_type": unit.product_type,
            "blood_type": unit.blood_type,
            "status": unit.status,
        },
    )
    return unit


def get_blood_unit(unit_id: int, *, clinic_id: int) -> BloodUnit:
    unit = db.session.execute(
        select(BloodUnit).where(
            BloodUnit.id == _positive_id(unit_id, "unit_id"),
            BloodUnit.clinic_id == _positive_id(clinic_id, "clinic_id"),
        )
    ).scalar_one_or_none()
    if unit is None:
        raise NotFoundError(f"Blood unit {unit_id} not found")
    return unit


def get_blood_unit_by_barcode(unit_barcode: str, *, clinic_id: int) -> BloodUnit:
    barcode = _required_text(unit_barcode, "unit_barcode", 128)
    unit = db.session.execute(
        select(BloodUnit).where(
            BloodUnit.clinic_id == _positive_id(clinic_id, "clinic_id"),
            BloodUnit.unit_barcode == barcode,
        )
    ).scalar_one_or_none()
    if unit is None:
        raise NotFoundError(f"Blood unit with barcode '{barcode}' not found")
    return unit


def list_blood_units(
    *,
    clinic_id: int,
    status: str | None = None,
    product_type: str | None = None,
    blood_type: str | None = None,
    include_expired: bool = True,
) -> list[BloodUnit]:
    stmt = select(BloodUnit).where(
        BloodUnit.clinic_id == _positive_id(clinic_id, "clinic_id")
    )
    if status is not None:
        stmt = stmt.where(BloodUnit.status == BloodUnitStatus(status).value)
    if product_type is not None:
        stmt = stmt.where(
            BloodUnit.product_type == _normalize_product_type(product_type)
        )
    if blood_type is not None:
        stmt = stmt.where(
            BloodUnit.blood_type == _normalize_blood_type(blood_type)
        )
    if not include_expired:
        stmt = stmt.where(BloodUnit.expires_at > _utcnow())
    return list(
        db.session.execute(
            stmt.order_by(BloodUnit.expires_at.asc(), BloodUnit.id.asc())
        ).scalars().all()
    )


@transactional
def discard_blood_unit(
    unit_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    reason: str,
) -> BloodUnit:
    actor = _get_staff(
        actor_staff_id,
        clinic_id,
        allowed_roles=(Role.LAB_TECHNICIAN, Role.ADMIN),
        role_message="Discarding blood units requires lab technician or admin role",
    )
    unit = db.session.execute(
        select(BloodUnit)
        .where(
            BloodUnit.id == _positive_id(unit_id, "unit_id"),
            BloodUnit.clinic_id == _positive_id(clinic_id, "clinic_id"),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if unit is None:
        raise NotFoundError(f"Blood unit {unit_id} not found")

    if unit.status in {
        BloodUnitStatus.ISSUED.value,
        BloodUnitStatus.TRANSFUSED.value,
        BloodUnitStatus.DISCARDED.value,
    }:
        raise ConflictError(
            f"Blood unit in status '{unit.status}' cannot be discarded"
        )

    clean_reason = _required_text(reason, "reason", 5000)
    old_status = unit.status
    unit.status = BloodUnitStatus.DISCARDED.value
    unit.reserved_for_order_id = None
    unit.reserved_by_staff_id = None
    unit.reserved_at = None
    unit.discarded_by_staff_id = actor.id
    unit.discarded_at = _utcnow()
    unit.discard_reason = clean_reason
    unit.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="BloodUnit",
        entity_id=unit.id,
        description="Discarded blood unit",
        old_value={"status": old_status},
        new_value={
            "status": unit.status,
            "discard_reason": unit.discard_reason,
            "version": unit.version,
        },
    )
    return unit


@transactional
def expire_stale_blood_units(
    *,
    clinic_id: int,
    actor_staff_id: int,
) -> list[BloodUnit]:
    actor = _get_staff(
        actor_staff_id,
        clinic_id,
        allowed_roles=(Role.LAB_TECHNICIAN, Role.ADMIN),
        role_message="Expiring stale blood units requires lab technician or admin role",
    )
    now = _utcnow()
    stale = list(
        db.session.execute(
            select(BloodUnit)
            .where(
                BloodUnit.clinic_id == clinic_id,
                BloodUnit.status.in_([
                    BloodUnitStatus.QUARANTINED.value,
                    BloodUnitStatus.AVAILABLE.value,
                    BloodUnitStatus.RESERVED.value,
                ]),
                BloodUnit.expires_at <= now,
            )
            .with_for_update()
        ).scalars().all()
    )
    for unit in stale:
        old_status = unit.status
        unit.status = BloodUnitStatus.EXPIRED.value
        unit.reserved_for_order_id = None
        unit.reserved_by_staff_id = None
        unit.reserved_at = None
        unit.version += 1
        _audit(
            actor=actor,
            action=AuditAction.STATUS_CHANGE,
            entity_type="BloodUnit",
            entity_id=unit.id,
            description="Marked expired blood unit",
            old_value={"status": old_status},
            new_value={"status": unit.status, "version": unit.version},
        )
    db.session.flush()
    return stale


# ---------------------------------------------------------------------------
# Stock Thresholds, Live Stock Summary & Demand-Driven Donor Matching
# ---------------------------------------------------------------------------


@transactional
def upsert_blood_stock_threshold(
    *,
    clinic_id: int,
    actor_staff_id: int,
    product_type: str,
    blood_type: BloodType | str,
    minimum_units: int,
    critical_units: int,
) -> BloodStockThreshold:
    actor = _get_staff(
        actor_staff_id,
        clinic_id,
        allowed_roles=(Role.ADMIN,),
        role_message="Stock threshold configuration requires admin role",
    )
    norm_product = _normalize_product_type(product_type)
    norm_blood = _normalize_blood_type(blood_type, allow_unknown=False)
    if critical_units > minimum_units:
        raise ValidationError("critical_units cannot exceed minimum_units")

    existing = db.session.execute(
        select(BloodStockThreshold)
        .where(
            BloodStockThreshold.clinic_id == clinic_id,
            BloodStockThreshold.product_type == norm_product,
            BloodStockThreshold.blood_type == norm_blood,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if existing is None:
        threshold = BloodStockThreshold(
            clinic_id=clinic_id,
            product_type=norm_product,
            blood_type=norm_blood,
            minimum_units=minimum_units,
            critical_units=critical_units,
            updated_by_staff_id=actor.id,
        )
        db.session.add(threshold)
        db.session.flush()
        action = AuditAction.CREATE
    else:
        threshold = existing
        threshold.minimum_units = minimum_units
        threshold.critical_units = critical_units
        threshold.updated_by_staff_id = actor.id
        db.session.flush()
        action = AuditAction.UPDATE

    _audit(
        actor=actor,
        action=action,
        entity_type="BloodStockThreshold",
        entity_id=threshold.id,
        description="Configured blood stock threshold",
        new_value={
            "product_type": threshold.product_type,
            "blood_type": threshold.blood_type,
            "minimum_units": threshold.minimum_units,
            "critical_units": threshold.critical_units,
        },
    )
    return threshold


def get_blood_stock_summary(
    *,
    clinic_id: int,
    product_type: str | None = None,
) -> dict:
    _positive_id(clinic_id, "clinic_id")
    now = _utcnow()
    soon = now + timedelta(hours=72)

    stmt = select(BloodUnit).where(BloodUnit.clinic_id == clinic_id)
    if product_type is not None:
        stmt = stmt.where(
            BloodUnit.product_type == _normalize_product_type(product_type)
        )
    units = list(db.session.execute(stmt).scalars().all())

    thresh_stmt = select(BloodStockThreshold).where(
        BloodStockThreshold.clinic_id == clinic_id
    )
    if product_type is not None:
        thresh_stmt = thresh_stmt.where(
            BloodStockThreshold.product_type == _normalize_product_type(product_type)
        )
    thresholds = {
        (t.product_type, t.blood_type): t
        for t in db.session.execute(thresh_stmt).scalars().all()
    }

    buckets: dict[tuple[str, str], dict] = {}
    for (pt, bt), t in thresholds.items():
        buckets[(pt, bt)] = {
            "product_type": pt,
            "blood_type": bt,
            "available_units": 0,
            "reserved_units": 0,
            "quarantined_units": 0,
            "expiring_within_72h_units": 0,
            "minimum_units": t.minimum_units,
            "critical_units": t.critical_units,
        }

    total_available = 0
    total_quarantined = 0
    for u in units:
        if u.status not in {
            BloodUnitStatus.AVAILABLE.value,
            BloodUnitStatus.RESERVED.value,
            BloodUnitStatus.QUARANTINED.value,
        }:
            continue
        key = (u.product_type, u.blood_type)
        if key not in buckets:
            t = thresholds.get(key)
            buckets[key] = {
                "product_type": u.product_type,
                "blood_type": u.blood_type,
                "available_units": 0,
                "reserved_units": 0,
                "quarantined_units": 0,
                "expiring_within_72h_units": 0,
                "minimum_units": t.minimum_units if t else 2,
                "critical_units": t.critical_units if t else 1,
            }
        exp = _as_utc_naive(u.expires_at)
        if u.status == BloodUnitStatus.AVAILABLE.value and exp > now:
            buckets[key]["available_units"] += 1
            total_available += 1
            if exp <= soon:
                buckets[key]["expiring_within_72h_units"] += 1
        elif u.status == BloodUnitStatus.RESERVED.value and exp > now:
            buckets[key]["reserved_units"] += 1
        elif u.status == BloodUnitStatus.QUARANTINED.value:
            buckets[key]["quarantined_units"] += 1
            total_quarantined += 1

    items = []
    for key in sorted(buckets.keys()):
        row = buckets[key]
        avail = row["available_units"]
        if avail <= row["critical_units"]:
            row["stock_level"] = "critical"
        elif avail < row["minimum_units"]:
            row["stock_level"] = "low"
        else:
            row["stock_level"] = "adequate"
        items.append(row)

    return {
        "items": items,
        "total_available": total_available,
        "total_quarantined": total_quarantined,
    }


def find_eligible_donors_for_shortage(
    *,
    clinic_id: int,
    blood_product_order_id: int | None = None,
    product_type: str | None = None,
    target_blood_type: BloodType | str | None = None,
) -> dict:
    _positive_id(clinic_id, "clinic_id")
    now = _utcnow()

    order: BloodProductOrder | None = None
    if blood_product_order_id is not None:
        order = db.session.execute(
            select(BloodProductOrder).where(
                BloodProductOrder.id
                == _positive_id(blood_product_order_id, "blood_product_order_id"),
                BloodProductOrder.clinic_id == clinic_id,
            )
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError(
                f"Blood product order {blood_product_order_id} not found"
            )
        resolved_product = order.product_type
        resolved_blood = (
            order.patient_blood_type_verified or order.patient_blood_type_at_order
        )
        emergency_mode = (
            order.compatibility_status == "emergency_release"
            or resolved_blood == BloodType.UNKNOWN.value
        )
    else:
        if product_type is None or target_blood_type is None:
            raise ValidationError(
                "Either blood_product_order_id or both product_type and target_blood_type are required"
            )
        resolved_product = _normalize_product_type(product_type)
        resolved_blood = _normalize_blood_type(target_blood_type, allow_unknown=True)
        emergency_mode = resolved_blood == BloodType.UNKNOWN.value

    compatible_types = compatible_donor_blood_types(
        resolved_blood,
        resolved_product,
        emergency_release=emergency_mode,
    )

    available_units = db.session.execute(
        select(BloodUnit).where(
            BloodUnit.clinic_id == clinic_id,
            BloodUnit.product_type == resolved_product,
            BloodUnit.blood_type.in_(compatible_types),
            BloodUnit.status == BloodUnitStatus.AVAILABLE.value,
            BloodUnit.tti_screening_status == TtiScreeningStatus.CLEARED.value,
            BloodUnit.expires_at > now,
        )
    ).scalars().all()
    avail_count = len(available_units)

    threshold = db.session.execute(
        select(BloodStockThreshold).where(
            BloodStockThreshold.clinic_id == clinic_id,
            BloodStockThreshold.product_type == resolved_product,
            BloodStockThreshold.blood_type == resolved_blood,
        )
    ).scalar_one_or_none()
    needed = order.units_requested if order is not None else (
        threshold.minimum_units if threshold is not None else 1
    )
    shortage = avail_count < needed

    donors = list(
        db.session.execute(
            select(BloodDonor)
            .where(
                BloodDonor.clinic_id == clinic_id,
                BloodDonor.blood_type.in_(compatible_types),
                BloodDonor.consent_to_contact.is_(True),
                BloodDonor.deferral_status != DonorDeferralStatus.PERMANENT_DEFERRAL.value,
            )
            .order_by(BloodDonor.last_donated_at.asc(), BloodDonor.id.asc())
        ).scalars().all()
    )

    eligible_matches = []
    for d in donors:
        ok, _ = _donor_is_eligible_now(d, now)
        if ok:
            # Strict PHI isolation: never include patient identity/order diagnosis.
            eligible_matches.append({
                "donor_id": d.id,
                "donor_code": d.donor_code,
                "full_name": d.full_name,
                "phone": d.phone,
                "blood_type": d.blood_type,
                "last_donated_at": d.last_donated_at,
                "total_donations": d.total_donations,
            })

    return {
        "blood_product_order_id": order.id if order else None,
        "product_type": resolved_product,
        "target_blood_type": resolved_blood,
        "compatible_donor_blood_types": compatible_types,
        "available_compatible_units": avail_count,
        "shortage_detected": shortage,
        "eligible_donors": eligible_matches,
    }


# ---------------------------------------------------------------------------
# Order Integration Helpers (Called inside BloodProductOrderService transactions)
# ---------------------------------------------------------------------------


def reserve_units_for_order(
    *,
    order: BloodProductOrder,
    actor: Staff,
    blood_unit_ids: list[int],
    recipient_blood_type: str,
    emergency_release: bool,
) -> list[BloodUnit]:
    if not blood_unit_ids:
        raise ValidationError("blood_unit_ids cannot be empty")
    unique_ids = list(dict.fromkeys(blood_unit_ids))
    if len(unique_ids) != len(blood_unit_ids):
        raise ValidationError("Duplicate blood_unit_ids are not allowed")
    if len(unique_ids) > order.units_requested:
        raise ValidationError("Cannot reserve more units than units_requested")

    now = _utcnow()
    units = list(
        db.session.execute(
            select(BloodUnit)
            .where(
                BloodUnit.clinic_id == order.clinic_id,
                BloodUnit.id.in_(unique_ids),
            )
            .order_by(BloodUnit.id.asc())
            .with_for_update()
        ).scalars().all()
    )
    if len(units) != len(unique_ids):
        raise NotFoundError("One or more blood units were not found in clinic")

    for unit in units:
        if unit.tti_screening_status != TtiScreeningStatus.CLEARED.value:
            raise ConflictError(
                f"Blood unit {unit.unit_barcode} has not passed TTI screening"
            )
        if _as_utc_naive(unit.expires_at) <= now:
            unit.status = BloodUnitStatus.EXPIRED.value
            unit.version += 1
            raise ConflictError(f"Blood unit {unit.unit_barcode} is expired")
        if unit.status != BloodUnitStatus.AVAILABLE.value:
            raise ConflictError(
                f"Blood unit {unit.unit_barcode} is not available (status={unit.status})"
            )
        if unit.product_type != order.product_type:
            raise ValidationError(
                f"Blood unit {unit.unit_barcode} product_type ({unit.product_type}) "
                f"does not match order ({order.product_type})"
            )
        if not is_blood_unit_compatible(
            unit_blood_type=unit.blood_type,
            recipient_blood_type=recipient_blood_type,
            product_type=order.product_type,
            emergency_release=emergency_release,
        ):
            raise ConflictError(
                f"Blood unit {unit.unit_barcode} ({unit.blood_type}) is incompatible "
                f"with patient blood type ({recipient_blood_type})"
            )

    for unit in units:
        unit.status = BloodUnitStatus.RESERVED.value
        unit.reserved_for_order_id = order.id
        unit.reserved_by_staff_id = actor.id
        unit.reserved_at = now
        unit.version += 1
        _audit(
            actor=actor,
            action=AuditAction.STATUS_CHANGE,
            entity_type="BloodUnit",
            entity_id=unit.id,
            description="Reserved blood unit for order",
            old_value={"status": BloodUnitStatus.AVAILABLE.value},
            new_value={
                "status": unit.status,
                "reserved_for_order_id": order.id,
                "version": unit.version,
            },
        )
    db.session.flush()
    return units


def issue_reserved_units_for_order(
    *,
    order: BloodProductOrder,
    actor: Staff,
    blood_unit_ids: list[int] | None = None,
) -> list[BloodUnit]:
    reserved = list(
        db.session.execute(
            select(BloodUnit)
            .where(
                BloodUnit.clinic_id == order.clinic_id,
                BloodUnit.reserved_for_order_id == order.id,
                BloodUnit.status == BloodUnitStatus.RESERVED.value,
            )
            .order_by(BloodUnit.id.asc())
            .with_for_update()
        ).scalars().all()
    )
    if not reserved and not blood_unit_ids:
        return []

    if blood_unit_ids is not None:
        unique_ids = list(dict.fromkeys(blood_unit_ids))
        if len(unique_ids) != len(blood_unit_ids):
            raise ValidationError("Duplicate blood_unit_ids are not allowed")
        reserved_by_id = {u.id: u for u in reserved}
        missing = [uid for uid in unique_ids if uid not in reserved_by_id]
        if missing:
            raise ConflictError(
                f"Blood units {missing} are not reserved for order {order.id}"
            )
        selected = [reserved_by_id[uid] for uid in unique_ids]
        unselected = [u for u in reserved if u.id not in set(unique_ids)]
    else:
        selected = reserved
        unselected = []

    now = _utcnow()
    for unit in selected:
        if unit.tti_screening_status != TtiScreeningStatus.CLEARED.value:
            raise ConflictError(
                f"Blood unit {unit.unit_barcode} has not passed TTI screening"
            )
        if _as_utc_naive(unit.expires_at) <= now:
            unit.status = BloodUnitStatus.EXPIRED.value
            unit.reserved_for_order_id = None
            unit.version += 1
            raise ConflictError(f"Blood unit {unit.unit_barcode} is expired")
        unit.status = BloodUnitStatus.ISSUED.value
        unit.issued_to_order_id = order.id
        unit.issued_by_staff_id = actor.id
        unit.issued_at = now
        unit.reserved_for_order_id = None
        unit.version += 1
        _audit(
            actor=actor,
            action=AuditAction.STATUS_CHANGE,
            entity_type="BloodUnit",
            entity_id=unit.id,
            description="Issued blood unit for order",
            old_value={"status": BloodUnitStatus.RESERVED.value},
            new_value={
                "status": unit.status,
                "issued_to_order_id": order.id,
                "version": unit.version,
            },
        )

    for unit in unselected:
        unit.status = BloodUnitStatus.AVAILABLE.value
        unit.reserved_for_order_id = None
        unit.reserved_by_staff_id = None
        unit.reserved_at = None
        unit.version += 1

    db.session.flush()
    return selected


def release_reserved_units_for_order(
    *,
    order: BloodProductOrder,
    actor: Staff,
) -> list[BloodUnit]:
    reserved = list(
        db.session.execute(
            select(BloodUnit)
            .where(
                BloodUnit.clinic_id == order.clinic_id,
                BloodUnit.reserved_for_order_id == order.id,
                BloodUnit.status == BloodUnitStatus.RESERVED.value,
            )
            .with_for_update()
        ).scalars().all()
    )
    now = _utcnow()
    for unit in reserved:
        unit.reserved_for_order_id = None
        unit.reserved_by_staff_id = None
        unit.reserved_at = None
        unit.status = (
            BloodUnitStatus.EXPIRED.value
            if _as_utc_naive(unit.expires_at) <= now
            else BloodUnitStatus.AVAILABLE.value
        )
        unit.version += 1
        _audit(
            actor=actor,
            action=AuditAction.STATUS_CHANGE,
            entity_type="BloodUnit",
            entity_id=unit.id,
            description="Released reserved blood unit on order cancellation",
            old_value={"status": BloodUnitStatus.RESERVED.value},
            new_value={"status": unit.status, "version": unit.version},
        )
    db.session.flush()
    return reserved
