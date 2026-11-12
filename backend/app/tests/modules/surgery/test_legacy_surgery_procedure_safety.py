"""CE1-S03B only: ensure legacy procedure mutation paths cannot execute Surgery.

The previous S01/S02/S03A and ProcedureOrder suites are intentionally NOT rerun.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError
from app.extensions import db
from app.modules.clinical_orders.services import procedure_order_service as procedure
from app.modules.surgery.services import surgical_authorization_service as authorization
from app.tests.modules.surgery.test_surgical_case_foundation import (
    _context, _order, _plan,
)
from app.tests.modules.surgery.test_surgical_authorization import (
    CODE, _ready, _authorized,
)


@pytest.fixture
def ctx(monkeypatch, clinic, make_patient, make_staff):
    result = _context(monkeypatch, clinic, make_patient, make_staff)
    result["clinic"] = clinic
    result["make_staff"] = make_staff
    monkeypatch.setattr(
        authorization, "create_audit_log", lambda **kw: result["events"].append(kw)
    )
    return result


def _surgical(ctx, **overrides):
    return _order(ctx["clinic"], ctx, procedure_code=CODE, **overrides)


def _non_surgical(ctx, **overrides):
    return _order(ctx["clinic"], ctx, procedure_type="bedside", **overrides)


def _headers(ctx, auth_headers_for):
    return auth_headers_for(ctx["doctor"].user, role=Role.DOCTOR)


def _complete(order, ctx):
    return procedure.complete_procedure_order(
        order.id, clinic_id=ctx["clinic"].id,
        actor_staff_id=ctx["doctor"].id,
        procedure_note="Test-only legacy completion attempt",
    )


def _verify(order, ctx):
    return procedure.verify_procedure_consent(
        order.id, clinic_id=ctx["clinic"].id,
        actor_staff_id=ctx["doctor"].id,
    )


def test_surgical_legacy_direct_consent_verification_is_blocked(ctx):
    order = _surgical(ctx)
    with pytest.raises(ConflictError, match="dedicated Surgery"):
        _verify(order, ctx)
    db.session.refresh(order)
    assert order.consent_verified_at is None
    assert order.consent_verified_by_staff_id is None
    assert order.version == 1


def test_surgical_legacy_direct_completion_is_blocked(ctx):
    order = _surgical(ctx)
    with pytest.raises(ConflictError, match="dedicated Surgery"):
        _complete(order, ctx)
    db.session.refresh(order)
    assert order.status == "active"
    assert order.completed_at is None
    assert order.version == 1


def test_surgical_without_required_consent_cannot_use_old_completion(ctx):
    order = _surgical(ctx, consent_required=False)
    with pytest.raises(ConflictError, match="dedicated Surgery"):
        _complete(order, ctx)
    db.session.refresh(order)
    assert order.status == "active"


def test_forged_legacy_consent_timestamp_does_not_enable_completion(ctx):
    order = _surgical(ctx)
    order.consent_verified_at = datetime.now(timezone.utc).replace(tzinfo=None)
    order.consent_verified_by_staff_id = ctx["doctor"].id
    db.session.commit()
    with pytest.raises(ConflictError, match="dedicated Surgery"):
        _complete(order, ctx)
    db.session.refresh(order)
    assert order.status == "active"
    assert order.completed_at is None


def test_surgical_legacy_consent_route_fails_closed(ctx, client, auth_headers_for):
    order = _surgical(ctx)
    response = client.post(
        f"/api/v1/clinical-orders/procedures/{order.id}/verify-consent",
        headers=_headers(ctx, auth_headers_for),
    )
    assert response.status_code == 409
    db.session.refresh(order)
    assert order.consent_verified_at is None
    assert order.version == 1


def test_surgical_legacy_complete_route_fails_closed(ctx, client, auth_headers_for):
    order = _surgical(ctx)
    response = client.post(
        f"/api/v1/clinical-orders/procedures/{order.id}/complete",
        headers=_headers(ctx, auth_headers_for),
        json={"procedure_note": "A legacy note must not finish surgery"},
    )
    assert response.status_code == 409
    db.session.refresh(order)
    assert order.status == "active"
    assert order.completed_at is None


def test_surgical_case_cannot_be_orphaned_by_legacy_cancel(
    ctx, client, auth_headers_for
):
    order = _surgical(ctx)
    case = _plan(ctx["clinic"], ctx, order)
    with pytest.raises(ConflictError, match="dedicated Surgery"):
        procedure.cancel_procedure_order(
            order.id, clinic_id=ctx["clinic"].id,
            actor_staff_id=ctx["doctor"].id, reason="Bypass attempt",
        )
    response = client.post(
        f"/api/v1/clinical-orders/procedures/{order.id}/cancel",
        headers=_headers(ctx, auth_headers_for),
        json={"reason": "Cannot bypass Surgery case"},
    )
    assert response.status_code == 409
    db.session.refresh(order)
    assert order.status == "active"
    assert case.status == "planned"


def test_surgical_order_without_case_can_still_be_cancelled(ctx):
    order = _surgical(ctx)
    cancelled = procedure.cancel_procedure_order(
        order.id, clinic_id=ctx["clinic"].id,
        actor_staff_id=ctx["doctor"].id,
        reason="Before a surgical case exists",
    )
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at is not None


def test_non_surgical_consent_and_completion_service_preserved(ctx):
    order = _non_surgical(ctx)
    verified = _verify(order, ctx)
    assert verified.consent_verified_at is not None
    completed = _complete(order, ctx)
    assert completed.status == "completed"
    assert completed.completed_at is not None


def test_non_surgical_consent_and_completion_http_preserved(
    ctx, client, auth_headers_for
):
    order = _non_surgical(ctx)
    headers = _headers(ctx, auth_headers_for)
    response = client.post(
        f"/api/v1/clinical-orders/procedures/{order.id}/verify-consent",
        headers=headers,
    )
    assert response.status_code == 200
    response = client.post(
        f"/api/v1/clinical-orders/procedures/{order.id}/complete",
        headers=headers,
        json={"procedure_note": "Completed non-surgical bedside procedure"},
    )
    assert response.status_code == 200
    assert response.get_json()["order"]["status"] == "completed"


def test_non_surgical_cancel_http_preserved(ctx, client, auth_headers_for):
    order = _non_surgical(ctx)
    response = client.post(
        f"/api/v1/clinical-orders/procedures/{order.id}/cancel",
        headers=_headers(ctx, auth_headers_for),
        json={"reason": "Routine cancellation"},
    )
    assert response.status_code == 200
    assert response.get_json()["order"]["status"] == "cancelled"


def test_verified_internal_evidence_does_not_unlock_legacy_surgical_completion(ctx):
    ctx["admin"] = ctx["make_staff"](ctx["clinic"], role=Role.ADMIN)
    ctx["reviewer"] = ctx["make_staff"](ctx["clinic"], role=Role.DOCTOR)
    ctx["anesthetist"] = ctx["make_staff"](ctx["clinic"], role=Role.DOCTOR)
    order = _surgical(ctx, body_site="abdomen")
    ctx["order"] = order
    ctx["case"] = _plan(ctx["clinic"], ctx, order)
    _ready(ctx)
    assert _authorized(ctx).surgical_case_id == ctx["case"].id
    with pytest.raises(ConflictError, match="dedicated Surgery"):
        _complete(order, ctx)
    db.session.refresh(order)
    assert order.status == "active"


def test_existing_nurse_route_denial_is_preserved(ctx, client, auth_headers_for):
    order = _surgical(ctx)
    response = client.post(
        f"/api/v1/clinical-orders/procedures/{order.id}/complete",
        headers=auth_headers_for(ctx["nurse"].user, role=Role.NURSE),
        json={"procedure_note": "Not authorized"},
    )
    assert response.status_code == 403


def test_foreign_clinic_cannot_discover_surgical_order(
    ctx, make_clinic, make_staff
):
    order = _surgical(ctx)
    foreign = make_clinic(name="S03B Other Clinic")
    doctor = make_staff(foreign, role=Role.DOCTOR)
    with pytest.raises(NotFoundError):
        procedure.complete_procedure_order(
            order.id, clinic_id=foreign.id,
            actor_staff_id=doctor.id, procedure_note="Forbidden",
        )
