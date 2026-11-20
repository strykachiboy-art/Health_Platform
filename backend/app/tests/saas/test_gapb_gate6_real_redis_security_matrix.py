"""GAP-B Gate 6 R01: real Redis/JWT tenant isolation without production data.

This module deliberately DOES NOT replace the JWT blocklist callback or use
an in-memory revocation substitute. The runner controls a disposable Redis
container and tests healthy, unavailable and restored network states.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.core.auth.user.services.user_service import issue_auth_tokens
from app.core.enums.role_enums import Role
from app.extensions import db
from app.modules.clinic.models.organization_model import (
    ClinicMembership,
    HospitalOrganization,
    TenantMembership,
)


@pytest.fixture()
def tenant_world(make_clinic, make_staff, make_patient):
    alpha = HospitalOrganization(id=uuid4(), name="GAPB R01 Synthetic Alpha")
    beta = HospitalOrganization(id=uuid4(), name="GAPB R01 Synthetic Beta")
    db.session.add_all([alpha, beta])
    db.session.flush()

    clinic_a = make_clinic(name="GAPB R01 Alpha HQ", organization_id=alpha.id,
                           is_headquarters=True)
    clinic_a2 = make_clinic(name="GAPB R01 Alpha Branch", organization_id=alpha.id,
                            parent_clinic_id=clinic_a.id)
    clinic_b = make_clinic(name="GAPB R01 Beta HQ", organization_id=beta.id,
                           is_headquarters=True)
    staff = make_staff(clinic_a, role=Role.DOCTOR)
    actor = staff.user
    own = make_patient(clinic_a)
    other_same_org = make_patient(clinic_a2)
    other_tenant = make_patient(clinic_b)

    membership = TenantMembership(
        user_id=actor.id, organization_id=alpha.id, role="member",
        status="active", is_active=True,
    )
    db.session.add(membership)
    db.session.flush()
    db.session.add(ClinicMembership(
        user_id=actor.id, organization_id=alpha.id,
        clinic_id=clinic_a.id, role="doctor", is_active=True,
    ))
    db.session.commit()
    return {
        "actor": actor,
        "own": own,
        "other_same_org": other_same_org,
        "other_tenant": other_tenant,
        "membership": membership,
        "clinic": clinic_a,
    }


def _authorized_headers(actor):
    tokens = issue_auth_tokens(actor)
    assert tokens["tenant_id"] is not None
    assert tokens["clinic_context_id"] is not None
    return {"Authorization": "Bearer " + tokens["access_token"]}


def test_real_redis_bound_access_health_matrix(client, tenant_world):
    """The same authorization path must fail closed during Redis outage."""
    phase = os.environ.get("GAPB_R01_REDIS_PHASE")
    assert phase in {"healthy", "down", "restored"}, "Runner phase required"
    w = tenant_world
    headers = _authorized_headers(w["actor"])
    response = client.get("/api/v1/patients?page=1&per_page=50", headers=headers)
    if phase == "down":
        assert response.status_code == 401
        assert response.get_json().get("success") is not True
        return

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    items = body["data"]["items"]
    ids = {item["id"] for item in items}
    assert w["own"].id in ids
    assert w["other_same_org"].id not in ids
    assert w["other_tenant"].id not in ids


def test_real_redis_revoked_tenant_membership_blocks_old_jwt(
    client, tenant_world,
):
    assert os.environ.get("GAPB_R01_REDIS_PHASE") in {"healthy", "restored"}
    w = tenant_world
    headers = _authorized_headers(w["actor"])
    assert client.get("/api/v1/patients", headers=headers).status_code == 200
    w["membership"].is_active = False
    w["membership"].status = "revoked"
    db.session.commit()
    response = client.get("/api/v1/patients", headers=headers)
    assert response.status_code == 401


def test_real_redis_token_version_rotation_revokes_old_jwt(
    client, tenant_world,
):
    assert os.environ.get("GAPB_R01_REDIS_PHASE") in {"healthy", "restored"}
    w = tenant_world
    headers = _authorized_headers(w["actor"])
    assert client.get("/api/v1/patients", headers=headers).status_code == 200
    w["actor"].token_version += 1
    db.session.commit()
    response = client.get("/api/v1/patients", headers=headers)
    assert response.status_code == 401
