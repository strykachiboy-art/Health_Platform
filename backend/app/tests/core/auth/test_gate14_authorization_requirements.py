from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.modules.emar.routes import emar_routes
from app.modules.emar.services import emar_service
from app.modules.lab.routes import lab_route
from app.modules.lab.services import lab_service
from app.modules.nursing.routes import nursing_route
from app.modules.pharmacy.routes import pharmacy_routes
from app.modules.pharmacy.services import pharmacy_service
from app.modules.prescription.routes import prescription_routes
from app.modules.prescription.services import prescription_service
from app.modules.ward.routes import ward_route


def test_gate14_role_contracts_are_explicit():
    assert prescription_routes.PRESCRIPTION_WRITE_ROLES == (
        Role.DOCTOR,
    )
    assert emar_routes.EMAR_WRITE_ROLES == (
        Role.NURSE,
    )
    assert set(emar_routes.EMAR_VIEW_ROLES) == {
        Role.DOCTOR,
        Role.NURSE,
    }
    assert pharmacy_routes.PHARMACY_OPERATION_ROLES == (
        Role.PHARMACIST,
    )
    assert lab_route.LAB_CLINICAL_ROLES == (
        Role.DOCTOR,
        Role.NURSE,
    )
    assert lab_route.LAB_TECHNICIAN_ROLES == (
        Role.LAB_TECHNICIAN,
    )
    assert nursing_route.NURSING_WRITE_ROLES == (
        Role.NURSE,
    )
    assert ward_route.DISCHARGE_ROLES == (
        Role.DOCTOR,
    )


def test_super_admin_is_not_an_ordinary_clinical_role():
    for roles in (
        prescription_routes.PRESCRIPTION_WRITE_ROLES,
        emar_routes.EMAR_WRITE_ROLES,
        pharmacy_routes.PHARMACY_OPERATION_ROLES,
        lab_route.LAB_CLINICAL_ROLES,
        lab_route.LAB_TECHNICIAN_ROLES,
        nursing_route.NURSING_WRITE_ROLES,
        ward_route.DISCHARGE_ROLES,
    ):
        assert Role.SUPER_ADMIN not in roles


def test_emar_service_is_nurse_only(
    clinic,
    make_staff,
):
    nurse = make_staff(
        clinic,
        role=Role.NURSE,
    )
    doctor = make_staff(
        clinic,
        role=Role.DOCTOR,
    )

    assert (
        emar_service._get_authorized_actor(
            nurse.id,
            clinic_id=clinic.id,
        ).id
        == nurse.id
    )

    with pytest.raises(
        ValidationError,
        match="Medication administration actor",
    ):
        emar_service._get_authorized_actor(
            doctor.id,
            clinic_id=clinic.id,
        )


def test_pharmacy_service_rejects_admin_dispensing(
    clinic,
    make_staff,
):
    pharmacist = make_staff(
        clinic,
        role=Role.PHARMACIST,
    )
    admin = make_staff(
        clinic,
        role=Role.ADMIN,
    )

    assert (
        pharmacy_service._validate_staff_for_pharmacy(
            pharmacist.id,
            clinic.id,
        ).id
        == pharmacist.id
    )

    with pytest.raises(
        ValidationError,
        match="not authorized for pharmacy",
    ):
        pharmacy_service._validate_staff_for_pharmacy(
            admin.id,
            clinic.id,
        )


def test_lab_operational_service_requires_lab_technician(
    clinic,
    make_staff,
):
    order = SimpleNamespace(
        clinic_id=clinic.id,
    )
    technician = make_staff(
        clinic,
        role=Role.LAB_TECHNICIAN,
    )
    admin = make_staff(
        clinic,
        role=Role.ADMIN,
    )

    assert (
        lab_service._validate_actor_for_order(
            technician.id,
            order,
        ).id
        == technician.id
    )

    with pytest.raises(
        ValidationError,
        match="authorized lab role",
    ):
        lab_service._validate_actor_for_order(
            admin.id,
            order,
        )


def test_lab_order_cancellation_preserves_clinical_roles(
    clinic,
    make_staff,
):
    order = SimpleNamespace(
        clinic_id=clinic.id,
    )
    doctor = make_staff(
        clinic,
        role=Role.DOCTOR,
    )
    technician = make_staff(
        clinic,
        role=Role.LAB_TECHNICIAN,
    )

    assert (
        lab_service._validate_actor_for_order(
            doctor.id,
            order,
            allowed_roles=(
                Role.DOCTOR,
                Role.NURSE,
            ),
        ).id
        == doctor.id
    )

    with pytest.raises(
        ValidationError,
        match="authorized lab role",
    ):
        lab_service._validate_actor_for_order(
            technician.id,
            order,
            allowed_roles=(
                Role.DOCTOR,
                Role.NURSE,
            ),
        )


def test_prescription_service_remains_doctor_only(
    clinic,
    make_staff,
):
    doctor = make_staff(
        clinic,
        role=Role.DOCTOR,
    )
    pharmacist = make_staff(
        clinic,
        role=Role.PHARMACIST,
    )

    assert (
        prescription_service._validate_prescriber(
            doctor.id,
            clinic.id,
        ).id
        == doctor.id
    )

    with pytest.raises(
        ValidationError,
        match="Only doctors can prescribe",
    ):
        prescription_service._validate_prescriber(
            pharmacist.id,
            clinic.id,
        )


def test_cross_tenant_patient_scope_fails_closed(
    clinic,
    make_clinic,
    make_patient,
):
    other_clinic = make_clinic()
    other_patient = make_patient(
        other_clinic
    )

    with pytest.raises(
        ValidationError,
        match="does not belong",
    ):
        prescription_service._validate_patient_for_clinic(
            other_patient.id,
            clinic.id,
        )

    with pytest.raises(
        ValidationError,
        match="does not belong",
    ):
        emar_service._get_patient(
            other_patient.id,
            clinic.id,
        )


def test_unauthorized_roles_are_rejected_at_routes(
    client,
    clinic,
    make_staff,
    auth_headers_for,
):
    pharmacist = make_staff(
        clinic,
        role=Role.PHARMACIST,
    )
    doctor = make_staff(
        clinic,
        role=Role.DOCTOR,
    )
    admin = make_staff(
        clinic,
        role=Role.ADMIN,
    )
    nurse = make_staff(
        clinic,
        role=Role.NURSE,
    )

    probes = (
        (
            "/api/v1/prescriptions",
            pharmacist.user,
            Role.PHARMACIST,
        ),
        (
            "/api/v1/emar/scheduled",
            doctor.user,
            Role.DOCTOR,
        ),
        (
            "/api/v1/pharmacy/dispense",
            admin.user,
            Role.ADMIN,
        ),
        (
            "/api/v1/lab/orders/1/process",
            admin.user,
            Role.ADMIN,
        ),
        (
            "/api/v1/nursing/admissions/1/care-plans",
            doctor.user,
            Role.DOCTOR,
        ),
        (
            "/api/v1/wards/admissions/1/discharge",
            nurse.user,
            Role.NURSE,
        ),
    )

    for url, user, role in probes:
        response = client.post(
            url,
            headers=auth_headers_for(
                user,
                role=role,
            ),
            json={},
        )
        assert response.status_code == 403, (
            url,
            response.status_code,
            response.get_json(),
        )


def test_super_admin_cannot_magically_bypass_clinical_routes(
    client,
    make_user,
    auth_headers_for,
):
    super_admin = make_user(
        None,
        role=Role.SUPER_ADMIN,
        email="gate14-super-admin@test.com",
    )
    headers = auth_headers_for(
        super_admin,
        role=Role.SUPER_ADMIN,
    )

    for url in (
        "/api/v1/prescriptions",
        "/api/v1/emar/scheduled",
        "/api/v1/pharmacy/dispense",
        "/api/v1/lab/orders/1/process",
        "/api/v1/nursing/admissions/1/care-plans",
        "/api/v1/wards/admissions/1/discharge",
    ):
        response = client.post(
            url,
            headers=headers,
            json={},
        )
        assert response.status_code == 403, (
            url,
            response.status_code,
            response.get_json(),
        )
