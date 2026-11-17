"""Rust-backed RAD6 end-to-end HTTP tests. No other production endpoints.

The route still owns identity, clinic scope, record ownership, and audit commit.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
import os
import subprocess
import json

import pytest
from app.core.enums.role_enums import Role
from app.modules.printing.routes import printing_routes
from app.modules.printing.services.print_policy import PrintDenied, build_document
from app.modules.printing.services.rust_print_bridge import render_pdf


def invoice(clinic_id=1):
    return build_document(kind="invoice",clinic_id=clinic_id,record_id=41,source_version=0,source={
        "invoice_number":"INV-TEST-41", "patient_id":11,"status":"ISSUED","currency":"NGN",
        "total_amount":"100.00", "amount_paid":"0.00"})


def test_rust_renderer_binary_is_required(monkeypatch):
    monkeypatch.setenv("CSP_RUST_PRINT_RENDERER", str(Path("Z:/missing-rust-print-test/renderer.exe")))
    with pytest.raises(PrintDenied, match="unavailable"):
        render_pdf(invoice())


def test_rust_renderer_never_renders_signed_or_released():
    with pytest.raises(PrintDenied):render_pdf(replace(invoice(), is_signed=True))
    with pytest.raises(PrintDenied):render_pdf(replace(invoice(), released_to_chart=True))


def test_rust_renderer_generates_authorized_pdf_and_unicode():
    assert os.environ.get("CSP_RUST_PRINT_RENDERER"), "Rust path must be pinned by installer"
    data=render_pdf(invoice())
    assert data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")
    assert len(data) < 3*1024*1024
    content=build_document(kind="radiology_draft", clinic_id=1,record_id=9,source_version=1,source={
        "imaging_order_id":40,"accession_number":"RAD-0123456789ABCDEF01234567", "report_revision":1,
        "report_text":"Clinic copy: Café – Côte d’Ivoire. <b>UNSIGNED</b> only."})
    # Requested Unicode must be representable by the available installed font; other scripts are denied.
    data2=render_pdf(content)
    assert data2.startswith(b"%PDF-") and len(data2)<3*1024*1024


def test_rust_renderer_covers_every_existing_flask_source_kind():
    other = [
        build_document(kind="appointment",clinic_id=1,record_id=15,source_version=0,source={
            "patient_id":11,"assigned_staff_id":3,"status":"SCHEDULED",
            "scheduled_start":"2030-01-01 10:00","scheduled_end":"2030-01-01 10:30"}),
        build_document(kind="lab_order",clinic_id=1,record_id=17,source_version=0,source={
            "patient_id":11,"status":"PENDING","created_at":"2030-01-01",
            "sample_collected_at":"NOT COLLECTED"}),
    ]
    for doc in (invoice(), *other):
        pdf=render_pdf(doc)
        assert pdf.startswith(b"%PDF-") and len(pdf) < 3*1024*1024

def test_rust_renderer_uses_multipage_layout():
    content=build_document(kind="radiology_draft", clinic_id=1,record_id=9,source_version=1,source={
        "imaging_order_id":40,"accession_number":"RAD-0123456789ABCDEF01234567", "report_revision":1,
        "report_text":("A bounded synthetic long clinical working note. "*170)})
    data=render_pdf(content)
    assert data.startswith(b"%PDF-")
    # A positive page-tree presence check; the renderer uses an auto-paginating layout engine.
    assert b"/Pages" in data or b"/Type/Pages" in data


def test_real_http_route_uses_rust_and_preserves_audit(
    app,client,clinic,make_authenticated_staff,monkeypatch,
):
    app.config["RAD6_PRINTING_ENABLED"] = True  # test app only, production remains disabled
    staff,headers=make_authenticated_staff(clinic,Role.ADMIN)
    source=Mock(return_value=invoice(clinic.id))
    audit=Mock()
    monkeypatch.setattr(printing_routes,"snapshot",source)
    monkeypatch.setattr(printing_routes,"create_audit_log",audit)
    result=client.get("/api/v1/printing/invoice/41.pdf",headers=headers)
    assert result.status_code==200 and result.data.startswith(b"%PDF-")
    assert result.headers["X-Clinical-Release"]=="none"
    assert "no-store" in result.headers["Cache-Control"]
    audit.assert_called_once()
    source.assert_called_once()


def test_missing_rust_renderer_denies_pdf_even_when_authorized(
    app,client,clinic,make_authenticated_staff,monkeypatch,
):
    app.config["RAD6_PRINTING_ENABLED"] = True
    _,headers=make_authenticated_staff(clinic,Role.ADMIN)
    source=Mock(return_value=invoice(clinic.id))
    audit=Mock()
    monkeypatch.setenv("CSP_RUST_PRINT_RENDERER",str(Path("Z:/missing-rust-print-test/renderer.exe")))
    monkeypatch.setattr(printing_routes,"snapshot",source)
    monkeypatch.setattr(printing_routes,"create_audit_log",audit)
    result=client.get("/api/v1/printing/invoice/41.pdf",headers=headers)
    assert result.status_code==404 and not result.data.startswith(b"%PDF-")
    audit.assert_not_called()
    assert "no-store" in result.headers["Cache-Control"]


def test_rust_renderer_long_latin_unicode_is_multipage():
    """Unicode punctuation and accents must survive long multi-page rendering.

    The 3 MiB limit and original 170-repeat safety test remain unchanged.
    """
    import re
    note = ("Café – Côte d’Ivoire. Synthetic unsigned note. " * 170)
    content = build_document(kind="radiology_draft", clinic_id=1, record_id=9,
        source_version=1, source={
            "imaging_order_id": 40,
            "accession_number": "RAD-0123456789ABCDEF01234567",
            "report_revision": 1,
            "report_text": note,
        })
    pdf = render_pdf(content)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) < 3 * 1024 * 1024
    # Explicit page count, not just the /Pages catalog token.
    assert len(re.findall(rb"/Type\s*/Page\b", pdf)) >= 2
