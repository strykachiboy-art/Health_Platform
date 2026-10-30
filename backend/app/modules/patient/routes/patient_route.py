from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.core.emergency_access.services.emergency_access_service import assert_emergency_access
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    DomainError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import active_staff_required, get_current_clinic_id, role_required

from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff
from app.modules.patient.schemas.patient_allergy_schema import (
    PatientAllergyCreateSchema,
    PatientAllergyListQuerySchema,
    PatientAllergyListResponseSchema,
    PatientAllergyResponseSchema,
    PatientAllergyStatusTransitionSchema,
    PatientAllergyUpdateSchema,
)

from app.modules.patient.schemas.patient_schema import (
    PatientCreateSchema,
    PatientFamilyMemberCreateSchema,
    PatientFamilyMemberListQuerySchema,
    PatientFamilyMemberListResponseSchema,
    PatientFamilyMemberResponseSchema,
    PatientFamilyMemberUpdateSchema,
    PatientInsuranceCreateSchema,
    PatientInsuranceListQuerySchema,
    PatientInsuranceListResponseSchema,
    PatientInsuranceResponseSchema,
    PatientInsuranceUpdateSchema,
    PatientListQuerySchema,
    PatientListResponseSchema,
    PatientResponseSchema,
    PatientStatusUpdateSchema,
    PatientUpdateSchema,
    PatientVitalsCreateSchema,
    PatientVitalsListQuerySchema,
    PatientVitalsListResponseSchema,
    PatientVitalsResponseSchema,
)

from app.modules.patient.services.patient_service import (
    add_family_member,
    create_patient_allergy,
    get_patient_allergy,
    list_patient_allergies,
    add_insurance,
    create_patient,
    get_latest_vitals,
    get_patient,
    get_vitals_history,
    list_family_members,
    list_insurances,
    list_patients,
    record_vitals,
    remove_family_member,
    set_active_status,
    update_family_member,
    update_insurance,
    update_patient,
    update_patient_allergy,
    transition_patient_allergy_status,
)


patient_bp = Blueprint(
    "patients",
    __name__,
    url_prefix="/patients",
)


def _get_current_user() -> User:
    identity = get_jwt_identity()

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        raise ValidationError(
            "Invalid authentication identity"
        )

    if user_id <= 0:
        raise ValidationError(
            "Invalid authentication identity"
        )

    user = db.session.get(
        User,
        user_id,
    )

    if user is None:
        raise NotFoundError(
            "Authenticated user could not be resolved"
        )

    if not user.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    return user


def _get_current_clinic_id() -> int:
    return get_current_clinic_id(required=True)


def _validate_positive_id(
    value: int,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    return value


def _get_patient_in_current_clinic(
    patient_id: int,
) -> Patient:
    patient_id = _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    clinic_id = _get_current_clinic_id()

    patient = db.session.get(
        Patient,
        patient_id,
    )

    if patient is None:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    if patient.clinic_id != clinic_id:
        raise ValidationError(
            "Patient does not belong to the authenticated user's clinic"
        )

    return patient


def _get_current_staff() -> Staff:
    user = _get_current_user()
    clinic_id = _get_current_clinic_id()

    staff = db.session.execute(
        select(Staff)
        .where(
            Staff.user_id == user.id,
            Staff.clinic_id == clinic_id,
        )
        .limit(1)
    ).scalar_one_or_none()

    if staff is None:
        raise ValidationError(
            "Authenticated user is not linked to a staff record"
        )

    return staff


def _validate_payload(schema_class):
    payload = request.get_json(
        silent=True
    ) or {}

    return schema_class.model_validate(
        payload
    )


def _validate_query(schema_class):
    payload = request.args.to_dict()

    return schema_class.model_validate(
        payload
    )


def _validation_error_response(
    exc: PydanticValidationError,
):
    return jsonify(
        {
            "success": False,
            "error": "Validation failed",
            "details": exc.errors(),
        }
    ), 422


def _domain_error_response(
    exc: DomainError,
):
    return jsonify(
        {
            "success": False,
            "error": str(exc),
        }
    ), exc.status_code


def _serialize_patient(
    patient: Patient,
) -> dict:
    return (
        PatientResponseSchema
        .model_validate(patient)
        .model_dump(mode="json")
    )


def _serialize_family_member(
    member,
) -> dict:
    return (
        PatientFamilyMemberResponseSchema
        .model_validate(member)
        .model_dump(mode="json")
    )


def _serialize_insurance(
    insurance,
) -> dict:
    return (
        PatientInsuranceResponseSchema
        .model_validate(insurance)
        .model_dump(mode="json")
    )


def _serialize_vitals(
    vitals,
) -> dict:
    return (
        PatientVitalsResponseSchema
        .model_validate(vitals)
        .model_dump(mode="json")
    )


def _serialize_allergy(
    allergy,
) -> dict:
    return (
        PatientAllergyResponseSchema
        .model_validate(
            allergy
        )
        .model_dump(
            mode="json"
        )
    )


def _serialize_allergy_page(
    page,
) -> dict:
    return (
        PatientAllergyListResponseSchema
        .model_validate(
            {
                "items": page.items,
                "total": page.total,
                "page": page.page,
                "per_page": page.per_page,
            }
        )
        .model_dump(
            mode="json"
        )
    )

def _serialize_patient_page(page):
    return (
        PatientListResponseSchema
        .model_validate(
            {
                "items": page.items,
                "total": page.total,
                "page": page.page,
                "per_page": page.per_page,
            }
        )
        .model_dump(mode="json")
    )


def _serialize_family_page(page):
    return (
        PatientFamilyMemberListResponseSchema
        .model_validate(
            {
                "items": page.items,
                "total": page.total,
                "page": page.page,
                "per_page": page.per_page,
            }
        )
        .model_dump(mode="json")
    )


def _serialize_insurance_page(page):
    return (
        PatientInsuranceListResponseSchema
        .model_validate(
            {
                "items": page.items,
                "total": page.total,
                "page": page.page,
                "per_page": page.per_page,
            }
        )
        .model_dump(mode="json")
    )


def _serialize_vitals_page(page):
    return (
        PatientVitalsListResponseSchema
        .model_validate(
            {
                "items": page.items,
                "total": page.total,
                "page": page.page,
                "per_page": page.per_page,
            }
        )
        .model_dump(mode="json")
    )


@patient_bp.post("")
@role_required(
    Role.ADMIN,
    Role.RECEPTIONIST,
)
@active_staff_required
def create_patient_route():
    try:
        data = _validate_payload(
            PatientCreateSchema
        )

        user = _get_current_user()

        patient = create_patient(
            clinic_id=_get_current_clinic_id(),
            data=data.model_dump(
                exclude_unset=True
            ),
            actor_id=user.id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_patient(patient),
            }
        ), 201

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.get("")
@role_required(
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.RECEPTIONIST,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.PARAMEDIC,
    Role.EMT,
)
@active_staff_required
def list_patients_route():
    try:
        query = _validate_query(
            PatientListQuerySchema
        )

        clinic_id = _get_current_clinic_id()

        page = list_patients(
            clinic_id=clinic_id,
            active_only=query.active_only,
            search=query.search,
            page=query.page,
            per_page=query.per_page,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_patient_page(page),
            }
        ), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.get("/<int:patient_id>")
@role_required(
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.RECEPTIONIST,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.PARAMEDIC,
    Role.EMT,
)
@active_staff_required
def get_patient_route(
    patient_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        emergency_access_id_header = request.headers.get(
            "X-Emergency-Access-ID"
        )

        if emergency_access_id_header is not None:
            try:
                emergency_access_id = int(
                    emergency_access_id_header.strip()
                )
            except (TypeError, ValueError):
                return jsonify({
                    "success": False,
                    "error": "Emergency access is not authorized for this resource",
                }), 403

            if emergency_access_id <= 0:
                return jsonify({
                    "success": False,
                    "error": "Emergency access is not authorized for this resource",
                }), 403

            allowed = assert_emergency_access(
                actor_id=_get_current_user().id,
                patient_id=patient.id,
                resource_type="patient",
                resource_id=patient.id,
                action="read",
                emergency_access_id=emergency_access_id,
            )

            if not allowed:
                return jsonify({
                    "success": False,
                    "error": "Emergency access is not authorized for this resource",
                }), 403

        patient = get_patient(
            patient.id
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_patient(patient),
            }
        ), 200

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.patch("/<int:patient_id>")
@role_required(
    Role.ADMIN,
    Role.RECEPTIONIST,
)
@active_staff_required
def update_patient_route(
    patient_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        data = _validate_payload(
            PatientUpdateSchema
        )

        user = _get_current_user()

        patient = update_patient(
            patient_id=patient.id,
            data=data.model_dump(
                exclude_unset=True
            ),
            actor_id=user.id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_patient(patient),
            }
        ), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.patch("/<int:patient_id>/status")
@role_required(
    Role.ADMIN,
    Role.RECEPTIONIST,
)
@active_staff_required
def set_patient_status_route(
    patient_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        data = _validate_payload(
            PatientStatusUpdateSchema
        )

        user = _get_current_user()

        patient = set_active_status(
            patient_id=patient.id,
            is_active=data.is_active,
            actor_id=user.id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_patient(patient),
            }
        ), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.get("/<int:patient_id>/family")
@role_required(
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.RECEPTIONIST,
)
@active_staff_required
def list_family_members_route(
    patient_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        query = _validate_query(
            PatientFamilyMemberListQuerySchema
        )

        page = list_family_members(
            patient_id=patient.id,
            page=query.page,
            per_page=query.per_page,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_family_page(page),
            }
        ), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.post("/<int:patient_id>/family")
@role_required(
    Role.ADMIN,
    Role.RECEPTIONIST,
)
@active_staff_required
def add_family_member_route(
    patient_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        data = _validate_payload(
            PatientFamilyMemberCreateSchema
        )

        user = _get_current_user()

        member = add_family_member(
            patient_id=patient.id,
            data=data.model_dump(
                exclude_unset=True
            ),
            actor_id=user.id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_family_member(member),
            }
        ), 201

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.patch(
    "/<int:patient_id>/family/<int:family_member_id>"
)
@role_required(
    Role.ADMIN,
    Role.RECEPTIONIST,
)
@active_staff_required
def update_family_member_route(
    patient_id: int,
    family_member_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        family_member_id = _validate_positive_id(
            family_member_id,
            "Family member ID",
        )

        data = _validate_payload(
            PatientFamilyMemberUpdateSchema
        )

        user = _get_current_user()

        member = update_family_member(
            patient_id=patient.id,
            family_member_id=family_member_id,
            data=data.model_dump(
                exclude_unset=True
            ),
            actor_id=user.id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_family_member(member),
            }
        ), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.delete(
    "/<int:patient_id>/family/<int:family_member_id>"
)
@role_required(
    Role.ADMIN,
    Role.RECEPTIONIST,
)
@active_staff_required
def remove_family_member_route(
    patient_id: int,
    family_member_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        family_member_id = _validate_positive_id(
            family_member_id,
            "Family member ID",
        )

        user = _get_current_user()

        remove_family_member(
            patient_id=patient.id,
            family_member_id=family_member_id,
            actor_id=user.id,
        )

        return jsonify(
            {
                "success": True,
                "message": "Family member removed successfully",
            }
        ), 200

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.get("/<int:patient_id>/insurance")
@role_required(
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.RECEPTIONIST,
    Role.ACCOUNTANT,
)
@active_staff_required
def list_insurances_route(
    patient_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        query = _validate_query(
            PatientInsuranceListQuerySchema
        )

        page = list_insurances(
            patient_id=patient.id,
            page=query.page,
            per_page=query.per_page,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_insurance_page(page),
            }
        ), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.post("/<int:patient_id>/insurance")
@role_required(
    Role.ADMIN,
    Role.RECEPTIONIST,
)
@active_staff_required
def add_insurance_route(
    patient_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        data = _validate_payload(
            PatientInsuranceCreateSchema
        )

        user = _get_current_user()

        insurance = add_insurance(
            patient_id=patient.id,
            data=data.model_dump(
                exclude_unset=True
            ),
            actor_id=user.id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_insurance(insurance),
            }
        ), 201

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.patch(
    "/<int:patient_id>/insurance/<int:insurance_id>"
)
@role_required(
    Role.ADMIN,
    Role.RECEPTIONIST,
)
@active_staff_required
def update_insurance_route(
    patient_id: int,
    insurance_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        insurance_id = _validate_positive_id(
            insurance_id,
            "Insurance ID",
        )

        data = _validate_payload(
            PatientInsuranceUpdateSchema
        )

        user = _get_current_user()

        insurance = update_insurance(
            patient_id=patient.id,
            insurance_id=insurance_id,
            data=data.model_dump(
                exclude_unset=True
            ),
            actor_id=user.id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_insurance(insurance),
            }
        ), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.get("/<int:patient_id>/vitals")
@role_required(
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
)
@active_staff_required
def get_vitals_history_route(
    patient_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        query = _validate_query(
            PatientVitalsListQuerySchema
        )

        page = get_vitals_history(
            patient_id=patient.id,
            page=query.page,
            per_page=query.per_page,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_vitals_page(page),
            }
        ), 200

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.get("/<int:patient_id>/vitals/latest")
@role_required(
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
)
@active_staff_required
def get_latest_vitals_route(
    patient_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        vitals = get_latest_vitals(
            patient.id
        )

        if vitals is None:
            return jsonify(
                {
                    "success": True,
                    "data": None,
                }
            ), 200

        return jsonify(
            {
                "success": True,
                "data": _serialize_vitals(vitals),
            }
        ), 200

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.post("/<int:patient_id>/vitals")
@role_required(
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
)
@active_staff_required
def record_vitals_route(
    patient_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        data = _validate_payload(
            PatientVitalsCreateSchema
        )

        payload = data.model_dump(
            exclude_unset=True
        )

        consultation_id = payload.pop(
            "consultation_id",
            None,
        )

        staff = _get_current_staff()

        vitals = record_vitals(
            patient_id=patient.id,
            data=payload,
            consultation_id=consultation_id,
            recorded_by_id=staff.id,
            actor_id=staff.user_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_vitals(vitals),
            }
        ), 201

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)


@patient_bp.get(
    "/<int:patient_id>/allergies"
)
@role_required(
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.RECEPTIONIST,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.PARAMEDIC,
    Role.EMT,
)
@active_staff_required
def list_patient_allergies_route(
    patient_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        query = _validate_query(
            PatientAllergyListQuerySchema
        )

        page = list_patient_allergies(
            clinic_id=_get_current_clinic_id(),
            patient_id=patient.id,
            active_only=query.active_only,
            include_refuted=query.include_refuted,
            page=query.page,
            per_page=query.per_page,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_allergy_page(
                    page
                ),
            }
        ), 200

    except PydanticValidationError as exc:
        return _validation_error_response(
            exc
        )

    except DomainError as exc:
        return _domain_error_response(
            exc
        )


@patient_bp.get(
    "/<int:patient_id>/allergies/"
    "<int:allergy_id>"
)
@role_required(
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.RECEPTIONIST,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.PARAMEDIC,
    Role.EMT,
)
@active_staff_required
def get_patient_allergy_route(
    patient_id: int,
    allergy_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        allergy = get_patient_allergy(
            clinic_id=_get_current_clinic_id(),
            patient_id=patient.id,
            allergy_id=allergy_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_allergy(
                    allergy
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error_response(
            exc
        )


@patient_bp.post(
    "/<int:patient_id>/allergies"
)
@role_required(
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.PHARMACIST,
)
@active_staff_required
def create_patient_allergy_route(
    patient_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        data = _validate_payload(
            PatientAllergyCreateSchema
        )

        user = _get_current_user()
        staff = _get_current_staff()

        allergy = create_patient_allergy(
            clinic_id=_get_current_clinic_id(),
            patient_id=patient.id,
            data=data.model_dump(
                exclude_unset=True
            ),
            recorded_by_id=staff.id,
            actor_id=user.id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_allergy(
                    allergy
                ),
            }
        ), 201

    except PydanticValidationError as exc:
        return _validation_error_response(
            exc
        )

    except DomainError as exc:
        return _domain_error_response(
            exc
        )


@patient_bp.patch(
    "/<int:patient_id>/allergies/"
    "<int:allergy_id>"
)
@role_required(
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.PHARMACIST,
)
@active_staff_required
def update_patient_allergy_route(
    patient_id: int,
    allergy_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        data = _validate_payload(
            PatientAllergyUpdateSchema
        )

        user = _get_current_user()

        allergy = update_patient_allergy(
            clinic_id=_get_current_clinic_id(),
            patient_id=patient.id,
            allergy_id=allergy_id,
            data=data.model_dump(
                exclude_unset=True
            ),
            actor_id=user.id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_allergy(
                    allergy
                ),
            }
        ), 200

    except PydanticValidationError as exc:
        return _validation_error_response(
            exc
        )

    except DomainError as exc:
        return _domain_error_response(
            exc
        )


@patient_bp.patch(
    "/<int:patient_id>/allergies/"
    "<int:allergy_id>/status"
)
@role_required(
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.PHARMACIST,
)
@active_staff_required
def transition_patient_allergy_status_route(
    patient_id: int,
    allergy_id: int,
):
    try:
        patient = _get_patient_in_current_clinic(
            patient_id
        )

        data = _validate_payload(
            PatientAllergyStatusTransitionSchema
        )

        user = _get_current_user()

        allergy = transition_patient_allergy_status(
            clinic_id=_get_current_clinic_id(),
            patient_id=patient.id,
            allergy_id=allergy_id,
            clinical_status=data.clinical_status,
            verification_status=(
                data.verification_status
            ),
            reason=data.reason,
            actor_id=user.id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_allergy(
                    allergy
                ),
            }
        ), 200

    except PydanticValidationError as exc:
        return _validation_error_response(
            exc
        )

    except DomainError as exc:
        return _domain_error_response(
            exc
        )
