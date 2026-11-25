"""Fail-closed Rust PDF renderer bridge for authorized RAD6 PrintDocument snapshots.

This module does NOT resolve users, roles, clinic IDs, source records, or audit state.
Those responsibilities remain in printing_routes / source_adapters / print_policy.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from .print_policy import PrintDenied, PrintDocument

_MAX_INPUT = 65_536
_MAX_PDF = 3 * 1024 * 1024


def _executable() -> Path:
    configured = os.environ.get("CSP_RUST_PRINT_RENDERER")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            raise PrintDenied("Print renderer unavailable")
    elif os.name == "nt":
        backend = Path(__file__).resolve().parents[4]
        path = backend / "rust" / "clinical_print_renderer" / "target" / "release" / "clinical_print_renderer.exe"
    else:
        path = Path("/usr/local/bin/clinical_print_renderer")
    if not path.is_file():
        raise PrintDenied("Print renderer unavailable")
    return path


def render_pdf(doc: PrintDocument) -> bytes:
    if not isinstance(doc, PrintDocument) or doc.is_signed or doc.released_to_chart:
        raise PrintDenied("Unsigned print policy required")
    payload = {
        "protocol": 1,
        "kind": doc.kind,
        "clinic_id": doc.clinic_id,
        "record_id": doc.record_id,
        "source_version": doc.source_version,
        "title": doc.title,
        "watermark": doc.watermark,
        "source_digest": doc.source_digest,
        "is_signed": doc.is_signed,
        "released_to_chart": doc.released_to_chart,
        "fields": [list(field) for field in doc.fields],
    }
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _MAX_INPUT:
            raise PrintDenied("Print renderer unavailable")
        result = subprocess.run(
            [str(_executable())],
            input=encoded,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=12,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise PrintDenied("Print renderer unavailable") from exc
    if (result.returncode != 0 or not result.stdout.startswith(b"%PDF-")
            or len(result.stdout) > _MAX_PDF):
        raise PrintDenied("Print renderer unavailable")
    return result.stdout
