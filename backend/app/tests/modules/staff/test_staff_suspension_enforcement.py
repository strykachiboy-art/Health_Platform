from __future__ import annotations

import ast
from pathlib import Path

import pytest
from flask_jwt_extended import decode_token

from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.modules.staff.services.staff_service import change_staff_status

REPO_ROOT = Path(__file__).resolve().parents[5]


CLINICAL_READ_PATHS = (
    ("/api/v1/patients/{patient_id}", Role.DOCTOR),
    ("/api/v1/consultations/patient/{patient_id}", Role.DOCTOR),
    ("/api/v1/appointments/patient/{patient_id}", Role.DOCTOR),
    ("/api/v1/lab/tests", Role.DOCTOR),
    ("/api/v1/prescriptions/patients/{patient_id}", Role.DOCTOR),
    ("/api/v1/pharmacy/drugs", Role.PHARMACIST),
    ("/api/v1/wards", Role.DOCTOR),
    ("/api/v1/ambulance/trips?page=1&per_page=50", Role.PARAMEDIC),
)


@pytest.mark.parametrize(
    ("method", "path_template", "json_body", "role"),
    [
        ("GET", "/api/v1/patients/{patient_id}", None, Role.DOCTOR),
        ("POST", "/api/v1/consultations/", {}, Role.DOCTOR),
        ("POST", "/api/v1/appointments/", {}, Role.DOCTOR),
        ("GET", "/api/v1/lab/tests", None, Role.DOCTOR),
        (
            "GET",
            "/api/v1/prescriptions/patients/{patient_id}",
            None,
            Role.DOCTOR,
        ),
        ("GET", "/api/v1/pharmacy/drugs", None, Role.PHARMACIST),
        ("GET", "/api/v1/wards", None, Role.DOCTOR),
        (
            "GET",
            "/api/v1/ambulance/trips?page=1&per_page=50",
            None,
            Role.PARAMEDIC,
        ),
    ],
)
@pytest.mark.parametrize(
    "inactive_status",
    [
        StaffStatus.SUSPENDED,
        StaffStatus.TERMINATED,
    ],
)
def test_inactive_staff_with_fresh_jwt_cannot_access_clinical_routes(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
    auth_headers_for,
    method,
    path_template,
    json_body,
    role,
    inactive_status,
):
    staff, _old_headers = make_authenticated_staff(
        clinic,
        role,
    )
    patient = make_patient(clinic)

    change_staff_status(
        staff_id=staff.id,
        clinic_id=clinic.id,
        new_status=inactive_status,
    )

    # Issue a NEW token after the status change: token-version revocation
    # alone cannot be the reason this request is denied.
    fresh_headers = auth_headers_for(
        staff.user,
        role=role,
    )
    path = path_template.format(patient_id=patient.id)

    response = client.open(
        path,
        method=method,
        headers=fresh_headers,
        json=json_body,
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == (
        "Staff account is suspended or terminated"
    )


@pytest.mark.parametrize(
    ("path_template", "role"),
    CLINICAL_READ_PATHS,
)
def test_active_staff_can_still_access_clinical_read_routes(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
    path_template,
    role,
):
    _staff, headers = make_authenticated_staff(
        clinic,
        role,
        status=StaffStatus.ACTIVE,
    )
    patient = make_patient(clinic)

    response = client.get(
        path_template.format(patient_id=patient.id),
        headers=headers,
    )

    assert response.status_code == 200


def test_user_without_linked_staff_record_keeps_existing_role_access(
    client,
    clinic,
    make_user,
    auth_headers_for,
    make_patient,
):
    user = make_user(
        clinic=clinic,
        role=Role.DOCTOR,
    )
    patient = make_patient(clinic)
    headers = auth_headers_for(
        user,
        role=Role.DOCTOR,
    )

    response = client.get(
        f"/api/v1/patients/{patient.id}",
        headers=headers,
    )

    assert response.status_code == 200


def test_status_change_invalidates_existing_jwt(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
):
    staff, old_headers = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )
    patient = make_patient(clinic)

    raw_token = old_headers["Authorization"].split(" ", 1)[1]
    old_claims = decode_token(raw_token)
    original_token_version = old_claims["token_version"]

    change_staff_status(
        staff_id=staff.id,
        clinic_id=clinic.id,
        new_status=StaffStatus.SUSPENDED,
    )

    assert staff.user.token_version == original_token_version + 1

    response = client.get(
        f"/api/v1/patients/{patient.id}",
        headers=old_headers,
    )

    assert response.status_code == 401


def _guard_contract_decorator_name(node):
    if isinstance(node, ast.Call):
        return _guard_contract_decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def test_clinical_route_domains_require_active_staff_guard():
    expected_counts = {
        "ambulance": 13,
        "lab": 14,
        "pharmacy": 15,
        "prescription": 7,
        "ward": 22,
    }
    methods = {"get", "post", "put", "patch", "delete", "route"}
    role_guards = {
        "role_required",
        "emergency_aware_role_required",
        "require_roles",
        "login_required",
    }
    missing = []

    for module, expected_count in expected_counts.items():
        route_root = (
            REPO_ROOT / "backend" / "app" / "modules"
            / module / "routes"
        )
        assert route_root.is_dir(), f"Missing route directory: {route_root}"

        count = 0
        for path in sorted(route_root.rglob("*.py")):
            source = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(source, filename=str(path))

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue

                names = {
                    _guard_contract_decorator_name(dec)
                    for dec in node.decorator_list
                }
                has_route = any(
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in methods
                    for dec in node.decorator_list
                )

                if has_route and names.intersection(role_guards):
                    count += 1
                    if "active_staff_required" not in names:
                        missing.append(f"{path}:{node.lineno}:{node.name}")

        assert count == expected_count, (
            f"{module} route count changed: expected {expected_count}, "
            f"observed {count}"
        )

    assert not missing, (
        "Role-protected clinical endpoints lack active_staff_required: "
        + ", ".join(missing)
    )
