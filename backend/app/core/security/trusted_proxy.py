from __future__ import annotations

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix


def configure_trusted_proxy(app: Flask) -> None:
    raw_hops = app.config.get("TRUSTED_PROXY_HOPS", 0)

    if isinstance(raw_hops, bool):
        raise RuntimeError(
            "TRUSTED_PROXY_HOPS must be a non-negative integer"
        )

    if isinstance(raw_hops, int):
        hops = raw_hops
    elif isinstance(raw_hops, str) and raw_hops.strip().isdigit():
        hops = int(raw_hops.strip())
    else:
        raise RuntimeError(
            "TRUSTED_PROXY_HOPS must be a non-negative integer"
        )

    if hops < 0:
        raise RuntimeError(
            "TRUSTED_PROXY_HOPS must be a non-negative integer"
        )

    if hops == 0:
        return

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=hops,
        x_proto=hops,
    )
