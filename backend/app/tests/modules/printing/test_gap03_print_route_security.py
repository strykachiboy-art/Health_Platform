"""GAP-03: real Flask HTTP boundary for RAD6; no clinical release or PDF storage."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.core.enums.role_enums import Role
from app.modules.printing.routes import printing_routes
from app.modules.printing.services.print_policy import PrintDenied, build_document


PATH = "/api/v1/printing/invoice/41.pdf"
# Synthetic renderer output only for two HTTP authorization/audit tests.
# Native Rust rendering correctness has its own outstanding release gate.
_GAPB_HTTP_TEST_PDF = b"%PDF-1.4\n%GAPB-HTTP-BOUNDARY-TEST\n%%EOF\n"


def _invoice_copy(clinic_id: int):
    return build_document(
        kind="invoice",
        clinic_id=clinic_id,
        record_id=41,
        source_version=0,
        source={
            "invoice_number": "INV-SYNTH-41",
            "patient_id": 17,
            "status": "ISSUED",
            "currency": "NGN",
            "total_amount": "125.00",
            "amount_paid": "0.00",
        },
    )


def _authenticated_admin(clinic, make_authenticated_staff):
    staff, headers = make_authenticated_staff(clinic, Role.ADMIN)
    return staff, headers


def test_printing_is_disabled_by_default_before_source_access(
    app, client, clinic, make_authenticated_staff, monkeypatch
):
    _, headers = _authenticated_admin(clinic, make_authenticated_staff)
    assert app.config.get("RAD6_PRINTING_ENABLED", False) is False
    source = Mock(side_effect=AssertionError("source was reached while disabled"))
    monkeypatch.setattr(printing_routes, "snapshot", source)

    response = client.get(PATH, headers=headers)

    assert response.status_code == 404
    assert response.headers["Cache-Control"].startswith("private, no-store")
    source.assert_not_called()


@pytest.mark.parametrize(
    "url,additional_headers",
    [
        (PATH + "?clinic_id=999", {}),
        (PATH, {"Idempotency-Key": "no-replay"}),
        ("/api/v1/printing/unknown/41.pdf", {}),
    ],
)
def test_enabled_print_route_rejects_unsafe_requests_before_source(
    app, client, clinic, make_authenticated_staff, monkeypatch,
    url, additional_headers,
):
    app.config["RAD6_PRINTING_ENABLED"] = True
    _, headers = _authenticated_admin(clinic, make_authenticated_staff)
    source = Mock()
    monkeypatch.setattr(printing_routes, "snapshot", source)
    response = client.get(url, headers={**headers, **additional_headers})
    assert response.status_code == 404
    source.assert_not_called()


def test_enabled_print_route_denies_nurse_role_before_source(
    app, client, clinic, make_authenticated_staff, monkeypatch
):
    app.config["RAD6_PRINTING_ENABLED"] = True
    _, headers = make_authenticated_staff(clinic, Role.NURSE)
    source = Mock()
    monkeypatch.setattr(printing_routes, "snapshot", source)
    response = client.get(PATH, headers=headers)
    assert response.status_code in {401, 403, 404}
    source.assert_not_called()


def test_enabled_print_route_denies_foreign_clinic_context_before_source(
    app, client, clinic, make_clinic, make_authenticated_staff,
    auth_headers_for, monkeypatch
):
    app.config["RAD6_PRINTING_ENABLED"] = True
    staff, _ = _authenticated_admin(clinic, make_authenticated_staff)
    foreign_clinic = make_clinic()
    headers = auth_headers_for(
        staff.user, role=Role.ADMIN, clinic_context_id=foreign_clinic.id
    )
    source = Mock()
    monkeypatch.setattr(printing_routes, "snapshot", source)
    response = client.get(PATH, headers=headers)
    assert response.status_code in {401, 403, 404}
    source.assert_not_called()


def test_enabled_authorized_pdf_is_attachment_no_store_unsigned_and_audited(
    app, client, clinic, make_authenticated_staff, monkeypatch
):
    app.config["RAD6_PRINTING_ENABLED"] = True  # test app only
    staff, headers = _authenticated_admin(clinic, make_authenticated_staff)
    doc = _invoice_copy(clinic.id)
    source = Mock(return_value=doc)
    audit = Mock()
    monkeypatch.setattr(printing_routes, "snapshot", source)
    monkeypatch.setattr(printing_routes, "create_audit_log", audit)
    # HTTP/audit boundary: isolate external Rust executable availability.
    renderer = Mock(return_value=_GAPB_HTTP_TEST_PDF)
    monkeypatch.setattr(printing_routes, "render_pdf", renderer)

    response = client.get(PATH, headers=headers)

    assert response.status_code == 200
    assert response.data.startswith(b"%PDF-")
    assert response.mimetype == "application/pdf"
    assert "attachment" in response.headers["Content-Disposition"].lower()
    assert response.headers["X-Source-SHA256"] == doc.source_digest
    assert response.headers["X-Clinical-Release"] == "none"
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    source.assert_called_once_with(
        kind="invoice", record_id=41, clinic_id=clinic.id,
        actor_role="ADMIN", actor_staff_id=staff.id,
    )
    renderer.assert_called_once_with(doc)
    audit.assert_called_once()
    kwargs = audit.call_args.kwargs
    assert kwargs["clinic_id"] == clinic.id
    assert kwargs["user_id"] == staff.user_id
    assert kwargs["entity_id"] == 41
    assert kwargs["new_value"] == {
        "kind": "invoice", "source_version": 0,
        "source_digest": doc.source_digest, "unsigned": True,
    }
    assert "patient_id" not in str(kwargs["new_value"])


def test_enabled_denied_source_never_produces_pdf_or_audit(
    app, client, clinic, make_authenticated_staff, monkeypatch
):
    app.config["RAD6_PRINTING_ENABLED"] = True
    _, headers = _authenticated_admin(clinic, make_authenticated_staff)
    audit = Mock()
    monkeypatch.setattr(
        printing_routes, "snapshot", Mock(side_effect=PrintDenied("Print unavailable"))
    )
    monkeypatch.setattr(printing_routes, "create_audit_log", audit)
    response = client.get(PATH, headers=headers)
    assert response.status_code == 404
    assert not response.data.startswith(b"%PDF-")
    audit.assert_not_called()


def test_enabled_audit_failure_returns_500_without_pdf_delivery(
    app, client, clinic, make_authenticated_staff, monkeypatch
):
    app.config["RAD6_PRINTING_ENABLED"] = True
    _, headers = _authenticated_admin(clinic, make_authenticated_staff)
    doc = _invoice_copy(clinic.id)
    source = Mock(return_value=doc)
    audit = Mock(side_effect=RuntimeError("synthetic audit unavailable"))
    monkeypatch.setattr(printing_routes, "snapshot", source)
    monkeypatch.setattr(printing_routes, "create_audit_log", audit)
    renderer = Mock(return_value=_GAPB_HTTP_TEST_PDF)
    monkeypatch.setattr(printing_routes, "render_pdf", renderer)
    response = client.get(PATH, headers=headers)
    assert response.status_code == 500
    assert response.is_json
    assert response.get_json() == {
        "success": False,
        "error": "Internal server error",
    }
    assert response.mimetype != "application/pdf"
    assert not response.data.startswith(b"%PDF-")
    assert "no-store" in response.headers["Cache-Control"]
    source.assert_called_once()
    renderer.assert_called_once_with(doc)
    audit.assert_called_once()
