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


def test_l05r2_real_branded_invoice(app,client,clinic,make_authenticated_staff,make_patient,make_invoice,tmp_path,db):
    from hashlib import sha256
    from dataclasses import asdict
    from app.core.audit.models.audit_model import AuditLog
    from app.tests.modules.printing.test_branded_rendering import seed_logo,brand_meta,write_artifact
    from app.modules.printing.services.branding_payload import prepare_branding
    from app.modules.printing.services.source_adapters import snapshot
    staff,headers=make_authenticated_staff(clinic,Role.ADMIN)
    invoice=make_invoice(clinic,make_patient(clinic),invoice_number='L05-R2-SYNTHETIC-INVOICE',currency='NGN')
    db.session.commit();seed_logo(clinic)
    app.config.update(RAD6_PRINTING_ENABLED=True,PRINT_BRANDING_STAGED_ENABLED=True)
    doc=snapshot(kind='invoice',record_id=invoice.id,clinic_id=clinic.id,actor_role='ADMIN',actor_staff_id=staff.id)
    brand=prepare_branding(doc,owning_clinic_id=clinic.id)
    path=f'/api/v1/printing/invoice/{invoice.id}.pdf'
    r=client.get(path,headers=headers)
    assert r.status_code==200 and r.headers['X-Source-SHA256']==doc.source_digest and 'no-store' in r.headers['Cache-Control']
    audit=db.session.query(AuditLog).filter_by(entity_type='PrintedDocument',entity_id=invoice.id,user_id=staff.user_id).one().new_value
    assert audit['branding_sha256']==brand.branding_sha256 and audit['pdf_sha256']==sha256(r.data).hexdigest() and audit['source_digest']==doc.source_digest
    write_artifact(tmp_path,'l05r2_real_invoice',r.data,{'doc':asdict(doc),'branding':brand_meta(brand),'audit':audit})
    assert client.get(path+'?clinic_id=999',headers=headers).status_code==404
    assert db.session.query(AuditLog).filter_by(entity_type='PrintedDocument',entity_id=invoice.id,user_id=staff.user_id).count()==1


def test_l05r2b_real_itemized_invoice(app,client,clinic,make_authenticated_staff,make_patient,make_invoice,tmp_path,db):
    from decimal import Decimal
    from hashlib import sha256
    from dataclasses import asdict
    import json
    from app.core.audit.models.audit_model import AuditLog
    from app.modules.billing.models.billing_model import InvoiceItem
    from app.modules.clinic.models.branding_model import OrganizationBranding
    from app.modules.clinic.services.branding_service import resolve_branding
    from app.tests.modules.printing.test_branded_rendering import seed_logo,brand_meta,write_artifact
    from app.modules.printing.services.branding_payload import prepare_branding
    from app.modules.printing.services.source_adapters import snapshot
    staff,headers=make_authenticated_staff(clinic,Role.ADMIN)
    invoice=make_invoice(clinic,make_patient(clinic),invoice_number='POLISH-REAL-SYNTHETIC',currency='NGN',total_amount=Decimal('15000.00'),amount_paid=Decimal('2500.00'))
    db.session.add_all([InvoiceItem(invoice_id=invoice.id,description='Synthetic consultation',quantity=1,unit_price=Decimal('5000.00'),subtotal=Decimal('5000.00')),InvoiceItem(invoice_id=invoice.id,description='Synthetic tests',quantity=2,unit_price=Decimal('5000.00'),subtotal=Decimal('10000.00'))]);db.session.commit()
    seed_logo(clinic)
    row=db.session.query(OrganizationBranding).filter_by(organization_id=clinic.organization_id).one();row.primary_color='#214E91';row.secondary_color='#283548';row.version+=1;db.session.commit()
    app.config.update(RAD6_PRINTING_ENABLED=True,PRINT_BRANDING_STAGED_ENABLED=True)
    d=snapshot(kind='invoice',record_id=invoice.id,clinic_id=clinic.id,actor_role='ADMIN',actor_staff_id=staff.id)
    f=dict(d.fields);assert json.loads(f['Invoice Items'])==[['Synthetic consultation','1','5000.00','5000.00'],['Synthetic tests','2','5000.00','10000.00']]
    assert f['Outstanding Balance']=='12500.00'
    brand=prepare_branding(d,owning_clinic_id=clinic.id);snap=resolve_branding(owning_clinic_id=clinic.id)
    path=f'/api/v1/printing/invoice/{invoice.id}.pdf';r=client.get(path,headers=headers)
    assert r.status_code==200 and r.headers['X-Source-SHA256']==d.source_digest and 'no-store' in r.headers['Cache-Control']
    a=db.session.query(AuditLog).filter_by(entity_type='PrintedDocument',entity_id=invoice.id,user_id=staff.user_id).one().new_value
    assert a['pdf_sha256']==sha256(r.data).hexdigest() and a['branding_sha256']==brand.branding_sha256
    bm=brand_meta(brand);bm.update(primary_color=brand.primary_color,secondary_color=brand.secondary_color,metadata=dict(snap.metadata))
    write_artifact(tmp_path,'l05r2b_real_invoice',r.data,{'doc':asdict(d),'branding':bm,'audit':a})
    # Invalid billing total must not silently render an apparently reconciled invoice.
    invoice.total_amount=Decimal('16000.00');db.session.commit()
    assert client.get(path,headers=headers).status_code==404
    assert db.session.query(AuditLog).filter_by(entity_type='PrintedDocument',entity_id=invoice.id,user_id=staff.user_id).count()==1
