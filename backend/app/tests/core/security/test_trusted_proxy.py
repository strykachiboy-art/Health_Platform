from pathlib import Path

import pytest
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from app.core.security.trusted_proxy import configure_trusted_proxy


REPO_ROOT = Path(__file__).resolve().parents[5]


def make_test_app(trusted_proxy_hops):
    app = Flask(__name__)
    app.config["TRUSTED_PROXY_HOPS"] = trusted_proxy_hops

    @app.get("/request-context")
    def request_context():
        return jsonify(
            remote_addr=request.remote_addr,
            is_secure=request.is_secure,
        )

    configure_trusted_proxy(app)
    return app


def test_configured_proxy_supplies_client_ip_and_https_scheme():
    app = make_test_app(1)

    response = app.test_client().get(
        "/request-context",
        headers={
            "X-Forwarded-For": "198.51.100.24",
            "X-Forwarded-Proto": "https",
        },
        environ_base={"REMOTE_ADDR": "172.20.0.10"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "remote_addr": "198.51.100.24",
        "is_secure": True,
    }


def test_forwarded_headers_are_ignored_by_default():
    app = make_test_app(0)

    response = app.test_client().get(
        "/request-context",
        headers={
            "X-Forwarded-For": "198.51.100.24",
            "X-Forwarded-Proto": "https",
        },
        environ_base={"REMOTE_ADDR": "172.20.0.10"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "remote_addr": "172.20.0.10",
        "is_secure": False,
    }


@pytest.mark.parametrize(
    "invalid_hops",
    ["invalid", "-1", -1, True, 1.5, None],
)
def test_invalid_proxy_hop_setting_fails_closed(invalid_hops):
    app = Flask(__name__)
    app.config["TRUSTED_PROXY_HOPS"] = invalid_hops

    with pytest.raises(RuntimeError, match="TRUSTED_PROXY_HOPS"):
        configure_trusted_proxy(app)


def test_phase6_enables_trust_only_for_the_backend_service():
    compose = (
        REPO_ROOT / "compose.phase6.yaml"
    ).read_text(encoding="utf-8")

    backend = compose.split("  backend:\n", 1)[1].split(
        "\n  worker:\n", 1
    )[0]

    assert 'TRUSTED_PROXY_HOPS: "1"' in backend
    assert compose.count('TRUSTED_PROXY_HOPS: "1"') == 1

def make_rate_limited_app(trusted_proxy_hops):
    app = Flask(__name__)
    app.config.update(
        TRUSTED_PROXY_HOPS=trusted_proxy_hops,
        RATELIMIT_ENABLED=True,
        RATELIMIT_STORAGE_URI="memory://",
    )

    configure_trusted_proxy(app)

    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=["1 per minute"],
        storage_uri="memory://",
    )
    limiter.init_app(app)

    @app.get("/limited")
    def limited():
        return jsonify(remote_addr=request.remote_addr)

    return app


def _request_as_forwarded_client(client, forwarded_ip):
    return client.get(
        "/limited",
        headers={"X-Forwarded-For": forwarded_ip},
        environ_base={"REMOTE_ADDR": "172.20.0.10"},
    )


def test_rate_limit_buckets_use_client_ip_with_trusted_proxy():
    app = make_rate_limited_app(1)
    client = app.test_client()

    first_client_request = _request_as_forwarded_client(
        client,
        "198.51.100.24",
    )
    repeated_client_request = _request_as_forwarded_client(
        client,
        "198.51.100.24",
    )
    second_client_request = _request_as_forwarded_client(
        client,
        "198.51.100.25",
    )

    assert first_client_request.status_code == 200
    assert first_client_request.get_json()["remote_addr"] == "198.51.100.24"
    assert repeated_client_request.status_code == 429
    assert second_client_request.status_code == 200
    assert second_client_request.get_json()["remote_addr"] == "198.51.100.25"


def test_rate_limit_clients_share_proxy_ip_when_trust_is_disabled():
    app = make_rate_limited_app(0)
    client = app.test_client()

    first_client_request = _request_as_forwarded_client(
        client,
        "198.51.100.24",
    )
    second_client_request = _request_as_forwarded_client(
        client,
        "198.51.100.25",
    )

    assert first_client_request.status_code == 200
    assert first_client_request.get_json()["remote_addr"] == "172.20.0.10"
    assert second_client_request.status_code == 429
